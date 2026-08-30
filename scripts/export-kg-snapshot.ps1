<#
.SYNOPSIS
    Export the live knowledge graph's causal layer to a re-applyable Cypher snapshot.

.DESCRIPTION
    Reads the running Neo4j (feed-neo4j) and writes every CausalFactor node, every CAUSES edge
    and every CORRELATES_WITH edge -- with their CURRENT weight, confidence and Beta-Bernoulli
    counts -- to infra/neo4j/snapshot/kg-snapshot.cypher.

    WHY THIS EXISTS
    ---------------
    infra/neo4j/init/*.cypher seeds expert PRIORS: every edge starts at alpha=1.0, beta=1.0.
    Credibility refines those counts online per scored prediction and the offline structure
    learner adds asset-level edges that no seed file declares at all. As of 2026-08-30 the live
    graph held 11 such learned asset edges, one carrying alpha=65/beta=8 -- months of evidence
    that exists nowhere on disk. Re-seeding a fresh volume silently reverts to the priors.

    This script is the other half: it puts the learned state back on disk so it survives a
    volume rebuild. Apply it with import-kg-snapshot.ps1.

    WHAT IT DOES NOT EXPORT
    -----------------------
    Asset, AssetGroup and MEMBER_OF. Those are owned by src/shared/shared/reference/assets.json
    via scripts/generate-asset-seed.py, and duplicating them here would give the graph a second
    source of truth for the registry. CausalFactor nodes ARE exported, because an edge whose
    endpoint is missing is silently dropped by Cypher's MATCH.

    OUTPUT IS DETERMINISTIC
    -----------------------
    Rows are sorted and one per line, so re-running against an unchanged graph produces a
    byte-identical file and `git diff` shows exactly what the graph learned since last export.
    The file is overwritten on every run.

    SAFETY
    ------
    Refuses to write if the container is not running or the graph holds no CAUSES edges, so a
    failed query cannot overwrite a good snapshot with an empty one.

.PARAMETER OutputPath
    Snapshot file to write. Defaults to infra/neo4j/snapshot/kg-snapshot.cypher.

.PARAMETER Container
    Neo4j container name. Defaults to feed-neo4j.

.PARAMETER Check
    Do not write. Exit 1 if the existing snapshot differs from the live graph, 0 if current.
    For use in CI or a pre-commit check, mirroring generate-asset-seed.py --check.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\export-kg-snapshot.ps1
    powershell -ExecutionPolicy Bypass -File scripts\export-kg-snapshot.ps1 -Check
    powershell -ExecutionPolicy Bypass -File scripts\export-kg-snapshot.ps1 -OutputPath C:\tmp\kg.cypher
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$OutputPath,

    [string]$Container = "feed-neo4j",

    [switch]$Check
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

# The snapshot format version. import-kg-snapshot.ps1 refuses to parse anything else, so a
# change to the row shape below MUST bump this and the parser in that script together.
$SnapshotFormat = "v1"

# cypher-shell emits UTF-8; PowerShell would otherwise decode native stdout using the console's
# OEM code page and mangle any non-ASCII in a factor name (same failure as export-prediction-audit).
$previousOutputEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

try {
    # --- Load credentials from infra/.env ---------------------------------------------------
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

    $neoUser = if ($envVars.ContainsKey("NEO4J_USER")) { $envVars["NEO4J_USER"] } else { "neo4j" }
    $neoPass = $envVars["NEO4J_PASSWORD"]
    if (-not $neoPass) { throw "NEO4J_PASSWORD is not set in infra/.env" }

    # --- Precondition: the container must be up ---------------------------------------------
    $state = & docker inspect --format "{{.State.Status}}" $Container 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Container '$Container' not found. Start the stack first (docker compose -p infra ... up -d)."
    }
    if ($state.Trim() -ne "running") {
        throw "Container '$Container' is '$($state.Trim())', not running. Nothing exported."
    }

    # --- Helper: run a read-only Cypher query, return its rows (header line dropped) ---------
    # The query goes in on stdin so multi-line Cypher needs no shell quoting. --format plain
    # wraps every returned string in double quotes; every string we build below uses single
    # quotes internally, so nothing is escaped and stripping the outer pair is lossless.
    function Invoke-CypherLines {
        param([string]$Query)
        $raw = $Query | & docker exec -i $Container cypher-shell -u $neoUser -p "$neoPass" `
            --format plain --non-interactive 2>&1
        if ($LASTEXITCODE -ne 0) {
            # $raw can contain the query but never the password: -p is passed as an argv entry.
            throw "cypher-shell failed:`n$($raw -join "`n")"
        }
        $lines = @($raw | ForEach-Object { $_.ToString() })
        if ($lines.Count -le 1) { return @() }
        # Drop the column-name header, unquote, and discard blank trailing lines.
        return @($lines[1..($lines.Count - 1)] | ForEach-Object {
            $s = $_.Trim()
            if ($s.StartsWith('"') -and $s.EndsWith('"') -and $s.Length -ge 2) {
                $s = $s.Substring(1, $s.Length - 2)
            }
            $s
        } | Where-Object { $_ -ne "" })
    }

    Write-Host "Feed Analyzer - exporting knowledge graph snapshot from '$Container' ..." -ForegroundColor Green

    # --- 1. CausalFactor nodes ---------------------------------------------------------------
    # Exported because a CAUSES row whose factor node is absent is a silent no-op at apply time
    # (the same failure mode that lost an entire file of priors; see 09-verify-seed.cypher).
    Write-Host "  [1/3] Reading CausalFactor nodes..." -ForegroundColor Gray
    $factorRows = Invoke-CypherLines @"
MATCH (cf:CausalFactor)
WITH cf ORDER BY cf.id
RETURN "  {id: '" + cf.id + "', name: '" + replace(coalesce(cf.name, ''), "'", "\\'") +
       "', category: '" + coalesce(cf.category, '') + "'}," AS line;
"@

    # --- 2. CAUSES edges ---------------------------------------------------------------------
    # One row per edge, keyed by (source, target, condition) -- the triple that identifies an
    # edge. condition is emitted as the literal `null` when absent so the apply query can match
    # the unconditional case explicitly instead of binding an arbitrary conditioned edge.
    Write-Host "  [2/3] Reading CAUSES edges..." -ForegroundColor Gray
    $causesRows = Invoke-CypherLines @"
MATCH (s:CausalFactor)-[r:CAUSES]->(t)
WITH s, r, t
ORDER BY s.id, labels(t)[0], t.id, coalesce(r.condition, '')
RETURN "  {source: '" + s.id + "', sourceLabel: 'CausalFactor', target: '" + t.id +
       "', targetLabel: '" + labels(t)[0] + "', condition: " +
       CASE WHEN r.condition IS NULL THEN "null" ELSE "'" + r.condition + "'" END +
       ", direction: '" + r.direction + "', weight: " + toString(r.weight) +
       ", confidence: " + toString(coalesce(r.confidence, 0.0)) +
       ", alpha: " + toString(r.alpha) + ", beta: " + toString(r.beta) +
       ", last_updated: '" + toString(coalesce(r.last_updated, datetime())) + "'}," AS line;
"@

    # --- 3. CORRELATES_WITH edges ------------------------------------------------------------
    Write-Host "  [3/3] Reading CORRELATES_WITH edges..." -ForegroundColor Gray
    $correlRows = Invoke-CypherLines @"
MATCH (s:Asset)-[r:CORRELATES_WITH]->(t:Asset)
WITH s, r, t
ORDER BY s.id, t.id, coalesce(r.condition, '')
RETURN "  {source: '" + s.id + "', sourceLabel: 'Asset', target: '" + t.id +
       "', targetLabel: '" + labels(t)[0] + "', condition: " +
       CASE WHEN r.condition IS NULL THEN "null" ELSE "'" + r.condition + "'" END +
       ", direction: '" + r.direction + "', weight: " + toString(r.weight) +
       ", confidence: " + toString(coalesce(r.confidence, 0.0)) +
       ", alpha: " + toString(r.alpha) + ", beta: " + toString(r.beta) +
       ", last_updated: '" + toString(coalesce(r.last_updated, datetime())) + "'}," AS line;
"@

    # --- Refuse to write an empty snapshot over a good one -----------------------------------
    # A query that returns nothing looks identical to a graph that genuinely has nothing, and
    # overwriting here would destroy the only on-disk copy of the learned weights.
    if ($causesRows.Count -eq 0) {
        throw "The live graph returned 0 CAUSES edges. Refusing to write an empty snapshot. " +
              "Check that '$Container' is healthy and that the graph has been seeded."
    }
    if ($factorRows.Count -eq 0) {
        throw "The live graph returned 0 CausalFactor nodes. Refusing to write an empty snapshot."
    }

    # --- Strip the trailing comma of each block's last row -----------------------------------
    function Close-Block {
        param([string[]]$Rows)
        if ($Rows.Count -eq 0) { return @() }
        $copy = @($Rows)
        $copy[$copy.Count - 1] = $copy[$copy.Count - 1] -replace ',\s*$', ''
        return $copy
    }

    $factorRows = Close-Block $factorRows
    $causesRows = Close-Block $causesRows
    $correlRows = Close-Block $correlRows

    $learnedCount = @($causesRows | Where-Object { $_ -notmatch 'alpha: 1\.0, beta: 1\.0' }).Count
    $exportedAt   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

    # --- Build the file ---------------------------------------------------------------------
    $sb = [System.Text.StringBuilder]::new()
    function Add-Line { param([string]$Text = "") ; [void]$sb.AppendLine($Text) }

    Add-Line "// Live knowledge graph snapshot (GENERATED -- do not edit by hand)."
    Add-Line "//"
    Add-Line "// Regenerate with:  powershell -File scripts\export-kg-snapshot.ps1"
    Add-Line "// Apply with:       powershell -File scripts\import-kg-snapshot.ps1"
    Add-Line "// snapshot-format:  $SnapshotFormat"
    Add-Line "// exported-at:      $exportedAt"
    Add-Line "//"
    Add-Line "// WHAT THIS IS"
    Add-Line "// infra/neo4j/init/*.cypher seeds expert PRIORS at alpha=1.0, beta=1.0. This file is the"
    Add-Line "// graph's LEARNED state: weights refined online by Credibility and edges added by the"
    Add-Line "// offline structure learner, neither of which exists in the seed files. Re-seeding a fresh"
    Add-Line "// volume without applying this file silently reverts every learned weight to its prior."
    Add-Line "//"
    Add-Line "// Contents at export time:"
    Add-Line "//   CausalFactor nodes   : $($factorRows.Count)"
    Add-Line "//   CAUSES edges         : $($causesRows.Count)  ($learnedCount with evidence beyond the 1.0/1.0 prior)"
    Add-Line "//   CORRELATES_WITH edges: $($correlRows.Count)"
    Add-Line "//"
    Add-Line "// NOT IN THIS FILE: Asset, AssetGroup and MEMBER_OF. Those come from"
    Add-Line "// src/shared/shared/reference/assets.json via scripts/generate-asset-seed.py, which stays"
    Add-Line "// the single source of truth for the registry. Applying this file to a graph whose assets"
    Add-Line "// are missing leaves those edges uncreated; import-kg-snapshot.ps1 reports that rather"
    Add-Line "// than letting Cypher drop them silently."
    Add-Line "//"
    # Single-quoted: these lines contain backticks, which PowerShell would otherwise read as its
    # own escape character and strip.
    Add-Line '// WHY EDGES MATCH ON `condition`, INCLUDING WHEN IT IS NULL'
    Add-Line '// `MERGE (s)-[r:CAUSES]->(t)` with no properties matches ANY CAUSES edge between the two'
    Add-Line '// nodes -- including a conditioned one, whose weight it would then overwrite while leaving'
    Add-Line "// ``condition`` in place (verified 2026-08-14; see 06-seed-group-edges.cypher's header). The"
    Add-Line '// OPTIONAL MATCH + FOREACH below is used instead of MERGE precisely because it can express'
    Add-Line '// "the edge whose condition IS NULL", which MERGE cannot.'
    Add-Line "//"
    Add-Line "// Rows are sorted and one per line so this file diffs cleanly. It is overwritten in full"
    Add-Line "// on every export."
    Add-Line ""

    # --- CausalFactor nodes ------------------------------------------------------------------
    Add-Line "// =========================================================================="
    Add-Line "// section: CAUSAL_FACTORS"
    Add-Line "// =========================================================================="
    Add-Line "UNWIND ["
    foreach ($row in $factorRows) { Add-Line $row }
    Add-Line "] AS row"
    Add-Line "MERGE (cf:CausalFactor {id: row.id})"
    Add-Line "SET cf.name = row.name, cf.category = row.category;"
    Add-Line ""

    # --- CAUSES edges ------------------------------------------------------------------------
    Add-Line "// =========================================================================="
    Add-Line "// section: CAUSES"
    Add-Line "// =========================================================================="
    if ($causesRows.Count -gt 0) {
        Add-Line "UNWIND ["
        foreach ($row in $causesRows) { Add-Line $row }
        Add-Line "] AS row"
        Add-Line "MATCH (s:CausalFactor {id: row.source})"
        Add-Line "MATCH (t) WHERE t.id = row.target AND row.targetLabel IN labels(t)"
        Add-Line "OPTIONAL MATCH (s)-[e:CAUSES]->(t)"
        Add-Line "  WHERE (e.condition IS NULL AND row.condition IS NULL) OR e.condition = row.condition"
        Add-Line "WITH row, s, t, collect(e) AS existing"
        Add-Line "FOREACH (_ IN CASE WHEN size(existing) = 0 THEN [1] ELSE [] END |"
        Add-Line "  CREATE (s)-[:CAUSES {condition: row.condition, direction: row.direction,"
        Add-Line "                       weight: row.weight, confidence: row.confidence,"
        Add-Line "                       alpha: row.alpha, beta: row.beta,"
        Add-Line "                       last_updated: datetime(row.last_updated)}]->(t))"
        Add-Line "FOREACH (r IN existing |"
        Add-Line "  SET r.direction = row.direction, r.weight = row.weight, r.confidence = row.confidence,"
        Add-Line "      r.alpha = row.alpha, r.beta = row.beta,"
        Add-Line "      r.last_updated = datetime(row.last_updated));"
    }
    Add-Line ""

    # --- CORRELATES_WITH edges ---------------------------------------------------------------
    Add-Line "// =========================================================================="
    Add-Line "// section: CORRELATES_WITH"
    Add-Line "// =========================================================================="
    if ($correlRows.Count -gt 0) {
        Add-Line "UNWIND ["
        foreach ($row in $correlRows) { Add-Line $row }
        Add-Line "] AS row"
        Add-Line "MATCH (s:Asset {id: row.source})"
        Add-Line "MATCH (t:Asset {id: row.target})"
        Add-Line "OPTIONAL MATCH (s)-[e:CORRELATES_WITH]->(t)"
        Add-Line "  WHERE (e.condition IS NULL AND row.condition IS NULL) OR e.condition = row.condition"
        Add-Line "WITH row, s, t, collect(e) AS existing"
        Add-Line "FOREACH (_ IN CASE WHEN size(existing) = 0 THEN [1] ELSE [] END |"
        Add-Line "  CREATE (s)-[:CORRELATES_WITH {condition: row.condition, direction: row.direction,"
        Add-Line "                                weight: row.weight, confidence: row.confidence,"
        Add-Line "                                alpha: row.alpha, beta: row.beta,"
        Add-Line "                                last_updated: datetime(row.last_updated)}]->(t))"
        Add-Line "FOREACH (r IN existing |"
        Add-Line "  SET r.direction = row.direction, r.weight = row.weight, r.confidence = row.confidence,"
        Add-Line "      r.alpha = row.alpha, r.beta = row.beta,"
        Add-Line "      r.last_updated = datetime(row.last_updated));"
    }
    else {
        Add-Line "// No CORRELATES_WITH edges in the graph at export time."
    }

    $content = $sb.ToString()

    # --- Resolve output path -----------------------------------------------------------------
    $out = if ($OutputPath) {
        $OutputPath
    }
    else {
        Join-Path $repoRoot "infra\neo4j\snapshot\kg-snapshot.cypher"
    }

    # --- -Check: compare, do not write --------------------------------------------------------
    # The header carries an export timestamp that changes every run, so comparing raw text would
    # always report a difference. Only the data rows are compared.
    if ($Check) {
        if (-not (Test-Path $out)) {
            Write-Host "STALE: no snapshot at $out" -ForegroundColor Red
            exit 1
        }
        $stripHeader = {
            param($text)
            ($text -split "`r?`n" | Where-Object { -not $_.StartsWith("//") -and $_.Trim() -ne "" }) -join "`n"
        }
        $existing = & $stripHeader (Get-Content $out -Raw)
        $fresh    = & $stripHeader $content
        if ($existing -eq $fresh) {
            Write-Host "CURRENT: $out matches the live graph." -ForegroundColor Green
            exit 0
        }
        Write-Host "STALE: $out differs from the live graph. Re-run without -Check to update." -ForegroundColor Red
        exit 1
    }

    # --- Write --------------------------------------------------------------------------------
    $outDir = Split-Path -Parent $out
    if ($outDir -and -not (Test-Path $outDir)) {
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    }

    # UTF8Encoding($false) -> no BOM. cypher-shell reads the file as UTF-8 and a BOM would land in
    # the first comment token.
    [System.IO.File]::WriteAllText($out, $content, [System.Text.UTF8Encoding]::new($false))

    Write-Host ""
    Write-Host "Written to: $out" -ForegroundColor Green
    Write-Host "  CausalFactor nodes:    $($factorRows.Count)"
    Write-Host "  CAUSES edges:          $($causesRows.Count)"
    Write-Host "    with learned evidence: $learnedCount  (alpha/beta moved off the 1.0/1.0 prior)"
    Write-Host "  CORRELATES_WITH edges: $($correlRows.Count)"
    Write-Host ""
    Write-Host "Review with 'git diff', then apply to a graph with:" -ForegroundColor Gray
    Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\import-kg-snapshot.ps1 -DryRun" -ForegroundColor Gray
}
finally {
    [Console]::OutputEncoding = $previousOutputEncoding
}
