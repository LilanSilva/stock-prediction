<#
.SYNOPSIS
    Apply a knowledge graph snapshot to the running Neo4j, reporting exactly what changes.

.DESCRIPTION
    Reads infra/neo4j/snapshot/kg-snapshot.cypher (written by export-kg-snapshot.ps1), compares
    every row in it against the live graph, prints a per-edge diff, and then applies it.

    WHY A DIFF FIRST
    ----------------
    The snapshot carries learned Beta-Bernoulli counts -- alpha/beta values representing months of
    scored predictions. Overwriting a live edge that has learned MORE than the snapshot silently
    throws that evidence away, and nothing downstream would report it. So this script always shows
    what it is about to change before changing it, and -DryRun stops after the report.

    CLASSIFICATION
    --------------
      NEW              edge is in the snapshot, absent from the graph        -> created
      CHANGED          edge exists but a property differs                    -> overwritten
      UNCHANGED        edge matches the snapshot exactly                     -> untouched
      MISSING_ENDPOINT the factor or asset the edge needs does not exist     -> SKIPPED, reported
      LIVE_ONLY        edge is in the graph but not in the snapshot          -> kept, or -Prune

    MISSING_ENDPOINT is reported rather than ignored because Cypher's MATCH silently binds nothing
    when a node is absent, and cypher-shell still exits 0 -- the failure mode that discarded a whole
    file of expert priors unnoticed (see infra/neo4j/init/09-verify-seed.cypher). Usually it means
    the asset registry moved on and the snapshot predates it: re-seed assets, then re-import.

    LIVE_ONLY is KEPT by default. An edge the graph has learned since the snapshot was taken is not
    garbage, and deleting it would be the one irreversible thing this script could do. Pass -Prune
    only when you deliberately want the graph to match the snapshot exactly.

.PARAMETER SnapshotPath
    Snapshot to apply. Defaults to infra/neo4j/snapshot/kg-snapshot.cypher.

.PARAMETER Container
    Neo4j container name. Defaults to feed-neo4j.

.PARAMETER DryRun
    Report the diff and exit without writing anything.

.PARAMETER Prune
    Also delete CAUSES/CORRELATES_WITH edges present in the graph but absent from the snapshot.
    Never touches Asset, AssetGroup or MEMBER_OF.

.PARAMETER Detail
    Print every row in the diff, not just the first 25 per category.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\import-kg-snapshot.ps1 -DryRun
    powershell -ExecutionPolicy Bypass -File scripts\import-kg-snapshot.ps1
    powershell -ExecutionPolicy Bypass -File scripts\import-kg-snapshot.ps1 -Prune -Detail
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$SnapshotPath,

    [string]$Container = "feed-neo4j",

    [switch]$DryRun,

    [switch]$Prune,

    [switch]$Detail
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

# Must match $SnapshotFormat in export-kg-snapshot.ps1. The row regex below is tied to that
# format; refusing an unknown version is what stops this script from half-parsing a newer file.
$SupportedFormat = "v1"

$previousOutputEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

