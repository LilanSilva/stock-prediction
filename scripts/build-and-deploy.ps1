<#
.SYNOPSIS
    Build and redeploy the application services only. The data stores and the causal graph are
    checked, never touched.

.DESCRIPTION
    For a stack that is already running: rebuilds the seven service images and recreates only
    those containers. Postgres, Neo4j and RabbitMQ keep running throughout, with their data and
    every weight Credibility has learned intact.

    Why this is not `up -d --build`: feed-prediction and feed-credibility declare
    `depends_on: feed-neo4j-seed`, so a plain `up` re-runs the graph seed container. The seed's
    `MERGE` statements are idempotent, but 05-seed-conditioned-edges.cypher *deletes* the
    unconditional edges its conditioned edges supersede - a re-seed cannot restore anything an
    earlier version of those files removed. This script therefore deploys with `--no-deps`, so
    Compose starts nothing but the services named.

    Steps:
      1. Verify Docker and Compose v2 are available and the daemon is reachable.
      2. Verify the env file exists, then validate the rendered compose config with
         `docker compose config --quiet` - this catches a variable the compose file declares
         required (POSTGRES_PASSWORD, NEO4J_AUTH, RABBITMQ_DEFAULT_PASS, ...) before any build.
      3. Read-only check that feed-postgres, feed-neo4j and feed-rabbitmq are running and
         healthy, and report how the graph seed last completed. Nothing is started or seeded.
      4. Build the service images.
      5. Recreate the service containers with `up -d --no-deps`.
      6. Wait for each service healthcheck to report `healthy`, then print the stack status and
         the tail of the logs of anything that failed.

    Uses the same compose file and env file as the documented command, so it shares the same
    Compose project ('infra') and volumes - it does not create a parallel stack.

    Secrets are read only by Docker from the env file; nothing from it is ever echoed.

    Bringing the data stores up in the first place is out of scope by design - that is
    `docker compose --env-file infra/.env -f infra/docker-compose.yml up -d`, documented in
    infra/README.md.

.PARAMETER Service
    Deploy only these services. Must be application services; omit for all seven. Dependencies
    are never started, whatever this is set to.

.PARAMETER EnvFile
    Path to the environment file. Defaults to infra/.env.

.PARAMETER NoBuild
    Skip the build and redeploy the images already present.

.PARAMETER PythonBaseImage
    Optional Python 3.12 base image with local CA certificates installed, for HTTPS-scanning networks.

.PARAMETER NoCache
    Build without the layer cache.

.PARAMETER Pull
    Always attempt to pull a newer version of each base image while building.

.PARAMETER Recreate
    Force-recreate the service containers even when their config and image are unchanged.

.PARAMETER RemoveFirst
    Stop and remove the targeted service containers before deploying. Data stores are untouched;
    no volume is ever removed.

.PARAMETER SkipInfraCheck
    Skip step 3. Only useful if you know the data stores are reachable but not reporting healthy.

.PARAMETER SkipHealthCheck
    Deploy and return immediately, without waiting for the service healthchecks.

.PARAMETER TimeoutSeconds
    How long to wait for every deployed service to become healthy. Default 300.

