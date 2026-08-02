<#
.SYNOPSIS
    Report the pipeline's activity for a single day as an easy-to-read HTML file:
    news retrieved, and predictions with their verified results.

.DESCRIPTION
    Runs against the local Docker stack (infra/docker-compose.yml). It reads credentials from
    infra/.env, then queries Postgres via `docker exec feed-postgres psql` (CSV output), builds an
    HTML report, and writes it next to this script as daily-activity-<Date>.html.

    Sections (filtered to the given -Date, server time = UTC in the containers):
      1. News retrieved          -> ingestion.articles           (ingested_at)
      4/5. Predictions + results -> prediction.predictions LEFT JOIN verification.scores
                                    (decision_at). One combined table: unscored predictions show
                                    with empty result columns; scored ones show is_correct/score.

.PARAMETER Date
    The day to report on, formatted yyyy-MM-dd (e.g. 2026-07-31).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\query-daily-activity.ps1 -Date 2026-07-31
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, HelpMessage = "Day to report on, formatted yyyy-MM-dd")]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Date
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

# --- Load credentials from infra/.env -------------------------------------------------------------
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
    $envVars[$key] = $val
}

$pgUser = if ($envVars.ContainsKey("POSTGRES_USER")) { $envVars["POSTGRES_USER"] } else { "feed_user" }
$pgPass = $envVars["POSTGRES_PASSWORD"]
$pgDb   = if ($envVars.ContainsKey("POSTGRES_DB")) { $envVars["POSTGRES_DB"] } else { "feed" }

if (-not $pgPass) { throw "POSTGRES_PASSWORD is not set in infra/.env" }

# --- Helpers --------------------------------------------------------------------------------------
function Invoke-PsqlCsv {
    # Runs a query and returns rows as PowerShell objects (CSV column headers become properties).
    param([string]$Sql)
    $csv = & docker exec -e "PGPASSWORD=$pgPass" feed-postgres `
        psql -U $pgUser -d $pgDb -P pager=off --csv -c $Sql 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "psql query failed (is 'feed-postgres' running? 'docker compose -f infra/docker-compose.yml up -d'):`n$csv"
    }
    if (-not $csv -or @($csv).Count -eq 0) { return @() }
    # Force an array so single-row results still expose .Count reliably.
    return @($csv | ConvertFrom-Csv)
}

function ConvertTo-HtmlTable {
    # Renders an array of objects as an HTML <table>; shows a friendly note when empty.
    param(
        [object[]]$Rows,
        [string[]]$Columns,
        [string]$EmptyText = "No rows for this date."
    )
    if (-not $Rows -or $Rows.Count -eq 0) {
        return "<p class='empty'>$EmptyText</p>"
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
            # Highlight the correctness column when present.
            if ($col -eq "is_correct") {
                if ($value -eq "t") { $cls = " class='ok'"; $value = "correct" }
                elseif ($value -eq "f") { $cls = " class='bad'"; $value = "wrong" }
            }
            $encoded = [System.Web.HttpUtility]::HtmlEncode($value)
            [void]$sb.Append("<td$cls>$encoded</td>")
        }
        [void]$sb.Append("</tr>")
    }
    [void]$sb.Append("</tbody></table>")
    return $sb.ToString()
}

Add-Type -AssemblyName System.Web

Write-Host "Feed Analyzer - building daily activity report for $Date ..." -ForegroundColor Green

# --- 1. News retrieved ----------------------------------------------------------------------------
$newsCols = @("article_id", "source_id", "title", "language", "country", "published_at", "ingested_at")
$news = Invoke-PsqlCsv @"
SELECT article_id,
       source_id,
       left(title, 80) AS title,
       language,
       country,
       published_at,
       ingested_at
FROM ingestion.articles
WHERE ingested_at::date = '$Date'::date
ORDER BY ingested_at;
"@
$newsSummary = Invoke-PsqlCsv @"
SELECT count(*) AS total_retrieved,
       count(DISTINCT source_id) AS distinct_sources
FROM ingestion.articles
WHERE ingested_at::date = '$Date'::date;
"@

# --- 4 & 5. Predictions and their verified results (single table) ---------------------------------
$predCols = @("prediction_id", "asset_id", "predicted_dir", "predicted_mag", "confidence",
    "horizon", "decision_at", "actual_direction", "actual_magnitude", "is_correct",
    "score", "actual_return")
$preds = Invoke-PsqlCsv @"
SELECT p.prediction_id,
       p.asset_id,
       p.direction  AS predicted_dir,
       p.magnitude  AS predicted_mag,
       round(p.confidence::numeric, 3) AS confidence,
       p.horizon,
       p.decision_at,
       s.actual_direction,
       s.actual_magnitude,
       s.is_correct,
       round(s.score::numeric, 3)         AS score,
       round(s.actual_return::numeric, 5) AS actual_return
