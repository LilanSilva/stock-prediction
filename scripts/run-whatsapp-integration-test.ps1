<#
.SYNOPSIS
    Run the WhatsApp message-send integration tests against the Meta Cloud API.

.DESCRIPTION
    Sends real WhatsApp messages using credentials from infra/.env and recipients from
    src/services/notification/config/recipients/whatsapp_recipients.json.

    Tests run:
      - test_send_alert_message_reaches_whatsapp      (prediction alert)
      - test_send_verification_message_reaches_whatsapp (verification alert)

    Skipped automatically when META_ACCESS_TOKEN / META_PHONE_NUMBER_ID are missing
    or no recipients are configured.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run-whatsapp-integration-test.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment not found at $venvPython. Run scripts\setup-venv.ps1 first."
}

$testDir = Join-Path $repoRoot "src\services\notification"
if (-not (Test-Path $testDir)) {
    throw "Notification service directory not found at $testDir"
}

Write-Host "Running WhatsApp integration tests..." -ForegroundColor Cyan
Write-Host "  Test file : tests\test_whatsapp_integration.py" -ForegroundColor Gray
Write-Host "  Credentials from : infra\.env" -ForegroundColor Gray
Write-Host ""

Push-Location $testDir
try {
    & $venvPython -m pytest tests\test_whatsapp_integration.py -v -m integration
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Tests FAILED (exit code $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "All WhatsApp integration tests passed." -ForegroundColor Green
