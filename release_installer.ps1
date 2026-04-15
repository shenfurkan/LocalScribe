param(
    [switch]$SkipBuild,
    [switch]$UseDotVenv
)

$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Err($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not (Test-Path (Join-Path $ProjectRoot "installer.iss"))) {
    Write-Err "installer.iss not found. Run this script from LocalScribe root."
    exit 1
}

$venvName = if ($UseDotVenv) { ".venv" } else { "venv" }
$venvPython = Join-Path $ProjectRoot "$venvName\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Err "Virtual env python not found: $venvPython"
    Write-Err "Create it first with: .\repair_env.ps1 -Yes"
    exit 1
}

$isccCandidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$iscc = $null
foreach ($p in $isccCandidates) {
    if (Test-Path $p) {
        $iscc = $p
        break
    }
}

if (-not $iscc) {
    Write-Err "Inno Setup compiler not found (ISCC.exe)."
    Write-Err "Install Inno Setup 6: https://jrsoftware.org/isdl.php"
    exit 1
}

Write-Info "Using Python: $venvPython"
Write-Info "Using ISCC: $iscc"

if (-not $SkipBuild) {
    Write-Info "Cleaning previous outputs..."
    if (Test-Path (Join-Path $ProjectRoot "build")) {
        Remove-Item -Recurse -Force (Join-Path $ProjectRoot "build")
    }
    if (Test-Path (Join-Path $ProjectRoot "dist")) {
        Remove-Item -Recurse -Force (Join-Path $ProjectRoot "dist")
    }

    Write-Info "Building app (PyInstaller)..."
    & $venvPython .\build.py
    if ($LASTEXITCODE -ne 0) {
        Write-Err "build.py failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
}

Write-Info "Compiling installer (installer.iss)..."
& $iscc .\installer.iss
if ($LASTEXITCODE -ne 0) {
    Write-Err "Installer compile failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

$setupPath = Join-Path $ProjectRoot "dist\LocalScribe_Setup.exe"
if (-not (Test-Path $setupPath)) {
    Write-Err "Compile finished but setup file was not found: $setupPath"
    exit 1
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "Release installer ready:" -ForegroundColor Green
Write-Host "  $setupPath" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
