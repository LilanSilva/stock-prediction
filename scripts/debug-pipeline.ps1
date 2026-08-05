<#
.SYNOPSIS
    Diagnostic report for the Feed Analyzer pipeline on a given date.

.DESCRIPTION
    Runs against the local Docker stack. Reads credentials from infra/.env, queries Postgres
    via docker exec feed-postgres psql and Neo4j via docker exec feed-neo4j cypher-shell.

    Sections (all filtered to -Date, server time = UTC):
      1. Ingestion vs Cleansing gap  - articles ingested but with no cleansing record
      2. Cleansed as OTHER           - articles that landed in the OTHER event type
      3. Predictions                 - all predictions made that day with verification result
      4. Verification issues         - evaluations stuck in non-SCORED state
      5. Market-data issues          - price requests not DONE or missing close observations
      6. Credibility issues          - sources/assets with a credibility score below 0.40
      7. KG updates today            - CAUSES edges updated on that date (offline learner)

.PARAMETER Date
    The day to diagnose, formatted yyyy-MM-dd (e.g. 2026-08-04).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\debug-pipeline.ps1 -Date 2026-08-04
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, HelpMessage = "Day to diagnose, formatted yyyy-MM-dd")]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Date
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

# ---------------------------------------------------------------------------
# Load credentials
# ---------------------------------------------------------------------------
$envPath = Join-Path $repoRoot "infra\.env"
if (-not (Test-Path $envPath)) {
    throw "Cannot find infra/.env at $envPath"
}

$envVars = @{}
foreach ($line in Get-Content $envPath) {
    $trimmed = $line.Trim()
    if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
    $idx = $trimmed.IndexOf("=")
    if ($idx -lt 1) { continue }
    $envVars[$trimmed.Substring(0, $idx).Trim()] = $trimmed.Substring($idx + 1).Trim()
}

$pgUser  = if ($envVars["POSTGRES_USER"]) { $envVars["POSTGRES_USER"] } else { "feed_user" }
$pgPass  = $envVars["POSTGRES_PASSWORD"]
$pgDb    = if ($envVars["POSTGRES_DB"])   { $envVars["POSTGRES_DB"] }   else { "feed" }
$neo4jPw = if ($envVars["NEO4J_PASSWORD"]) { $envVars["NEO4J_PASSWORD"] } else { "local_dev_pw" }

if (-not $pgPass) { throw "POSTGRES_PASSWORD not set in infra/.env" }

Add-Type -AssemblyName System.Web

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Invoke-PsqlCsv {
    param([string]$Sql)
    $csv = & docker exec -e "PGPASSWORD=$pgPass" feed-postgres `
        psql -U $pgUser -d $pgDb -P pager=off --csv -c $Sql 2>&1
    if ($LASTEXITCODE -ne 0) { throw "psql failed:`n$csv" }
    if (-not $csv -or @($csv).Count -eq 0) { return @() }
    return @($csv | ConvertFrom-Csv)
}

function Invoke-CypherCsv {
    param([string]$Cypher)
    $result = & docker exec feed-neo4j cypher-shell `
        -u neo4j -p $neo4jPw --format plain -d neo4j $Cypher 2>&1
    if ($LASTEXITCODE -ne 0) { throw "cypher-shell failed:`n$result" }
    if (-not $result -or @($result).Count -eq 0) { return @() }
    $lines = @($result) | Where-Object { $_ -ne "" }
    if ($lines.Count -lt 2) { return @() }
    $header = $lines[0] -split ',\s*' | ForEach-Object { $_.Trim('"').Trim() }
    $rows = @()
    foreach ($line in $lines[1..($lines.Count - 1)]) {
        $vals = $line -split ',\s*' | ForEach-Object { $_.Trim('"').Trim() }
        $obj = [ordered]@{}
        for ($i = 0; $i -lt $header.Count; $i++) {
            $obj[$header[$i]] = if ($i -lt $vals.Count) { $vals[$i] } else { "" }
        }
        $rows += [PSCustomObject]$obj
    }
    return $rows
}

