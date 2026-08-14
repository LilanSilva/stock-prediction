<#
.SYNOPSIS
    For each prediction on a given date, show the raw news articles that drove it
    plus the verification outcome — all in one HTML report.

.DESCRIPTION
    Runs against the local Docker stack (infra/docker-compose.yml). Reads credentials
    from infra/.env, queries Postgres via `docker exec feed-postgres psql`.

    The report has one row per prediction. The "Contributing News" column lists
    every raw article (title + body snippet) that fed into that prediction as a
    bullet list. Verification status and result are shown in the same row.

    Join path used:
      prediction.predictions
        → prediction.contexts
        → prediction.context_events
        → cleansing.events
        → cleansing.event_clusters
        → cleansing.cluster_articles
        → ingestion.articles

.PARAMETER Date
    The day to report on, formatted yyyy-MM-dd (e.g. 2026-08-13).

.PARAMETER BodyLength
    Max characters of article body shown per news item (default 400).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\prediction-news-impact.ps1 -Date 2026-08-13
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, HelpMessage = "Day to report on, formatted yyyy-MM-dd")]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Date,

    [int]$BodyLength = 400
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

# ---------------------------------------------------------------------------
# Load credentials from infra/.env
# ---------------------------------------------------------------------------
$envPath = Join-Path $repoRoot "infra\.env"
if (-not (Test-Path $envPath)) {
    throw "Cannot find infra/.env at $envPath. Run from the repo root with the stack configured."
}

$envVars = @{}
foreach ($line in Get-Content $envPath) {
    $trimmed = $line.Trim()
    if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
    $idx = $trimmed.IndexOf("=")
    if ($idx -lt 1) { continue }
    $envVars[$trimmed.Substring(0, $idx).Trim()] = $trimmed.Substring($idx + 1).Trim()
}

$pgUser = if ($envVars["POSTGRES_USER"]) { $envVars["POSTGRES_USER"] } else { "feed_user" }
$pgPass = $envVars["POSTGRES_PASSWORD"]
$pgDb   = if ($envVars["POSTGRES_DB"])   { $envVars["POSTGRES_DB"] }   else { "feed" }

if (-not $pgPass) { throw "POSTGRES_PASSWORD is not set in infra/.env" }

Add-Type -AssemblyName System.Web

# ---------------------------------------------------------------------------
# Helper: run a SQL query and return rows as PSCustomObjects (via CSV)
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Query 1: predictions + verification (one row per prediction)
# ---------------------------------------------------------------------------
Write-Host "Feed Analyzer - prediction news impact report for $Date ..." -ForegroundColor Green
Write-Host "  [1/2] Querying predictions and verification results..." -ForegroundColor Gray

$predRows = Invoke-PsqlCsv @"
SELECT p.prediction_id,
       p.asset_id,
       p.direction                            AS predicted_dir,
       p.magnitude                            AS predicted_mag,
       round(p.confidence::numeric, 3)        AS confidence,
       p.horizon,
       p.decision_at,
       p.rationale,
       p.decision_method,
       vs.actual_direction,
       vs.actual_magnitude,
       vs.is_correct,
       round(vs.score::numeric, 3)            AS score,
       round(vs.actual_return::numeric, 5)    AS actual_return,
       COALESCE(ve.status, 'NOT_EVALUATED')   AS eval_status
FROM   prediction.predictions p
LEFT   JOIN verification.scores      vs ON vs.prediction_id = p.prediction_id
LEFT   JOIN verification.evaluations ve ON ve.prediction_id = p.prediction_id
WHERE  p.decision_at::date = '$Date'::date
ORDER  BY p.decision_at;
"@

# ---------------------------------------------------------------------------
# Query 2: contributing raw news articles (one row per article per prediction)
# ---------------------------------------------------------------------------
Write-Host "  [2/2] Querying contributing news articles..." -ForegroundColor Gray