FROM prediction.predictions p
LEFT JOIN verification.scores s ON s.prediction_id = p.prediction_id
WHERE p.decision_at::date = '$Date'::date
ORDER BY p.decision_at;
"@
$predSummary = Invoke-PsqlCsv @"
SELECT count(*)                          AS predictions_made,
       count(s.prediction_id)            AS scored,
       count(*) FILTER (WHERE s.is_correct)       AS correct,
       count(*) FILTER (WHERE s.is_correct = false) AS wrong,
       count(*) FILTER (WHERE s.prediction_id IS NULL) AS not_yet_scored
FROM prediction.predictions p
LEFT JOIN verification.scores s ON s.prediction_id = p.prediction_id
WHERE p.decision_at::date = '$Date'::date;
"@

# --- Summary line values (safe defaults when a section is empty) ----------------------------------
# @(...) guards against PowerShell unwrapping a single-row result into a scalar.
$newsRow = @($newsSummary)[0]
$predRow = @($predSummary)[0]
$newsTotal   = if ($newsRow) { $newsRow.total_retrieved } else { "0" }
$newsSources = if ($newsRow) { $newsRow.distinct_sources } else { "0" }
$pMade   = if ($predRow) { $predRow.predictions_made } else { "0" }
$pScored = if ($predRow) { $predRow.scored } else { "0" }
$pCorr   = if ($predRow) { $predRow.correct } else { "0" }
$pWrong  = if ($predRow) { $predRow.wrong } else { "0" }
$pPend   = if ($predRow) { $predRow.not_yet_scored } else { "0" }

$newsTable = ConvertTo-HtmlTable -Rows $news  -Columns $newsCols -EmptyText "No news retrieved on $Date."
$predTable = ConvertTo-HtmlTable -Rows $preds -Columns $predCols -EmptyText "No predictions made on $Date."

# --- Assemble HTML --------------------------------------------------------------------------------
$html = @"
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Feed Analyzer - daily activity $Date</title>
<style>
  body { font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #1a1a1a; background: #f7f9fc; }
  h1 { font-size: 20px; margin-bottom: 2px; }
  h2 { font-size: 16px; margin-top: 32px; border-bottom: 2px solid #2c6fbb; padding-bottom: 4px; color: #2c6fbb; }
  .meta { color: #666; font-size: 12px; margin-bottom: 8px; }
  .summary { background: #eaf1fb; border: 1px solid #cfe0f5; border-radius: 6px; padding: 10px 14px; margin: 10px 0; font-size: 14px; }
  .summary b { color: #2c6fbb; }
  table { border-collapse: collapse; width: 100%; background: #fff; font-size: 13px; box-shadow: 0 1px 2px rgba(0,0,0,.06); }
  th, td { border: 1px solid #d9e2ec; padding: 6px 9px; text-align: left; vertical-align: top; }
  th { background: #2c6fbb; color: #fff; position: sticky; top: 0; }
  tr:nth-child(even) td { background: #f4f8fd; }
  td.ok  { color: #1a7f37; font-weight: 600; }
  td.bad { color: #c02626; font-weight: 600; }
  .empty { color: #888; font-style: italic; }
</style>
</head>
<body>
  <h1>Feed Analyzer &mdash; daily activity report</h1>
  <div class="meta">Date: <b>$Date</b> &nbsp;|&nbsp; Postgres db=$pgDb user=$pgUser &nbsp;|&nbsp; dates compared in container/UTC time</div>

  <h2>1. News retrieved (ingestion.articles)</h2>
  <div class="summary">Total retrieved: <b>$newsTotal</b> &nbsp;|&nbsp; distinct sources: <b>$newsSources</b></div>
  $newsTable

  <h2>4 &amp; 5. Predictions and results (prediction.predictions + verification.scores)</h2>
  <div class="summary">
    Predictions made: <b>$pMade</b> &nbsp;|&nbsp; scored: <b>$pScored</b> &nbsp;|&nbsp;
    correct: <b>$pCorr</b> &nbsp;|&nbsp; wrong: <b>$pWrong</b> &nbsp;|&nbsp; not yet scored: <b>$pPend</b>
  </div>
  $predTable
</body>
</html>
"@

$outFile = Join-Path $PSScriptRoot "daily-activity-$Date.html"
$html | Out-File -FilePath $outFile -Encoding utf8

Write-Host "Report written to: $outFile" -ForegroundColor Green
Write-Host "  News retrieved:   $newsTotal (sources: $newsSources)"
Write-Host "  Predictions:      $pMade made, $pScored scored ($pCorr correct / $pWrong wrong), $pPend pending"