function ConvertTo-HtmlTable {
    param(
        [object[]]$Rows,
        [string[]]$Columns,
        [string]$EmptyText = "No issues found."
    )
    if (-not $Rows -or $Rows.Count -eq 0) {
        return "<p class='ok'>$([System.Web.HttpUtility]::HtmlEncode($EmptyText))</p>"
    }
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.Append("<table><thead><tr>")
    foreach ($col in $Columns) {
        [void]$sb.Append("<th>$([System.Web.HttpUtility]::HtmlEncode($col))</th>")
    }
    [void]$sb.Append("</tr></thead><tbody>")
    foreach ($row in $Rows) {
        [void]$sb.Append("<tr>")
        foreach ($col in $Columns) {
            $value = [string]$row.$col
            $cls = ""
            if ($col -eq "is_correct") {
                if ($value -eq "t") { $cls = " class='ok-cell'"; $value = "correct" }
                elseif ($value -eq "f") { $cls = " class='bad-cell'"; $value = "wrong" }
            }
            if ($col -eq "state" -or $col -eq "status") {
                if ($value -in @("DONE","SCORED","DELIVERED")) { $cls = " class='ok-cell'" }
                elseif ($value -in @("ERROR","FAILED"))        { $cls = " class='bad-cell'" }
                elseif ($value -ne "")                         { $cls = " class='warn-cell'" }
            }
            $encoded = [System.Web.HttpUtility]::HtmlEncode($value)
            [void]$sb.Append("<td$cls>$encoded</td>")
        }
        [void]$sb.Append("</tr>")
    }
    [void]$sb.Append("</tbody></table>")
    return $sb.ToString()
}

function Get-Badge {
    param([string]$Flag)
    switch ($Flag) {
        "OK"   { return "<span class='badge ok-badge'>OK</span>" }
        "WARN" { return "<span class='badge warn-badge'>WARN</span>" }
        "INFO" { return "<span class='badge info-badge'>INFO</span>" }
        default { return "<span class='badge'>$Flag</span>" }
    }
}

Write-Host "Feed Analyzer - building debug report for $Date ..." -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 1. Ingestion vs Cleansing gap
# ---------------------------------------------------------------------------
Write-Host "  [1/7] Ingestion vs cleansing gap..." -ForegroundColor Gray
$gapRows = Invoke-PsqlCsv @"
SELECT a.article_id,
       left(a.title, 90) AS title,
       a.source_id,
       a.language,
       a.ingested_at
FROM   ingestion.articles a
LEFT   JOIN cleansing.article_actions aa ON aa.article_id = a.article_id
WHERE  a.ingested_at::date = '$Date'::date
  AND  aa.article_id IS NULL
ORDER  BY a.ingested_at;
"@
$gapSummary = Invoke-PsqlCsv @"
SELECT count(*) AS ingested_total,
       count(aa.article_id) AS cleansed_total,
       count(*) FILTER (WHERE aa.article_id IS NULL) AS missing_in_cleansing
FROM   ingestion.articles a
LEFT   JOIN cleansing.article_actions aa ON aa.article_id = a.article_id
WHERE  a.ingested_at::date = '$Date'::date;
"@
$gapCols  = @("article_id","title","source_id","language","ingested_at")
$gapTable = ConvertTo-HtmlTable -Rows $gapRows -Columns $gapCols `
            -EmptyText "All articles ingested on $Date have a cleansing record."
$gs        = @($gapSummary)[0]
$gIngested = if ($gs) { $gs.ingested_total }      else { "0" }
$gCleansed = if ($gs) { $gs.cleansed_total }      else { "0" }
$gMissing  = if ($gs) { $gs.missing_in_cleansing } else { "0" }

# ---------------------------------------------------------------------------
# 2. Articles cleansed as OTHER
# ---------------------------------------------------------------------------
Write-Host "  [2/7] Articles classified as OTHER..." -ForegroundColor Gray
$otherRows = Invoke-PsqlCsv @"
SELECT a.article_id,
       left(ca.title, 90) AS title,
       ca.source_id,
       aa.actor,
       aa.action_lemma,
       aa.language,
       aa.event_type,
       ca.published_at
FROM   cleansing.article_actions aa
JOIN   cleansing.cluster_articles ca ON ca.article_id = aa.article_id
JOIN   ingestion.articles a          ON a.article_id  = aa.article_id
WHERE  aa.event_type = 'OTHER'
  AND  a.ingested_at::date = '$Date'::date
ORDER  BY ca.published_at;
"@
$otherCols  = @("article_id","title","source_id","actor","action_lemma","language","published_at")
$otherTable = ConvertTo-HtmlTable -Rows $otherRows -Columns $otherCols `
              -EmptyText "No articles classified as OTHER on $Date."