$articleRows = Invoke-PsqlCsv @"
SELECT DISTINCT
       p.prediction_id,
       ia.article_id,
       ia.title,
       left(regexp_replace(COALESCE(ia.body, ''), E'[\\n\\r\\t]+', ' ', 'g'), $BodyLength) AS body_snippet,
       ia.source_id,
       ia.published_at
FROM   prediction.predictions p
JOIN   prediction.contexts      ctx   ON ctx.context_id   = p.context_id
JOIN   prediction.context_events ce_ev ON ce_ev.context_id = ctx.context_id
JOIN   cleansing.events          ce   ON ce.event_id      = ce_ev.event_id
JOIN   cleansing.event_clusters  ec   ON ec.cluster_id    = ce.cluster_id
JOIN   cleansing.cluster_articles ca  ON ca.cluster_id    = ec.cluster_id
JOIN   ingestion.articles         ia  ON ia.article_id    = ca.article_id
WHERE  p.decision_at::date = '$Date'::date
ORDER  BY p.prediction_id, ia.published_at;
"@

# ---------------------------------------------------------------------------
# Group articles by prediction_id for fast lookup
# ---------------------------------------------------------------------------
$articleMap = @{}
foreach ($row in $articleRows) {
    $predId = $row.prediction_id
    if (-not $articleMap.ContainsKey($predId)) { $articleMap[$predId] = [System.Collections.Generic.List[object]]::new() }
    $articleMap[$predId].Add($row)
}

# ---------------------------------------------------------------------------
# Build HTML table rows (one per prediction, news as bullet list)
# ---------------------------------------------------------------------------
function Format-VerificationCell {
    param([string]$IsCorrect, [string]$EvalStatus)
    if ($IsCorrect -eq "t")     { return "<td class='ok-cell'>correct</td>" }
    if ($IsCorrect -eq "f")     { return "<td class='bad-cell'>wrong</td>" }
    if ($EvalStatus -eq "NOT_EVALUATED") { return "<td class='muted'>not evaluated</td>" }
    return "<td class='warn-cell'>$([System.Web.HttpUtility]::HtmlEncode($EvalStatus))</td>"
}

function Format-EvalStatusCell {
    param([string]$Status)
    switch ($Status) {
        "SCORED"        { return "<td class='ok-cell'>SCORED</td>" }
        "NOT_EVALUATED" { return "<td class='muted'>-</td>" }
        "ERROR"         { return "<td class='bad-cell'>ERROR</td>" }
        default         { return "<td class='warn-cell'>$([System.Web.HttpUtility]::HtmlEncode($Status))</td>" }
    }
}

function Format-DirCell {
    param([string]$Dir)
    switch ($Dir) {
        "UP"      { return "<td class='dir-up'>&#9650; UP</td>" }
        "DOWN"    { return "<td class='dir-down'>&#9660; DOWN</td>" }
        "NEUTRAL" { return "<td class='muted'>&#9654; NEUTRAL</td>" }
        ""        { return "<td class='muted'>-</td>" }
        default   { return "<td>$([System.Web.HttpUtility]::HtmlEncode($Dir))</td>" }
    }
}

$sb = [System.Text.StringBuilder]::new()

$predCount    = @($predRows).Count
$withNewsCount = 0