.PARAMETER PlainProgress
    Use `--progress plain` for build output - full, non-collapsing logs, better for CI or for
    diagnosing a failing build step.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-and-deploy.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-and-deploy.ps1 -Service feed-prediction,feed-notification

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-and-deploy.ps1 -NoCache -Pull -PlainProgress
#>
[CmdletBinding()]
param(
    [string[]]$Service,
    [string]$EnvFile,
    [switch]$NoBuild,
    [string]$PythonBaseImage,
    [switch]$NoCache,
    [switch]$Pull,
    [switch]$Recreate,
    [switch]$RemoveFirst,
    [switch]$SkipInfraCheck,
    [switch]$SkipHealthCheck,
    [int]$TimeoutSeconds = 300,
    [switch]$PlainProgress
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

$composeFile = Join-Path $repoRoot "infra\docker-compose.yml"
if (-not (Test-Path $composeFile)) {
    throw "Compose file not found at $composeFile"
}

if (-not $EnvFile) {
    $EnvFile = Join-Path $repoRoot "infra\.env"
}
if (-not (Test-Path $EnvFile)) {
    throw @"
Environment file not found at $EnvFile.
Create it from the template, then fill in local values:
    copy infra\.env.example infra\.env
Every secret lives in infra\.env only (git-ignored) - see infra\README.md.
"@
}
$EnvFile = (Resolve-Path $EnvFile).Path

# The infrastructure this script deploys *onto*. Everything else in the compose file is an
# application service and is deployed. Adding a new service to compose picks it up automatically;
# adding a new data store means adding it here.
$dataStores = @("feed-postgres", "feed-neo4j", "feed-rabbitmq")
$seedService = "feed-neo4j-seed"
$infraServices = $dataStores + $seedService

# --- Helpers ---------------------------------------------------------------------------------

# Run docker with its output going straight to the console (keeps the live build progress).
# The exit code is published in $script:DockerExit rather than returned, so docker's own output
# stays the only thing on the function's output stream. $ErrorActionPreference = "Stop" does not
# trip on a non-zero native exit code, so callers must check $script:DockerExit themselves.
$script:DockerExit = 0
function Invoke-Docker {
    param([Parameter(Mandatory)][string[]]$Arguments)

    & docker @Arguments
    $script:DockerExit = $LASTEXITCODE
}

# Run docker and capture its output. stderr is merged in, which PowerShell 5.1 turns into a
# terminating NativeCommandError while $ErrorActionPreference is "Stop" - hence the local reset.
function Invoke-DockerCapture {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & docker @Arguments 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    return [pscustomobject]@{
        ExitCode = $code
        Lines    = @($output | ForEach-Object { "$_" })
    }
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

# State of every container in the Compose project, keyed by compose service name. Read from the
# service label rather than the container name, so it does not rely on the two matching.
function Get-StackState {
    param([Parameter(Mandatory)][string[]]$ComposeArgs)

    $ids = Invoke-DockerCapture ($ComposeArgs + @("ps", "--all", "--quiet"))
    if ($ids.ExitCode -ne 0) {
        throw "Could not list stack containers: $($ids.Lines -join ' ')"
    }
    $ids = @($ids.Lines | Where-Object { $_ -match '^[0-9a-f]{12,}$' })

    $state = @{}
    if (-not $ids) { return $state }

    # The label key is a Go backquoted raw string, not a double-quoted one: PowerShell strips
    # embedded double quotes when it builds a native command line, so `index .Config.Labels
    # "com..."` reaches docker unquoted and fails with `function "com" not defined`.
    $template = '{{index .Config.Labels `com.docker.compose.service`}}|{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.State.ExitCode}}'
    $inspect = Invoke-DockerCapture (@("inspect", "--format", $template) + $ids)
    if ($inspect.ExitCode -ne 0) {
        throw "docker inspect failed: $($inspect.Lines -join ' ')"
    }

    foreach ($line in $inspect.Lines) {
        if ($line -notmatch '\|') { continue }
        $parts = $line.Split('|')
        $state[$parts[0]] = [pscustomobject]@{
            Service  = $parts[0]
            Name     = $parts[1].TrimStart('/')
            Status   = $parts[2]
            Health   = $parts[3]
            ExitCode = [int]$parts[4]
        }
    }
    return $state
}

# A service container is settled once it can no longer change state on its own.
function Test-Settled {
    param([pscustomobject]$Container)

    if ($Container.Health -eq "none") { return $Container.Status -ne "created" }
    return $Container.Health -in @("healthy", "unhealthy")
}

function Test-Healthy {
    param([pscustomobject]$Container)

    if ($Container.Status -ne "running") { return $false }
    if ($Container.Health -eq "none") { return $true }
    return $Container.Health -eq "healthy"
}

# --- 1. Docker availability ------------------------------------------------------------------

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker was not found on PATH. Install Docker Desktop and re-run this script."
}

$version = Invoke-DockerCapture @("compose", "version", "--short")
if ($version.ExitCode -ne 0) {
    throw "Docker Compose v2 is unavailable ('docker compose version' failed). Output: $($version.Lines -join ' ')"
}

$info = Invoke-DockerCapture @("info", "--format", "{{.ServerVersion}}")
if ($info.ExitCode -ne 0) {
    throw "The Docker daemon is not reachable. Start Docker Desktop and re-run this script."
}

Write-Host "Repository root : $repoRoot"
Write-Host "Compose file    : $composeFile"
Write-Host "Env file        : $EnvFile"
Write-Host "Docker engine   : $($info.Lines -join '') (compose $($version.Lines -join ''))"

$compose = @("compose", "--env-file", $EnvFile, "-f", $composeFile)
if ($PlainProgress) { $compose += @("--progress", "plain") }

# --- 2. Validate the rendered config ---------------------------------------------------------

Write-Step "Validating compose configuration"

$config = Invoke-DockerCapture ($compose + @("config", "--quiet"))
if ($config.ExitCode -ne 0) {
    Write-Host ($config.Lines -join [Environment]::NewLine) -ForegroundColor Red
    throw "Compose configuration is invalid. A required variable is probably missing from $EnvFile (compare against infra\.env.example)."
}

$allServices = Invoke-DockerCapture ($compose + @("config", "--services"))
if ($allServices.ExitCode -ne 0) {
    throw "Could not list compose services: $($allServices.Lines -join ' ')"
}
$allServices = @($allServices.Lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })

$appServices = @($allServices | Where-Object { $infraServices -notcontains $_ })
if (-not $appServices) {
    throw "No application services found in $composeFile."
}

if ($Service) {
    $infraNamed = @($Service | Where-Object { $infraServices -contains $_ })
    if ($infraNamed) {
        throw @"
$($infraNamed -join ', ') is infrastructure; this script deploys application services only and
never starts, recreates or seeds a data store. Bring those up with:
    docker compose --env-file infra\.env -f infra\docker-compose.yml up -d $($infraNamed -join ' ')
"@
    }
    $unknown = @($Service | Where-Object { $appServices -notcontains $_ })
    if ($unknown) {
        throw "Unknown service(s): $($unknown -join ', '). Available: $($appServices -join ', ')"
    }
    $targets = @($Service)
}
else {
    $targets = @($appServices)
}

Write-Host "Configuration valid." -ForegroundColor Green
Write-Host "Deploying       : $($targets -join ', ')"
Write-Host "Left untouched  : $($infraServices -join ', ')"

# --- 3. Check the infrastructure (read-only) --------------------------------------------------

if ($SkipInfraCheck) {
    Write-Step "Skipping the infrastructure check (-SkipInfraCheck)"
}
else {
    Write-Step "Checking data stores and the causal graph (read-only)"

    $state = Get-StackState -ComposeArgs $compose
    $problems = @()

    foreach ($svc in $dataStores) {
        $container = $state[$svc]
        if (-not $container) {
            $problems += "$svc has no container"
            Write-Host ("  [fail] {0,-22} not deployed" -f $svc) -ForegroundColor Red
            continue
        }
        if (Test-Healthy -Container $container) {
            $detail = if ($container.Health -eq "none") { "running" } else { "running, healthy" }
            Write-Host ("  [ ok ] {0,-22} {1}" -f $svc, $detail) -ForegroundColor Green
        }
        else {
            $problems += "$svc is $($container.Status)/$($container.Health)"
            Write-Host ("  [fail] {0,-22} {1}/{2}" -f $svc, $container.Status, $container.Health) -ForegroundColor Red
        }
    }

    if ($problems) {
        throw @"
The data stores this deploy needs are not ready: $($problems -join '; ').
This script does not start them. Bring the infrastructure up, wait for it to be healthy, then
re-run:
    docker compose --env-file infra\.env -f infra\docker-compose.yml up -d $($dataStores -join ' ')
"@
    }

    # Informational only. The graph is never re-seeded here: 05-seed-conditioned-edges.cypher
    # deletes the unconditional edges it supersedes, so a re-seed is not a pure no-op.
    $seed = $state[$seedService]
    if (-not $seed) {
        $seedWarning = "$seedService has never run, so the causal graph may be unseeded."
    }
    elseif ($seed.Status -eq "running") {
        $seedWarning = "$seedService is still running; the graph seed has not finished."
    }
    elseif ($seed.ExitCode -ne 0) {
        $seedWarning = "$seedService last exited $($seed.ExitCode) - 09-verify-seed.cypher's assertions did not pass."
    }
    else {
        $seedWarning = $null
        Write-Host ("  [ ok ] {0,-22} last run exited 0" -f $seedService) -ForegroundColor Green
    }

    if ($seedWarning) {
        Write-Host ("  [warn] {0,-22} {1}" -f $seedService, $seedWarning) -ForegroundColor Yellow
        Write-Host "         Deploy continues - but feed-prediction and feed-credibility read that graph," -ForegroundColor Yellow
        Write-Host "         so treat their output as untrustworthy until it is resolved. See infra\README.md." -ForegroundColor Yellow
    }
}

# --- 4. Build ---------------------------------------------------------------------------------

if ($NoBuild) {
    Write-Step "Skipping build (-NoBuild)"
}
else {
    Write-Step "Building service images"

    $buildArgs = @("build")
    if ($PythonBaseImage) { $buildArgs += @("--build-arg", "PYTHON_BASE_IMAGE=$PythonBaseImage") }
    if ($NoCache) { $buildArgs += "--no-cache" }
    if ($Pull) { $buildArgs += "--pull" }
    $buildArgs += $targets

    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    Invoke-Docker ($compose + $buildArgs)
    $stopwatch.Stop()
    if ($script:DockerExit -ne 0) {
        throw "Build failed with exit code $script:DockerExit."
    }
    Write-Host ("Build completed in {0:mm\:ss}." -f $stopwatch.Elapsed) -ForegroundColor Green
}