# ---------------------------------------------------------------------------
# 3. Predictions + verification results
# ---------------------------------------------------------------------------
Write-Host "  [3/7] Predictions and results..." -ForegroundColor Gray
$predRows = Invoke-PsqlCsv @"
SELECT p.prediction_id,
       p.asset_id,
       p.direction          AS predicted_dir,
       p.magnitude          AS predicted_mag,
       round(p.confidence::numeric,3)       AS confidence,
       p.horizon,
       p.decision_at,
       vs.actual_direction,
       vs.actual_magnitude,
       vs.is_correct,
       round(vs.score::numeric,3)           AS score,
       round(vs.actual_return::numeric,5)   AS actual_return,
       ve.status                            AS eval_status
FROM   prediction.predictions p
LEFT   JOIN verification.scores      vs ON vs.prediction_id = p.prediction_id
LEFT   JOIN verification.evaluations ve ON ve.prediction_id = p.prediction_id
WHERE  p.decision_at::date = '$Date'::date
ORDER  BY p.decision_at;
"@
$predSummary = Invoke-PsqlCsv @"
SELECT count(*)                                         AS predictions_made,
       count(vs.prediction_id)                          AS scored,
       count(*) FILTER (WHERE vs.is_correct = true)     AS correct,
       count(*) FILTER (WHERE vs.is_correct = false)    AS wrong,
       count(*) FILTER (WHERE vs.prediction_id IS NULL) AS not_yet_scored
FROM   prediction.predictions p
LEFT   JOIN verification.scores vs ON vs.prediction_id = p.prediction_id
WHERE  p.decision_at::date = '$Date'::date;
"@
$predCols  = @("prediction_id","asset_id","predicted_dir","predicted_mag","confidence",
               "horizon","decision_at","actual_direction","actual_magnitude","is_correct",
               "score","actual_return","eval_status")
$predTable = ConvertTo-HtmlTable -Rows $predRows -Columns $predCols `
             -EmptyText "No predictions made on $Date."
$ps      = @($predSummary)[0]
$pMade   = if ($ps) { $ps.predictions_made }  else { "0" }
$pScored = if ($ps) { $ps.scored }            else { "0" }
$pCorr   = if ($ps) { $ps.correct }           else { "0" }
$pWrong  = if ($ps) { $ps.wrong }             else { "0" }
$pPend   = if ($ps) { $ps.not_yet_scored }    else { "0" }

# ---------------------------------------------------------------------------
# 4. Verification issues
# ---------------------------------------------------------------------------
Write-Host "  [4/7] Verification issues..." -ForegroundColor Gray
$verifyIssues = Invoke-PsqlCsv @"
SELECT ve.prediction_id,
       ve.asset_id,
       ve.status,
       ve.baseline_session,
       ve.settlement_session,
       ve.created_at,
       ve.updated_at,
       po.baseline::text   AS baseline_obs,
       po.settlement::text AS settlement_obs
FROM   verification.evaluations ve
LEFT   JOIN verification.price_observations po ON po.prediction_id = ve.prediction_id
WHERE  ve.created_at::date = '$Date'::date
  AND  ve.status <> 'SCORED'
ORDER  BY ve.created_at;
"@
$verifyCols  = @("prediction_id","asset_id","status","baseline_session","settlement_session",
                 "created_at","updated_at","baseline_obs","settlement_obs")
$verifyTable = ConvertTo-HtmlTable -Rows $verifyIssues -Columns $verifyCols `
               -EmptyText "No verification issues on $Date - all evaluations scored."

# ---------------------------------------------------------------------------
# 5. Market-data issues
# ---------------------------------------------------------------------------
Write-Host "  [5/7] Market-data issues..." -ForegroundColor Gray
$mdIssues = Invoke-PsqlCsv @"
SELECT pr.request_id,
       pr.asset_id,
       pr.state,
       pr.baseline_session,
       pr.settlement_session,
       pr.attempts,
       pr.last_error,
       pr.created_at,
       pr.updated_at,
       co_base.close AS baseline_close,
       co_sett.close AS settlement_close