try {
    # --- Resolve and read the snapshot --------------------------------------------------------
    $snap = if ($SnapshotPath) {
        $SnapshotPath
    }
    else {
        Join-Path $repoRoot "infra\neo4j\snapshot\kg-snapshot.cypher"
    }

    if (-not (Test-Path $snap)) {
        throw "No snapshot at $snap. Create one first: powershell -File scripts\export-kg-snapshot.ps1"
    }

    $snapLines = Get-Content $snap

    $formatLine = $snapLines | Where-Object { $_ -match '^//\s*snapshot-format:\s*(\S+)' } | Select-Object -First 1
    if (-not $formatLine) {
        throw "$snap has no 'snapshot-format' header. It was not written by export-kg-snapshot.ps1."
    }
    $null = $formatLine -match '^//\s*snapshot-format:\s*(\S+)'
    $format = $Matches[1]
    if ($format -ne $SupportedFormat) {
        throw "Snapshot format '$format' is not supported by this script (expects '$SupportedFormat'). " +
              "Update import-kg-snapshot.ps1's parser before applying it."
    }

    $exportedLine = $snapLines | Where-Object { $_ -match '^//\s*exported-at:\s*(\S+)' } | Select-Object -First 1
    $exportedAt = if ($exportedLine -and $exportedLine -match '^//\s*exported-at:\s*(\S+)') { $Matches[1] } else { "unknown" }

    # --- Parse the row literals ---------------------------------------------------------------
    # Section markers tell us which relationship type each block of rows belongs to. The row
    # literal text is kept verbatim so the diff query re-uses exactly what the file contains
    # rather than a re-serialised copy of it.
    $edgeRowRegex = "^\s*\{source: '(?<source>[^']*)', sourceLabel: '(?<sourceLabel>[^']*)', " +
                    "target: '(?<target>[^']*)', targetLabel: '(?<targetLabel>[^']*)', " +
                    "condition: (?<condition>null|'[^']*'), direction: '(?<direction>[^']*)', " +
                    "weight: (?<weight>[^,]+), confidence: (?<confidence>[^,]+), " +
                    "alpha: (?<alpha>[^,]+), beta: (?<beta>[^,]+), " +
                    "last_updated: '(?<lastUpdated>[^']*)'\},?\s*$"
    $factorRowRegex = "^\s*\{id: '(?<id>[^']*)', name: '(?<name>.*)', category: '(?<category>[^']*)'\},?\s*$"

    $section     = ""
    $causesRows  = [System.Collections.Generic.List[string]]::new()
    $correlRows  = [System.Collections.Generic.List[string]]::new()
    $factorIds   = [System.Collections.Generic.List[string]]::new()

    foreach ($line in $snapLines) {
        if ($line -match '^//\s*section:\s*(\S+)') { $section = $Matches[1]; continue }
        if ($line.TrimStart().StartsWith("//")) { continue }

        switch ($section) {
            "CAUSAL_FACTORS" {
                if ($line -match $factorRowRegex) { $factorIds.Add($Matches['id']) }
            }
            "CAUSES" {
                if ($line -match $edgeRowRegex) { $causesRows.Add(($line -replace ',\s*$', '')) }
            }
            "CORRELATES_WITH" {
                if ($line -match $edgeRowRegex) { $correlRows.Add(($line -replace ',\s*$', '')) }
            }
        }
    }

    if ($causesRows.Count -eq 0 -and $correlRows.Count -eq 0) {
        throw "Parsed 0 edges from $snap. The file is empty or its row format does not match '$SupportedFormat'."
    }

    Write-Host "Feed Analyzer - importing KG snapshot into '$Container'" -ForegroundColor Green
    Write-Host "  snapshot:    $snap"
    Write-Host "  exported at: $exportedAt"
    Write-Host "  parsed:      $($factorIds.Count) factors, $($causesRows.Count) CAUSES, $($correlRows.Count) CORRELATES_WITH"
    Write-Host ""

    # --- Credentials --------------------------------------------------------------------------
    $envPath = Join-Path $repoRoot "infra\.env"
    if (-not (Test-Path $envPath)) {
        throw "Cannot find infra/.env at $envPath."
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

    $neoUser = if ($envVars.ContainsKey("NEO4J_USER")) { $envVars["NEO4J_USER"] } else { "neo4j" }
    $neoPass = $envVars["NEO4J_PASSWORD"]
    if (-not $neoPass) { throw "NEO4J_PASSWORD is not set in infra/.env" }

    $state = & docker inspect --format "{{.State.Status}}" $Container 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Container '$Container' not found. Start the stack first." }
    if ($state.Trim() -ne "running") { throw "Container '$Container' is '$($state.Trim())', not running." }

    function Invoke-CypherLines {
        param([string]$Query)
        $raw = $Query | & docker exec -i $Container cypher-shell -u $neoUser -p "$neoPass" `
            --format plain --non-interactive 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "cypher-shell failed:`n$($raw -join "`n")"
        }
        $lines = @($raw | ForEach-Object { $_.ToString() })
        if ($lines.Count -le 1) { return @() }
        return @($lines[1..($lines.Count - 1)] | ForEach-Object {
            $s = $_.Trim()
            if ($s.StartsWith('"') -and $s.EndsWith('"') -and $s.Length -ge 2) {
                $s = $s.Substring(1, $s.Length - 2)
            }
            $s
        } | Where-Object { $_ -ne "" })
    }

    # --- Build the diff query for one edge section --------------------------------------------
    # Comparison is exact equality. Both sides originate from the same export path -- Cypher's
    # toString on a Float is round-trip safe -- so a difference here is a real difference, not a
    # formatting artefact. coalesce guards confidence, the one property older edges may lack.
    # $CreatableSourceIds are source nodes the snapshot creates itself before its edge sections
    # run -- the CausalFactor ids in its first block. Without this the diff calls all 248 CAUSES
    # rows MISSING_ENDPOINT on a fresh volume, $willWrite comes out 0, and the script reports
    # "already matches" while restoring nothing: the precise disaster-recovery case it exists for.
    # Asset and AssetGroup are NOT creatable here, so a missing one is still a genuine skip.
    function New-DiffQuery {
        param([string[]]$Rows, [string]$RelType, [string]$SourceLabel, [string[]]$CreatableSourceIds = @())

        $rowList = ($Rows -join ",`n")
        $creatable = if ($CreatableSourceIds.Count -gt 0) {
            "'" + (($CreatableSourceIds | ForEach-Object { $_ -replace "'", "\'" }) -join "', '") + "'"
        }
        else { "" }

        return @"
WITH [$creatable] AS creatable
UNWIND [
$rowList
] AS row
OPTIONAL MATCH (s:$SourceLabel {id: row.source})
OPTIONAL MATCH (t) WHERE t.id = row.target AND row.targetLabel IN labels(t)
OPTIONAL MATCH (s)-[e:$RelType]->(t)
  WHERE (e.condition IS NULL AND row.condition IS NULL) OR e.condition = row.condition
WITH creatable, row, s, t, e
RETURN
  CASE
    WHEN t IS NULL              THEN 'MISSING_ENDPOINT'
    WHEN s IS NULL AND NOT row.source IN creatable THEN 'MISSING_ENDPOINT'
    WHEN s IS NULL              THEN 'NEW'
    WHEN e IS NULL              THEN 'NEW'
    WHEN e.direction <> row.direction
      OR e.weight <> row.weight
      OR coalesce(e.confidence, -1.0) <> row.confidence
      OR e.alpha <> row.alpha
      OR e.beta <> row.beta   THEN 'CHANGED'
    ELSE 'UNCHANGED'
  END + '|' + row.source + '|' + row.target + '|' + coalesce(row.condition, '-') + '|' +
  CASE
    WHEN t IS NULL THEN 'no ' + row.targetLabel + ' node ' + row.target
    WHEN s IS NULL AND NOT row.source IN creatable THEN 'no $SourceLabel node ' + row.source
    WHEN s IS NULL OR e IS NULL THEN 'weight ' + toString(row.weight) + ', alpha/beta ' +
                        toString(row.alpha) + '/' + toString(row.beta)
    ELSE trim(
      (CASE WHEN e.direction <> row.direction
            THEN 'direction ' + e.direction + '->' + row.direction + '  ' ELSE '' END) +
      (CASE WHEN e.weight <> row.weight
            THEN 'weight ' + toString(e.weight) + '->' + toString(row.weight) + '  ' ELSE '' END) +
      (CASE WHEN coalesce(e.confidence, -1.0) <> row.confidence
            THEN 'confidence ' + toString(coalesce(e.confidence, -1.0)) + '->' +
                 toString(row.confidence) + '  ' ELSE '' END) +
      (CASE WHEN e.alpha <> row.alpha OR e.beta <> row.beta
            THEN 'alpha/beta ' + toString(e.alpha) + '/' + toString(e.beta) + '->' +
                 toString(row.alpha) + '/' + toString(row.beta) ELSE '' END)
    )
  END AS diff;
"@
    }

    # --- Run the diffs ------------------------------------------------------------------------
    Write-Host "Comparing snapshot against the live graph..." -ForegroundColor Gray

    # CausalFactor nodes the snapshot's first section will MERGE into existence. Reported so the
    # NEW edge count below is explicable on a fresh volume, where every factor is absent.
    $factorList = "'" + (($factorIds | ForEach-Object { $_ -replace "'", "\'" }) -join "', '") + "'"
    $missingFactors = Invoke-CypherLines @"
WITH [$factorList] AS ids
UNWIND ids AS id
OPTIONAL MATCH (cf:CausalFactor {id: id})
WITH id, cf WHERE cf IS NULL
RETURN id;
"@

    $diffLines = [System.Collections.Generic.List[object]]::new()

    if ($causesRows.Count -gt 0) {
        foreach ($d in (Invoke-CypherLines (New-DiffQuery -Rows $causesRows -RelType "CAUSES" `
                        -SourceLabel "CausalFactor" -CreatableSourceIds $factorIds))) {
            $parts = $d -split '\|', 5
            if ($parts.Count -lt 5) { continue }
            $diffLines.Add([pscustomobject]@{
                Rel = "CAUSES"; Status = $parts[0]; Source = $parts[1]
                Target = $parts[2]; Condition = $parts[3]; Detail = $parts[4]
            })
        }
    }

    if ($correlRows.Count -gt 0) {
        foreach ($d in (Invoke-CypherLines (New-DiffQuery -Rows $correlRows -RelType "CORRELATES_WITH" -SourceLabel "Asset"))) {
            $parts = $d -split '\|', 5
            if ($parts.Count -lt 5) { continue }
            $diffLines.Add([pscustomobject]@{
                Rel = "CORRELATES_WITH"; Status = $parts[0]; Source = $parts[1]
                Target = $parts[2]; Condition = $parts[3]; Detail = $parts[4]
            })
        }
    }

    # --- LIVE_ONLY: edges the graph has that the snapshot does not -----------------------------
    # Built as a list of (source, target, condition) keys the snapshot covers, so the graph can
    # report its own extras. '-' stands in for a null condition on both sides.
    function New-LiveOnlyQuery {
        param([string[]]$Rows, [string]$RelType, [string]$SourceLabel)
        $rowList = ($Rows -join ",`n")
        return @"
WITH [
$rowList
] AS rows
WITH [r IN rows | r.source + '|' + r.target + '|' + coalesce(r.condition, '-')] AS keys
MATCH (s:$SourceLabel)-[e:$RelType]->(t)
WITH keys, s, e, t, s.id + '|' + t.id + '|' + coalesce(e.condition, '-') AS key
WHERE NOT key IN keys
RETURN 'LIVE_ONLY|' + s.id + '|' + t.id + '|' + coalesce(e.condition, '-') + '|weight ' +
       toString(e.weight) + ', alpha/beta ' + toString(e.alpha) + '/' + toString(e.beta) AS diff
ORDER BY diff;
"@
    }

    foreach ($spec in @(
        @{ Rows = $causesRows; Rel = "CAUSES";          Label = "CausalFactor" },
        @{ Rows = $correlRows; Rel = "CORRELATES_WITH"; Label = "Asset" }
    )) {
        if ($spec.Rows.Count -eq 0) { continue }
        foreach ($d in (Invoke-CypherLines (New-LiveOnlyQuery -Rows $spec.Rows -RelType $spec.Rel -SourceLabel $spec.Label))) {
            $parts = $d -split '\|', 5
            if ($parts.Count -lt 5) { continue }
            $diffLines.Add([pscustomobject]@{
                Rel = $spec.Rel; Status = $parts[0]; Source = $parts[1]
                Target = $parts[2]; Condition = $parts[3]; Detail = $parts[4]
            })
        }
    }

    # --- Report -------------------------------------------------------------------------------
    $byStatus = @{}
    foreach ($status in @("NEW", "CHANGED", "UNCHANGED", "MISSING_ENDPOINT", "LIVE_ONLY")) {
        $byStatus[$status] = @($diffLines | Where-Object { $_.Status -eq $status })
    }

    $colour = @{
        NEW              = "Green"
        CHANGED          = "Yellow"
        UNCHANGED        = "Gray"
        MISSING_ENDPOINT = "Red"
        LIVE_ONLY        = "Cyan"
    }

    Write-Host ""
    Write-Host "Diff summary" -ForegroundColor White
    Write-Host ("  {0,-17} {1,5}" -f "FACTORS_TO_ADD", $missingFactors.Count) `
        -ForegroundColor $(if ($missingFactors.Count -gt 0) { "Green" } else { "Gray" })
    foreach ($status in @("NEW", "CHANGED", "UNCHANGED", "MISSING_ENDPOINT", "LIVE_ONLY")) {
        Write-Host ("  {0,-17} {1,5}" -f $status, $byStatus[$status].Count) -ForegroundColor $colour[$status]
    }

    foreach ($status in @("MISSING_ENDPOINT", "NEW", "CHANGED", "LIVE_ONLY")) {
        $rows = $byStatus[$status]
        if ($rows.Count -eq 0) { continue }
        Write-Host ""
        Write-Host "$status ($($rows.Count))" -ForegroundColor $colour[$status]
        $show = if ($Detail) { $rows } else { $rows | Select-Object -First 25 }
        foreach ($r in $show) {
            Write-Host ("  {0,-15} {1} -> {2} [{3}]  {4}" -f $r.Rel, $r.Source, $r.Target, $r.Condition, $r.Detail)
        }
        if (-not $Detail -and $rows.Count -gt 25) {
            Write-Host "  ... $($rows.Count - 25) more (-Detail to list all)" -ForegroundColor DarkGray
        }
    }

    # --- Warn where it matters ------------------------------------------------------------------
    # An edge whose live alpha+beta exceeds the snapshot's has been scored more times since the
    # export than the snapshot knows about. Applying it trades newer evidence for older.
    $regressions = @($byStatus["CHANGED"] | Where-Object {
        $_.Detail -match 'alpha/beta ([\d.]+)/([\d.]+)->([\d.]+)/([\d.]+)' -and
        ([double]$Matches[1] + [double]$Matches[2]) -gt ([double]$Matches[3] + [double]$Matches[4])
    })

    if ($regressions.Count -gt 0) {
        Write-Host ""
        Write-Host "WARNING: $($regressions.Count) edge(s) have MORE evidence live than in the snapshot." -ForegroundColor Yellow
        Write-Host "         Applying replaces newer learned counts with older ones. The snapshot is" -ForegroundColor Yellow
        Write-Host "         probably stale -- consider re-running export-kg-snapshot.ps1 instead." -ForegroundColor Yellow
        foreach ($r in ($regressions | Select-Object -First 10)) {
            Write-Host ("         {0} -> {1} [{2}]  {3}" -f $r.Source, $r.Target, $r.Condition, $r.Detail) -ForegroundColor Yellow
        }
    }

    $willWrite = $byStatus["NEW"].Count + $byStatus["CHANGED"].Count

    if ($DryRun) {
        Write-Host ""
        Write-Host "-DryRun: nothing written. $willWrite edge(s) would be created or updated." -ForegroundColor Cyan
        if ($Prune -and $byStatus["LIVE_ONLY"].Count -gt 0) {
            Write-Host "         -Prune would additionally DELETE $($byStatus['LIVE_ONLY'].Count) live-only edge(s)." -ForegroundColor Cyan
        }
        exit 0
    }

    # $missingFactors is checked too: a factor carrying no edges yet would otherwise leave
    # $willWrite at 0 and skip the apply, never creating the node.
    if ($willWrite -eq 0 -and $missingFactors.Count -eq 0 -and
        -not ($Prune -and $byStatus["LIVE_ONLY"].Count -gt 0)) {
        Write-Host ""
        Write-Host "Graph already matches the snapshot. Nothing to do." -ForegroundColor Green
        exit 0
    }

    # --- Apply --------------------------------------------------------------------------------
    # The snapshot file is executed as-is rather than from the parsed rows, so the thing reviewed
    # in git is exactly the thing that runs. Its own statements are idempotent.
    Write-Host ""
    Write-Host "Applying snapshot..." -ForegroundColor Green

    $applyOut = Get-Content $snap -Raw | & docker exec -i $Container cypher-shell `
        -u $neoUser -p "$neoPass" --format plain --non-interactive --fail-at-end 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Applying the snapshot failed:`n$($applyOut -join "`n")"
    }

    # --- Prune ---------------------------------------------------------------------------------
    if ($Prune -and $byStatus["LIVE_ONLY"].Count -gt 0) {
        Write-Host "Pruning $($byStatus['LIVE_ONLY'].Count) live-only edge(s)..." -ForegroundColor Yellow
        foreach ($spec in @(
            @{ Rows = $causesRows; Rel = "CAUSES";          Label = "CausalFactor" },
            @{ Rows = $correlRows; Rel = "CORRELATES_WITH"; Label = "Asset" }
        )) {
            if ($spec.Rows.Count -eq 0) { continue }
            $rowList = ($spec.Rows -join ",`n")
            $pruneQuery = @"
WITH [
$rowList
] AS rows
WITH [r IN rows | r.source + '|' + r.target + '|' + coalesce(r.condition, '-')] AS keys
MATCH (s:$($spec.Label))-[e:$($spec.Rel)]->(t)
WITH keys, e, s.id + '|' + t.id + '|' + coalesce(e.condition, '-') AS key
WHERE NOT key IN keys
DELETE e
RETURN count(*) AS deleted;
"@
            $null = Invoke-CypherLines $pruneQuery
        }
    }

    # --- Verify --------------------------------------------------------------------------------
    # Re-run the CAUSES diff and assert nothing is left outstanding. A silent partial apply is the
    # exact failure this whole pipeline is built to avoid, so it is checked rather than assumed.
    Write-Host "Verifying..." -ForegroundColor Gray
    $post = @()
    if ($causesRows.Count -gt 0) {
        $post += Invoke-CypherLines (New-DiffQuery -Rows $causesRows -RelType "CAUSES" `
                                     -SourceLabel "CausalFactor" -CreatableSourceIds $factorIds)
    }
    if ($correlRows.Count -gt 0) {
        $post += Invoke-CypherLines (New-DiffQuery -Rows $correlRows -RelType "CORRELATES_WITH" -SourceLabel "Asset")
    }

    $stillNew     = @($post | Where-Object { $_.StartsWith("NEW|") }).Count
    $stillChanged = @($post | Where-Object { $_.StartsWith("CHANGED|") }).Count
    $stillMissing = @($post | Where-Object { $_.StartsWith("MISSING_ENDPOINT|") }).Count

    Write-Host ""
    if ($stillNew -eq 0 -and $stillChanged -eq 0) {
        Write-Host "Applied. Graph now matches the snapshot." -ForegroundColor Green
        Write-Host "  created/updated: $willWrite"
        if ($Prune) { Write-Host "  pruned:          $($byStatus['LIVE_ONLY'].Count)" }
    }
    else {
        Write-Host "INCOMPLETE: $stillNew still missing, $stillChanged still differing after apply." -ForegroundColor Red
    }

    if ($stillMissing -gt 0) {
        Write-Host ""
        Write-Host "$stillMissing edge(s) were SKIPPED because an endpoint node does not exist." -ForegroundColor Red
        Write-Host "Re-seed assets from the registry, then re-run:" -ForegroundColor Red
        Write-Host "  python scripts\generate-asset-seed.py" -ForegroundColor Red
        exit 1
    }

    if ($stillNew -gt 0 -or $stillChanged -gt 0) { exit 1 }
}
finally {
    [Console]::OutputEncoding = $previousOutputEncoding
}