foreach ($pred in $predRows) {
    $predId      = $pred.prediction_id
    $articles = if ($articleMap.ContainsKey($predId)) { $articleMap[$predId] } else { @() }
    if ($articles.Count -gt 0) { $withNewsCount++ }

    # --- News bullet list cell ---
    $newsCellContent = if ($articles.Count -eq 0) {
        "<span class='muted no-news'>No linked articles found for this prediction.</span>"
    } else {
        $listSb = [System.Text.StringBuilder]::new()
        [void]$listSb.Append("<ul class='news-list'>")
        foreach ($a in $articles) {
            $title  = [System.Web.HttpUtility]::HtmlEncode([string]$a.title)
            $body   = [System.Web.HttpUtility]::HtmlEncode([string]$a.body_snippet)
            $source = [System.Web.HttpUtility]::HtmlEncode([string]$a.source_id)
            $pubAt  = [System.Web.HttpUtility]::HtmlEncode([string]$a.published_at)
            [void]$listSb.Append("<li>")
            [void]$listSb.Append("<div class='news-title'>$title</div>")
            if ($body) {
                [void]$listSb.Append("<div class='news-body'>$body</div>")
            }
            [void]$listSb.Append("<div class='news-meta'>$source &nbsp;&middot;&nbsp; $pubAt</div>")
            [void]$listSb.Append("</li>")
        }
        [void]$listSb.Append("</ul>")
        $listSb.ToString()
    }

    $scoreVal      = if ($pred.score)            { $pred.score }            else { "-" }
    $actualReturn  = if ($pred.actual_return)  { $pred.actual_return }  else { "-" }
    $actualDir     = if ($pred.actual_direction) { $pred.actual_direction } else { "-" }
    $rationale     = if ($pred.rationale) { [System.Web.HttpUtility]::HtmlEncode($pred.rationale) } else { "" }

    [void]$sb.Append("<tr>")
    [void]$sb.Append("<td class='asset-id'>$([System.Web.HttpUtility]::HtmlEncode($pred.asset_id))</td>")
    [void]$sb.Append((Format-DirCell $pred.predicted_dir))
    [void]$sb.Append("<td>$([System.Web.HttpUtility]::HtmlEncode($pred.predicted_mag))</td>")
    [void]$sb.Append("<td>$([System.Web.HttpUtility]::HtmlEncode($pred.confidence))</td>")
    [void]$sb.Append("<td>$([System.Web.HttpUtility]::HtmlEncode($pred.horizon))</td>")
    [void]$sb.Append("<td class='ts'>$([System.Web.HttpUtility]::HtmlEncode($pred.decision_at))</td>")
    [void]$sb.Append((Format-EvalStatusCell $pred.eval_status))
    [void]$sb.Append((Format-VerificationCell $pred.is_correct $pred.eval_status))
    [void]$sb.Append("<td>$([System.Web.HttpUtility]::HtmlEncode($scoreVal))</td>")
    [void]$sb.Append("<td>$([System.Web.HttpUtility]::HtmlEncode($actualReturn))</td>")
    [void]$sb.Append("<td>$([System.Web.HttpUtility]::HtmlEncode($actualDir))</td>")
    if ($rationale) {
        [void]$sb.Append("<td class='rationale'>$rationale</td>")
    } else {
        [void]$sb.Append("<td class='muted'>-</td>")
    }
    [void]$sb.Append("<td class='news-cell'>$newsCellContent</td>")
    [void]$sb.Append("</tr>")
}

$tableBody = $sb.ToString()

$tableHtml = if ($predCount -eq 0) {
    "<p class='empty'>No predictions made on $Date.</p>"
} else {
    @"
<table>
  <thead>
    <tr>
      <th>Asset</th>
      <th>Predicted Dir</th>
      <th>Magnitude</th>
      <th>Confidence</th>
      <th>Horizon</th>
      <th>Decision At</th>
      <th>Eval Status</th>
      <th>Verification</th>
      <th>Score</th>
      <th>Actual Return</th>
      <th>Actual Dir</th>
      <th>Rationale</th>
      <th>Contributing News (title + body)</th>
    </tr>
  </thead>
  <tbody>
    $tableBody
  </tbody>
</table>
"@
}

# ---------------------------------------------------------------------------
# Summary counts
# ---------------------------------------------------------------------------
$totalPred = $predCount
$scored    = @($predRows | Where-Object { $_.eval_status -eq "SCORED" }).Count
$correct   = @($predRows | Where-Object { $_.is_correct -eq "t" }).Count
$wrong     = @($predRows | Where-Object { $_.is_correct -eq "f" }).Count
$pending   = $totalPred - $scored