FROM   market_data.price_requests pr
LEFT   JOIN market_data.close_observations co_base
         ON co_base.asset_id = pr.asset_id
        AND co_base.session  = pr.baseline_session
LEFT   JOIN market_data.close_observations co_sett
         ON co_sett.asset_id = pr.asset_id
        AND co_sett.session  = pr.settlement_session
WHERE  pr.created_at::date = '$Date'::date
  AND  (pr.state <> 'DONE'
        OR co_base.close IS NULL
        OR co_sett.close IS NULL)
ORDER  BY pr.created_at;
"@
$mdCols  = @("request_id","asset_id","state","baseline_session","settlement_session",
             "attempts","last_error","created_at","updated_at","baseline_close","settlement_close")
$mdTable = ConvertTo-HtmlTable -Rows $mdIssues -Columns $mdCols `
           -EmptyText "No market-data issues on $Date - all price requests completed."

# ---------------------------------------------------------------------------
# 6. Credibility issues
# ---------------------------------------------------------------------------
Write-Host "  [6/7] Credibility scores..." -ForegroundColor Gray
$credIssues = Invoke-PsqlCsv @"
SELECT entity_id,
       entity_type,
       round(credibility_score::numeric, 4) AS score,
       round(alpha::numeric, 2)             AS alpha,
       round(beta::numeric, 2)              AS beta,
       last_updated
FROM   credibility.credibility
WHERE  credibility_score < 0.40
ORDER  BY credibility_score;
"@
$credCols  = @("entity_id","entity_type","score","alpha","beta","last_updated")
$credTable = ConvertTo-HtmlTable -Rows $credIssues -Columns $credCols `
             -EmptyText "No credibility scores below 0.40 - all sources and assets look healthy."

$credTotalRows = Invoke-PsqlCsv "SELECT count(*) AS total FROM credibility.credibility;"
$credTotalN    = if (@($credTotalRows)[0]) { @($credTotalRows)[0].total } else { "0" }