# --- 5. Deploy --------------------------------------------------------------------------------

if ($RemoveFirst) {
    Write-Step "Removing the targeted service containers"
    Invoke-Docker ($compose + @("rm", "--stop", "--force") + $targets)
    if ($script:DockerExit -ne 0) {
        throw "docker compose rm failed with exit code $script:DockerExit."
    }
}

Write-Step "Deploying services (--no-deps: nothing else is started)"

# --no-deps is what keeps this off the data stores and the seed container. Without it, Compose
# would evaluate feed-prediction's and feed-credibility's depends_on and re-run the graph seed.
# --remove-orphans is deliberately absent: this is a partial up by design.
$upArgs = @("up", "-d", "--no-deps")
if ($Recreate) { $upArgs += "--force-recreate" }
$upArgs += $targets

Invoke-Docker ($compose + $upArgs)
if ($script:DockerExit -ne 0) {
    throw "docker compose up failed with exit code $script:DockerExit."
}

# --- 6. Wait for health -----------------------------------------------------------------------

if ($SkipHealthCheck) {
    Write-Step "Skipping the health wait (-SkipHealthCheck)"
    Invoke-Docker ($compose + @("ps") + $targets)
    Write-Host ""
    Write-Host "Services deployed. Health not verified." -ForegroundColor Yellow
    exit 0
}

Write-Step "Waiting for the deployed services to become healthy (timeout ${TimeoutSeconds}s)"

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$reported = @{}
$deployed = @()

while ($true) {
    $state = Get-StackState -ComposeArgs $compose
    $deployed = @($targets | ForEach-Object {
        if ($state[$_]) { $state[$_] }
        else {
            [pscustomobject]@{ Service = $_; Name = $_; Status = "missing"; Health = "none"; ExitCode = -1 }
        }
    })

    # Report each service once, as soon as it settles, so a slow deploy still shows progress.
    foreach ($c in $deployed) {
        if ($reported.ContainsKey($c.Service)) { continue }
        if (($c.Status -ne "missing") -and -not (Test-Settled -Container $c)) { continue }

        $reported[$c.Service] = $true
        if (Test-Healthy -Container $c) {
            $detail = if ($c.Health -eq "none") { "running" } else { "healthy" }
            Write-Host ("  [ ok ] {0,-22} {1}" -f $c.Service, $detail) -ForegroundColor Green
        }
        else {
            Write-Host ("  [fail] {0,-22} {1}/{2}" -f $c.Service, $c.Status, $c.Health) -ForegroundColor Red
        }
    }

    $pending = @($deployed | Where-Object { $_.Status -ne "missing" -and -not (Test-Settled -Container $_) })
    if (-not $pending) { break }

    if ((Get-Date) -ge $deadline) {
        Write-Host ""
        foreach ($c in $pending) {
            Write-Host ("  [wait] {0,-22} {1}/{2} - timed out" -f $c.Service, $c.Status, $c.Health) -ForegroundColor Yellow
        }
        break
    }

    Start-Sleep -Seconds 3
}

# --- 7. Report --------------------------------------------------------------------------------

Write-Step "Deployed service status"
Invoke-Docker ($compose + @("ps") + $targets)

$failed = @($deployed | Where-Object { -not (Test-Healthy -Container $_) })

if ($failed) {
    foreach ($c in $failed) {
        if ($c.Status -eq "missing") { continue }
        Write-Host ""
        Write-Host "--- last 40 log lines: $($c.Service) ---" -ForegroundColor Yellow
        Invoke-Docker ($compose + @("logs", "--tail", "40", $c.Service))
    }

    Write-Host ""
    Write-Host "Deploy incomplete - not healthy: $(($failed | ForEach-Object { $_.Service }) -join ', ')" -ForegroundColor Red
    Write-Host "The data stores and the causal graph were not modified." -ForegroundColor Gray
    exit 1
}

Write-Host ""
Write-Host "$($targets.Count) service(s) deployed and healthy." -ForegroundColor Green
if ($seedWarning) {
    Write-Host "Outstanding warning: $seedWarning" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Follow logs with: docker compose --env-file infra\.env -f infra\docker-compose.yml logs -f" -ForegroundColor Gray