# ---------------------------------------------------------------------------
# Assemble HTML
# ---------------------------------------------------------------------------
$html = @"
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Feed Analyzer &mdash; prediction news impact $Date</title>
<style>
  body {
    font-family: Segoe UI, Arial, sans-serif;
    margin: 24px;
    color: #1a1a1a;
    background: #f4f6fa;
  }
  h1 { font-size: 20px; margin-bottom: 2px; }
  .meta { color: #777; font-size: 12px; margin-bottom: 14px; }
  .summary {
    background: #eaf1fb;
    border: 1px solid #cfe0f5;
    border-radius: 6px;
    padding: 10px 16px;
    margin: 10px 0 16px 0;
    font-size: 13px;
  }
  .summary b { color: #2a5ca8; }
  table {
    border-collapse: collapse;
    width: 100%;
    background: #fff;
    font-size: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,.08);
  }
  th, td {
    border: 1px solid #d4dde8;
    padding: 6px 9px;
    text-align: left;
    vertical-align: top;
  }
  th {
    background: #2c6fbb;
    color: #fff;
    position: sticky;
    top: 0;
    white-space: nowrap;
  }
  tr:nth-child(even) td { background: #f0f5fc; }
  td.ok-cell    { color: #1a7f37; font-weight: 700; }
  td.bad-cell   { color: #b02020; font-weight: 700; }
  td.warn-cell  { color: #8a6000; font-weight: 600; }
  td.muted      { color: #999; }
  td.dir-up     { color: #1a7f37; font-weight: 700; }
  td.dir-down   { color: #b02020; font-weight: 700; }
  td.asset-id   { font-weight: 600; white-space: nowrap; }
  td.ts         { white-space: nowrap; font-size: 11px; color: #555; }
  td.rationale  { font-size: 11px; color: #444; max-width: 200px; }
  td.news-cell  { min-width: 340px; max-width: 540px; }
  .news-list {
    margin: 0;
    padding: 0 0 0 16px;
    list-style: disc;
  }
  .news-list li {
    margin-bottom: 10px;
    padding-bottom: 8px;
    border-bottom: 1px dotted #d4dde8;
  }
  .news-list li:last-child { border-bottom: none; margin-bottom: 0; }
  .news-title {
    font-weight: 600;
    font-size: 12px;
    color: #1a1a1a;
    margin-bottom: 3px;
  }
  .news-body {
    font-size: 11px;
    color: #444;
    line-height: 1.5;
    margin-bottom: 3px;
  }
  .news-meta {
    font-size: 10px;
    color: #888;
  }
  .no-news { font-style: italic; font-size: 11px; }
  p.empty { color: #888; font-style: italic; }
</style>
</head>
<body>
  <h1>Feed Analyzer &mdash; prediction news impact report</h1>
  <div class="meta">
    Date: <b>$Date</b> &nbsp;|&nbsp;
    db=$pgDb user=$pgUser &nbsp;|&nbsp;
    times are UTC &nbsp;|&nbsp;
    body truncated to $BodyLength chars
  </div>

  <div class="summary">
    Predictions: <b>$totalPred</b> &nbsp;|&nbsp;
    With linked news: <b>$withNewsCount</b> &nbsp;|&nbsp;
    Scored: <b>$scored</b> &nbsp;|&nbsp;
    Correct: <b>$correct</b> &nbsp;|&nbsp;
    Wrong: <b>$wrong</b> &nbsp;|&nbsp;
    Pending score: <b>$pending</b>
  </div>

  $tableHtml
</body>
</html>
"@

$outFile = Join-Path $PSScriptRoot "prediction-news-impact-$Date.html"
$html | Out-File -FilePath $outFile -Encoding utf8

Write-Host ""
Write-Host "Report written to: $outFile" -ForegroundColor Green
Write-Host "  Predictions:      $totalPred  (scored: $scored  correct: $correct  wrong: $wrong  pending: $pending)"
Write-Host "  With linked news: $withNewsCount / $totalPred"