# ---------------------------------------------------------------------------
# 7. KG updates today (offline learner)
# ---------------------------------------------------------------------------
Write-Host "  [7/7] KG edge updates (offline learner)..." -ForegroundColor Gray
$kgRows  = @()
$kgError = ""
try {
    $kgRows = Invoke-CypherCsv @"
MATCH (cf:CausalFactor)-[r:CAUSES]->(target)
WHERE date(r.last_updated) = date('${Date}')
RETURN cf.id AS factor,
       labels(target)[0] AS target_type,
       CASE WHEN target:Asset THEN target.id ELSE target.name END AS target,
       r.direction AS direction,
       round(toFloat(r.weight),3)     AS weight,
       round(toFloat(r.confidence),3) AS confidence,
       round(toFloat(r.alpha),2)      AS alpha,
       round(toFloat(r.beta),2)       AS beta,
       toString(r.last_updated)       AS last_updated
ORDER BY r.last_updated DESC;
"@
} catch {
    $kgError = $_.Exception.Message
}
$kgCols  = @("factor","target_type","target","direction","weight","confidence","alpha","beta","last_updated")
if ($kgError) {
    $kgTable = "<p class='warn'>Neo4j query failed: $([System.Web.HttpUtility]::HtmlEncode($kgError))</p>"
} else {
    $kgTable = ConvertTo-HtmlTable -Rows $kgRows -Columns $kgCols `
               -EmptyText "No KG edge updates on $Date - offline learner did not run or found no new data."
}

# ---------------------------------------------------------------------------
# Pre-build status badges and warning snippets (avoids complex expressions inside here-string)
# ---------------------------------------------------------------------------
$gapFlag    = if ([int]$gMissing -gt 0)         { "WARN" } else { "OK" }
$otherFlag  = if (@($otherRows).Count -gt 0)    { "WARN" } else { "OK" }
$verifyFlag = if (@($verifyIssues).Count -gt 0) { "WARN" } else { "OK" }
$mdFlag     = if (@($mdIssues).Count -gt 0)     { "WARN" } else { "OK" }
$credFlag   = if (@($credIssues).Count -gt 0)   { "WARN" } else { "OK" }
$kgFlag     = if (@($kgRows).Count -gt 0)       { "OK" }   else { "INFO" }

$badgeGap    = Get-Badge $gapFlag
$badgeOther  = Get-Badge $otherFlag
$badgePred   = Get-Badge "OK"
$badgeVerify = Get-Badge $verifyFlag
$badgeMd     = Get-Badge $mdFlag
$badgeCred   = Get-Badge $credFlag
$badgeKg     = Get-Badge $kgFlag

$otherCount   = @($otherRows).Count
$verifyCount  = @($verifyIssues).Count
$mdCount      = @($mdIssues).Count
$credLowCount = @($credIssues).Count
$kgCount      = @($kgRows).Count

$gapWarn    = if ([int]$gMissing -gt 0)   { "&nbsp;&mdash;&nbsp;<span style='color:#b02020'>Articles ingested but not cleansed. Check the cleansing service logs.</span>" } else { "" }
$otherWarn  = if ($otherCount -gt 0)      { "&nbsp;&mdash;&nbsp;<span style='color:#8a6000'>Review these - the taxonomy may be missing keywords.</span>" } else { "" }
$verifyWarn = if ($verifyCount -gt 0)     { "&nbsp;&mdash;&nbsp;<span style='color:#b02020'>Check verification logs. Possible cause: market-data not published, or settlement session not complete.</span>" } else { "" }
$mdWarn     = if ($mdCount -gt 0)         { "&nbsp;&mdash;&nbsp;<span style='color:#b02020'>Check last_error column and market-data service logs.</span>" } else { "" }
$credWarn   = if ($credLowCount -gt 0)    { "&nbsp;&mdash;&nbsp;<span style='color:#8a6000'>These sources or assets have low credibility. Their predictions carry less weight.</span>" } else { "" }
$kgInfo     = if ($kgCount -gt 0)         { "&nbsp;&mdash;&nbsp;<span style='color:#1a7f37'>Offline learner ran and updated these causal edges.</span>" } else { "&nbsp;&mdash;&nbsp;<span style='color:#777'>Offline learner has not updated any edges today.</span>" }

# ---------------------------------------------------------------------------
# Assemble HTML
# ---------------------------------------------------------------------------
$html = @"
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Feed Analyzer debug $Date</title>
<style>
  body { font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #1a1a1a; background: #f4f6fa; }
  h1   { font-size: 21px; margin-bottom: 4px; }
  h2   { font-size: 15px; margin-top: 36px; border-bottom: 2px solid #3b73c8; padding-bottom: 5px; color: #3b73c8; }
  .meta { color: #777; font-size: 12px; margin-bottom: 12px; }
  .summary { background: #eaf1fb; border: 1px solid #cfe0f5; border-radius: 6px;
             padding: 10px 16px; margin: 10px 0; font-size: 13px; }
  .summary b { color: #2a5ca8; }
  table { border-collapse: collapse; width: 100%; background: #fff; font-size: 12px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); margin-top: 6px; }
  th, td { border: 1px solid #d4dde8; padding: 5px 9px; text-align: left; vertical-align: top; }
  th { background: #3b73c8; color: #fff; position: sticky; top: 0; }
  tr:nth-child(even) td { background: #f0f5fc; }
  td.ok-cell   { color: #1a7f37; font-weight: 600; }
  td.bad-cell  { color: #b02020; font-weight: 600; }
  td.warn-cell { color: #8a6000; font-weight: 600; }
  p.ok   { color: #1a7f37; font-style: italic; margin: 4px 0; }
  p.warn { color: #b02020; font-style: italic; margin: 4px 0; }
  .badge { display:inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px;
           font-weight: 700; margin-left: 8px; vertical-align: middle; }
  .ok-badge   { background: #d0f0db; color: #1a7f37; }
  .warn-badge { background: #fde8e8; color: #b02020; }
  .info-badge { background: #e8f0fe; color: #3b73c8; }
  .toc { margin: 12px 0 20px 0; padding: 10px 16px; background: #fff;
         border: 1px solid #d4dde8; border-radius: 6px; font-size: 13px; }
  .toc li { margin: 4px 0; }
</style>
</head>
<body>
<h1>Feed Analyzer &mdash; pipeline debug report</h1>
<div class="meta">Date: <b>$Date</b> &nbsp;|&nbsp; db=$pgDb user=$pgUser &nbsp;|&nbsp; times are UTC</div>

<div class="toc">
  <b>Sections:</b>
  <ul>
    <li><a href="#gap">1. Ingestion &rarr; Cleansing gap</a> $badgeGap</li>
    <li><a href="#other">2. Articles classified as OTHER</a> $badgeOther</li>
    <li><a href="#pred">3. Predictions &amp; verification results</a> $badgePred</li>
    <li><a href="#vf">4. Verification issues</a> $badgeVerify</li>
    <li><a href="#md">5. Market-data issues</a> $badgeMd</li>
    <li><a href="#cred">6. Credibility issues</a> $badgeCred</li>
    <li><a href="#kg">7. KG updates (offline learner)</a> $badgeKg</li>
  </ul>
</div>

<h2 id="gap">1. Ingestion &rarr; Cleansing gap</h2>
<div class="summary">
  Ingested: <b>$gIngested</b> &nbsp;|&nbsp;
  Cleansed: <b>$gCleansed</b> &nbsp;|&nbsp;
  Missing in cleansing: <b>$gMissing</b> $gapWarn
</div>
$gapTable

<h2 id="other">2. Articles classified as OTHER</h2>
<div class="summary">Count: <b>$otherCount</b> $otherWarn</div>
$otherTable

<h2 id="pred">3. Predictions &amp; verification results</h2>
<div class="summary">
  Made: <b>$pMade</b> &nbsp;|&nbsp;
  Scored: <b>$pScored</b> &nbsp;|&nbsp;
  Correct: <b>$pCorr</b> &nbsp;|&nbsp;
  Wrong: <b>$pWrong</b> &nbsp;|&nbsp;
  Pending score: <b>$pPend</b>
</div>
$predTable

<h2 id="vf">4. Verification issues</h2>
<div class="summary">Evaluations not SCORED: <b>$verifyCount</b> $verifyWarn</div>
$verifyTable

<h2 id="md">5. Market-data issues</h2>
<div class="summary">Price requests with issues: <b>$mdCount</b> $mdWarn</div>
$mdTable

<h2 id="cred">6. Credibility issues (score &lt; 0.40)</h2>
<div class="summary">
  Total tracked entities: <b>$credTotalN</b> &nbsp;|&nbsp;
  Low-credibility entities: <b>$credLowCount</b> $credWarn
</div>
$credTable

<h2 id="kg">7. KG edge updates today (offline learner)</h2>
<div class="summary">Edges updated on ${Date}: <b>$kgCount</b> $kgInfo</div>
$kgTable

</body>
</html>
"@

$outFile = Join-Path $PSScriptRoot "debug-pipeline-$Date.html"
$html | Out-File -FilePath $outFile -Encoding utf8

Write-Host ""
Write-Host "Report written: $outFile" -ForegroundColor Green
$c1 = if ([int]$gMissing -gt 0) { "Yellow" } else { "Green" }
$c2 = if ($otherCount -gt 0)    { "Yellow" } else { "Green" }
$c3 = if ($verifyCount -gt 0)   { "Yellow" } else { "Green" }
$c4 = if ($mdCount -gt 0)       { "Yellow" } else { "Green" }
$c5 = if ($credLowCount -gt 0)  { "Yellow" } else { "Green" }
$c6 = if ($kgCount -gt 0)       { "Green" }  else { "Gray" }
Write-Host "  [1] Gap (ingestion vs cleansing) : $gMissing missing" -ForegroundColor $c1
Write-Host "  [2] OTHER-classified articles    : $otherCount" -ForegroundColor $c2
Write-Host "  [3] Predictions made             : $pMade  (scored: $pScored  correct: $pCorr  wrong: $pWrong  pending: $pPend)"
Write-Host "  [4] Verification issues          : $verifyCount" -ForegroundColor $c3
Write-Host "  [5] Market-data issues           : $mdCount" -ForegroundColor $c4
Write-Host "  [6] Low-credibility entities     : $credLowCount" -ForegroundColor $c5
Write-Host "  [7] KG edges updated today       : $kgCount" -ForegroundColor $c6
