<#
.SYNOPSIS
    Export predictions with their contributing news articles to a JSON file for
    LLM audit of whether each prediction is justified by its news.

.DESCRIPTION
    Reads credentials from infra/.env, queries Postgres via docker exec, and writes a JSON
    file grouped by prediction. Each entry contains only what an LLM needs to judge
    whether the prediction is logically supported by the news that triggered it:

      - prediction_id      : key to reference a prediction in the LLM reply
      - asset_id           : the financial asset (e.g. GOLD, AAPL, OIL)
      - predicted_dir      : UP / DOWN / NEUTRAL
      - predicted_mag      : SMALL / MEDIUM / LARGE
      - confidence         : 0.0 - 1.0
      - horizon            : prediction window (e.g. ONE_TRADING_DAY)
      - decision_at        : UTC timestamp the prediction was made
      - eval_status        : SCORED / NOT_EVALUATED / PENDING / ERROR
      - verification       : correct / wrong / pending (from verification.scores)
      - rationale          : the system's own explanation for the prediction
      - contributing_news[]: article_id (key), title, body excerpt, source, published_at

    The llm_instructions field at the top tells the LLM exactly what to check and
    what format to reply in (one verdict per prediction_id).

.PARAMETER Date
    The day to export, formatted yyyy-MM-dd (filtered on prediction.decision_at UTC).

.PARAMETER OutputPath
    Optional output file path. Defaults to scripts\prediction-audit-<Date>.json.

.PARAMETER BodyLength
    Max characters of article body included per news item (default 500).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\export-prediction-audit.ps1 -Date 2026-08-13
    powershell -ExecutionPolicy Bypass -File scripts\export-prediction-audit.ps1 -Date 2026-08-13 -OutputPath C:\tmp\pred-audit.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, HelpMessage = "Day to export, formatted yyyy-MM-dd")]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Date,

    [Parameter(Mandatory = $false)]
    [string]$OutputPath,

    [int]$BodyLength = 500
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

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

Write-Host "Feed Analyzer - building prediction audit for $Date ..." -ForegroundColor Green

# --- Query 1: predictions + verification (one row per prediction) ---------------------------
Write-Host "  [1/2] Querying predictions and verification results..." -ForegroundColor Gray

$predRows = Invoke-PsqlCsv @"
SELECT p.prediction_id,
       p.asset_id,
       p.direction                            AS predicted_dir,
       p.magnitude                            AS predicted_mag,
       round(p.confidence::numeric, 3)        AS confidence,
       p.rationale,
       CASE
           WHEN vs.is_correct IS NULL THEN 'pending'
           WHEN vs.is_correct = true  THEN 'correct'
           ELSE 'wrong'
       END                                    AS verification
FROM   prediction.predictions p
LEFT   JOIN verification.scores vs ON vs.prediction_id = p.prediction_id
WHERE  p.decision_at::date = '$Date'::date
ORDER  BY p.decision_at;
"@

# --- Query 2: contributing raw news articles (one row per article per prediction) -----------
Write-Host "  [2/2] Querying contributing news articles..." -ForegroundColor Gray

$articleRows = Invoke-PsqlCsv @"
SELECT DISTINCT
       p.prediction_id,
       ia.article_id,
       left(ia.title, 200)                                                                   AS title,
       left(regexp_replace(COALESCE(ia.body, ''), E'[\\n\\r\\t]+', ' ', 'g'), $BodyLength)  AS body
FROM   prediction.predictions p
JOIN   prediction.contexts       ctx   ON ctx.context_id    = p.context_id
JOIN   prediction.context_events ce_ev ON ce_ev.context_id  = ctx.context_id
JOIN   cleansing.events          ce    ON ce.event_id       = ce_ev.event_id
JOIN   cleansing.event_clusters  ec    ON ec.cluster_id     = ce.cluster_id
JOIN   cleansing.cluster_articles ca   ON ca.cluster_id     = ec.cluster_id
JOIN   ingestion.articles         ia   ON ia.article_id     = ca.article_id
WHERE  p.decision_at::date = '$Date'::date
ORDER  BY p.prediction_id, ia.article_id;
"@

