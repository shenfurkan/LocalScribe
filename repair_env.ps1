param(
    [string]$InterpreterPath = "python",
    [switch]$AllowStorePython,
    [switch]$UseDotVenv,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not (Test-Path (Join-Path $ProjectRoot "requirements.txt"))) {
    Write-Err "requirements.txt not found. Run this script from LocalScribe root."
    exit 1
}

$envName = if ($UseDotVenv) { ".venv" } else { "venv" }
$envPath = Join-Path $ProjectRoot $envName

Write-Info "Project root: $ProjectRoot"
Write-Info "Target environment: $envName"

# Resolve interpreter and inspect executable path/version.
$pyInfoJson = & $InterpreterPath -c "import json, sys; print(json.dumps({'executable': sys.executable, 'version': sys.version.split()[0]}))" 2>$null
if (-not $pyInfoJson) {
    Write-Err "Could not execute interpreter '$InterpreterPath'."
    Write-Err "Install Python 3.11+ from python.org, then re-run this script."
    exit 1
}

$pyInfo = $pyInfoJson | ConvertFrom-Json
$resolvedExe = [string]$pyInfo.executable
$resolvedVersion = [string]$pyInfo.version

Write-Info "Using Python: $resolvedExe"
Write-Info "Version: $resolvedVersion"

if ($resolvedExe -like "*WindowsApps*") {
    Write-Warn "Windows Store alias Python detected: $resolvedExe"
    if (-not $AllowStorePython) {
        Write-Err "Store alias Python is blocked by default for reliability/security."
        Write-Err "Install official Python from python.org and run again, or pass -AllowStorePython to override."
        exit 1
    }
}

# Delete existing target environment safely.
if (Test-Path $envPath) {
    $doDelete = $false
    if ($Yes) {
        $doDelete = $true
    } else {
        $reply = Read-Host "Environment '$envName' already exists. Delete and recreate? (y/N)"
        if ($reply -match '^(y|yes)$') {
            $doDelete = $true
        }
    }

    if (-not $doDelete) {
        Write-Warn "Cancelled by user. No changes made."
        exit 1
    }

    Write-Info "Removing old environment: $envPath"
    Remove-Item -Recurse -Force $envPath
}

Write-Info "Creating virtual environment..."
& $resolvedExe -m venv $envPath

$venvPython = Join-Path $envPath "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Err "Virtual environment was created but interpreter was not found: $venvPython"
    exit 1
}

Write-Info "Upgrading pip..."
& $venvPython -m pip install --upgrade pip

Write-Info "Installing requirements..."
& $venvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")

Write-Info "Environment repair complete."
Write-Info "Run the app with: python run.py"
