<#
.SYNOPSIS
    Export raw news articles alongside their cleansing classification to a JSON file for
    manual LLM verification of event-group correctness.

.DESCRIPTION
    Reads credentials from infra/.env, queries Postgres via docker exec, and writes a JSON
    file grouped by cluster. Each cluster entry contains only what an LLM needs to judge
    whether the classification is correct:

      - cluster_id             : key to reference a cluster in your LLM reply
      - classified_event_type  : the event group the Cleansing service assigned
      - articles[]             : article_id (key), title, and body excerpt (500 chars)

.PARAMETER Date
    The day to export, formatted yyyy-MM-dd (filtered on ingestion.articles.ingested_at UTC).

.PARAMETER OutputPath
    Optional output file path. Defaults to scripts\cleansing-audit-<Date>.json.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\export-cleansing-audit.ps1 -Date 2026-08-12
    powershell -ExecutionPolicy Bypass -File scripts\export-cleansing-audit.ps1 -Date 2026-08-12 -OutputPath C:\tmp\audit.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, HelpMessage = "Day to export, formatted yyyy-MM-dd")]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Date,

    [Parameter(Mandatory = $false)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

# See export-prediction-audit.ps1: PowerShell decodes a native command's stdout with the console OEM
# code page, which double-encodes psql's UTF-8 output. Force UTF-8 so article text matches the database.
$previousOutputEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# --- Load credentials from infra/.env -------------------------------------------------------
$envPath = Join-Path $repoRoot "infra\.env"
if (-not (Test-Path $envPath)) {
    throw "Cannot find infra/.env at $envPath. Run from the repo and ensure the stack is configured."
}

$envVars = @{}
foreach ($line in Get-Content $envPath) {
    $trimmed = $line.Trim()
    if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
    $idx = $trimmed.IndexOf("=")
    if ($idx -lt 1) { continue }
    $key = $trimmed.Substring(0, $idx).Trim()
    $val = $trimmed.Substring($idx + 1).Trim()
    if ($val.StartsWith('"') -and $val.EndsWith('"')) { $val = $val.Substring(1, $val.Length - 2) }
    $envVars[$key] = $val
}

$pgUser = if ($envVars.ContainsKey("POSTGRES_USER")) { $envVars["POSTGRES_USER"] } else { "feed_user" }
$pgPass = $envVars["POSTGRES_PASSWORD"]
$pgDb   = if ($envVars.ContainsKey("POSTGRES_DB"))   { $envVars["POSTGRES_DB"]   } else { "feed" }

if (-not $pgPass) { throw "POSTGRES_PASSWORD is not set in infra/.env" }

# --- Helper ---------------------------------------------------------------------------------
function Invoke-PsqlCsv {
    param([string]$Sql)
    $csv = & docker exec -e "PGPASSWORD=$pgPass" feed-postgres `
        psql -U $pgUser -d $pgDb -P pager=off --csv -c $Sql 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "psql query failed (is 'feed-postgres' running?):`n$csv"
    }
    if (-not $csv -or @($csv).Count -eq 0) { return @() }
    return @($csv | ConvertFrom-Csv)
}

# --- Read canonical event types from the single source of truth ----------------------------
# Parses the EventType StrEnum in shared/schemas/messages.py so this script never drifts.
$messagesPath = Join-Path $repoRoot "src\shared\shared\schemas\messages.py"
if (-not (Test-Path $messagesPath)) {
    throw "Cannot find $messagesPath. Run from the repo root."
}
$inEventType = $false
$allEventTypes = [System.Collections.Generic.List[string]]::new()
foreach ($line in Get-Content $messagesPath) {
    if ($line -match 'class EventType\s*\(') { $inEventType = $true; continue }
    if ($inEventType) {
        if ($line -match '^\s*class\s+') { break }           # next class — stop
        if ($line -match '^\s*([A-Z_]+)\s*=\s*"([A-Z_]+)"') {
            $allEventTypes.Add($Matches[2])
        }
    }
}
if ($allEventTypes.Count -eq 0) {
    throw "No EventType values found in $messagesPath. Check the file structure."
}

# --- Query ----------------------------------------------------------------------------------
Write-Host "Feed Analyzer - building cleansing audit for $Date ..." -ForegroundColor Green

$rows = Invoke-PsqlCsv @"
SELECT
    ec.cluster_id,
    ec.event_type                                              AS classified_event_type,
    ia.article_id,
    left(ia.title, 200)                                        AS title,
    left(regexp_replace(ia.body, E'[\\n\\r]+', ' ', 'g'), 500) AS body
FROM ingestion.articles ia
JOIN cleansing.cluster_articles ca ON ca.article_id = ia.article_id
JOIN cleansing.event_clusters   ec ON ec.cluster_id = ca.cluster_id
WHERE ia.ingested_at::date = '$Date'::date
ORDER BY ec.cluster_id, ia.published_at;
"@

# --- Handle empty result --------------------------------------------------------------------
$out = if ($OutputPath) { $OutputPath } else { Join-Path $PSScriptRoot "cleansing-audit-$Date.json" }

if (-not $rows -or @($rows).Count -eq 0) {
    Write-Host "No classified articles found for $Date." -ForegroundColor Yellow
    @{ date = $Date; cluster_count = 0; event_types_used = @(); clusters = @() } |
        ConvertTo-Json -Depth 5 | Out-File -FilePath $out -Encoding utf8
    Write-Host "Written to: $out" -ForegroundColor Green
    return
}

# --- Group by cluster_id --------------------------------------------------------------------
$clusterMap = [ordered]@{}

foreach ($row in $rows) {
    $cid = $row.cluster_id
    if (-not $clusterMap.Contains($cid)) {
        $clusterMap[$cid] = [ordered]@{
            cluster_id            = $cid
            classified_event_type = $row.classified_event_type
            articles              = [System.Collections.Generic.List[object]]::new()
        }
    }
    $clusterMap[$cid].articles.Add([ordered]@{
        article_id = $row.article_id
        title      = $row.title
        body       = $row.body
    })
}

# --- Collect distinct event types -----------------------------------------------------------
$eventTypes = $allEventTypes

# --- Write output ---------------------------------------------------------------------------
$clusters = @($clusterMap.Values)

[ordered]@{
    date             = $Date
    llm_instructions = "The system classified each news cluster into one of the event_types_used below. For each cluster, check if the classified_event_type matches the article titles. Reply per cluster: cluster_id | classified_event_type | correct/incorrect | reason."
    event_types_used = $eventTypes
    cluster_count    = $clusters.Count
    clusters         = $clusters
} | ConvertTo-Json -Depth 6 | Out-File -FilePath $out -Encoding utf8

[Console]::OutputEncoding = $previousOutputEncoding

Write-Host "Written to: $out" -ForegroundColor Green
Write-Host "  Event types: $($eventTypes.Count) distinct"
Write-Host "  Clusters:    $($clusters.Count)"
Write-Host "  Articles:    $(@($rows).Count)"