# --- Handle empty result --------------------------------------------------------------------
$out = if ($OutputPath) { $OutputPath } else { Join-Path $PSScriptRoot "prediction-audit-$Date.json" }

if (-not $predRows -or @($predRows).Count -eq 0) {
    Write-Host "No predictions found for $Date." -ForegroundColor Yellow
    [ordered]@{
        date             = $Date
        prediction_count = 0
        predictions      = @()
    } | ConvertTo-Json -Depth 5 | Out-File -FilePath $out -Encoding utf8
    Write-Host "Written to: $out" -ForegroundColor Green
    return
}

# --- Group articles by prediction_id --------------------------------------------------------
$articleMap = @{}
foreach ($row in $articleRows) {
    $key = $row.prediction_id
    if (-not $articleMap.ContainsKey($key)) { $articleMap[$key] = @() }
    $articleMap[$key] += [ordered]@{
        article_id = $row.article_id
        title      = $row.title
        body       = $row.body
    }
}

# --- Build prediction entries ---------------------------------------------------------------
$predictions = [System.Collections.Generic.List[object]]::new()

foreach ($pred in $predRows) {
    $key      = $pred.prediction_id
    # @() re-wraps to prevent PowerShell unwrapping single-element arrays on hashtable retrieval
    $articles = @(if ($articleMap.ContainsKey($key)) { $articleMap[$key] } else { @() })

    $predictions.Add([ordered]@{
        prediction_id           = $pred.prediction_id
        asset_id                = $pred.asset_id
        predicted_dir           = $pred.predicted_dir
        predicted_mag           = $pred.predicted_mag
        confidence              = $pred.confidence
        verification            = $pred.verification
        rationale               = if ($pred.rationale) { $pred.rationale } else { "" }
        contributing_news_count = $articles.Count
        contributing_news       = $articles
    })
}

# --- Collect summary stats ------------------------------------------------------------------
$totalPred = @($predRows).Count
$withNews  = @($predictions | Where-Object { $_.contributing_news_count -gt 0 }).Count
$correct   = @($predRows | Where-Object { $_.verification -eq "correct" }).Count
$wrong     = @($predRows | Where-Object { $_.verification -eq "wrong" }).Count
$pending   = @($predRows | Where-Object { $_.verification -eq "pending" }).Count

# --- Write output ---------------------------------------------------------------------------
[ordered]@{
    date             = $Date
    exported_at      = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    summary          = [ordered]@{
        prediction_count       = $totalPred
        with_contributing_news = $withNews
        without_news           = $totalPred - $withNews
        verified_correct       = $correct
        verified_wrong         = $wrong
        pending_verification   = $pending
    }
    llm_instructions = (
        "You are auditing whether each financial market prediction is logically justified by its contributing news articles. " +
        "A prediction is SUSPICIOUS when: (1) the news topic is unrelated to the asset (e.g. sports news triggering an oil stock prediction), " +
        "(2) the news sentiment clearly contradicts the predicted direction, or " +
        "(3) the rationale does not match the news content. " +
        "For EACH prediction entry below, reply with a JSON object: " +
        "{ ""prediction_id"": ""<id>"", ""asset_id"": ""<asset>"", " +
        """verdict"": ""ok"" | ""suspicious"" | ""unrelated_news"" | ""contradicts_news"", " +
        """confidence_in_verdict"": 0.0-1.0, " +
        """reason"": ""one sentence explanation"" }. " +
        "Return a JSON array of these objects, one per prediction_id. " +
        "If contributing_news is empty, set verdict to ""no_news"" and explain."
    )
    predictions      = @($predictions)
} | ConvertTo-Json -Depth 8 | Out-File -FilePath $out -Encoding utf8

Write-Host ""
Write-Host "Written to: $out" -ForegroundColor Green
Write-Host "  Predictions:        $totalPred"
Write-Host "  With linked news:   $withNews"
Write-Host "  Without news:       $($totalPred - $withNews)"
Write-Host "  Verification:       correct: $correct  wrong: $wrong  pending: $pending"
