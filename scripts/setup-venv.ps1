<#
.SYNOPSIS
    Create or refresh the shared root virtual environment (.venv) with pinned, hash-verified
    dependencies. Safe to re-run: it syncs an existing .venv to requirements.txt.

.DESCRIPTION
    Run from anywhere; the script resolves the repository root itself. Steps:
      1. Find a Python 3.12+ launcher.
      2. Create .venv at the repo root if missing.
      3. Upgrade pip.
      4. Install exact, checksum-verified dependencies from requirements.txt (--require-hashes).
      5. Install shared and all service packages editable (--no-deps; deps already verified).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "Repository root: $repoRoot"

# 1. Create the venv if it does not already exist.
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment at .venv ..."
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv .venv
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv .venv
    }
    else {
        throw "Python 3.12+ was not found. Install it and re-run this script."
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Creating the virtual environment failed (exit code $LASTEXITCODE)."
    }
}
else {
    Write-Host ".venv already exists; syncing dependencies."
}

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Expected interpreter not found at $venvPython"
}

& $venvPython -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "The virtual environment requires Python 3.12+."
}

function Invoke-Pip {
    param([Parameter(Mandatory)][string[]]$Arguments)
    & $venvPython -m pip @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "pip failed (exit code $LASTEXITCODE): $($Arguments -join ' ')"
    }
}

# 2-4. Upgrade pip, install hash-verified dependencies, install the local packages editable.
Invoke-Pip @("install", "--upgrade", "pip")
Invoke-Pip @("install", "--require-hashes", "-r", "requirements.txt")
foreach ($package in @("shared", "services\ingestion", "services\cleansing", "services\prediction", "services\market-data", "services\verification", "services\credibility", "services\notification")) {
    Invoke-Pip @("install", "-e", "src\$package", "--no-deps")
}
Invoke-Pip @("check")

Write-Host ""
Write-Host "Virtual environment ready."
Write-Host "In VS Code: Command Palette -> 'Python: Select Interpreter' -> .venv"
