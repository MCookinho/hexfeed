# hexfeed Windows installer
# Run: powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"

$HexfeedDir = "$env:LOCALAPPDATA\hexfeed"
$BinDir = "$env:LOCALAPPDATA\Programs\hexfeed"

Write-Host "  ⬡ Installing hexfeed on Windows..."

# Check Python
try {
    $pyVersion = python --version 2>&1
    Write-Host "  Found $pyVersion"
} catch {
    Write-Host "  ✗ Python not found. Install Python 3.11+ from https://python.org"
    exit 1
}

New-Item -ItemType Directory -Force -Path $HexfeedDir | Out-Null
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

# Create venv
if (-not (Test-Path "$HexfeedDir\.venv")) {
    Write-Host "  Creating virtual environment..."
    python -m venv "$HexfeedDir\.venv"
}

Write-Host "  Installing Python dependencies..."
& "$HexfeedDir\.venv\Scripts\pip" install --quiet --upgrade pip setuptools wheel
& "$HexfeedDir\.venv\Scripts\pip" install --quiet -e (Split-Path -Parent $PSScriptRoot)

# Create batch wrappers
@"
@echo off
"$HexfeedDir\.venv\Scripts\hexfeed" %*
"@ | Out-File -FilePath "$BinDir\hexfeed.cmd" -Encoding ascii

@"
@echo off
"$HexfeedDir\.venv\Scripts\hexfeed-server" %*
"@ | Out-File -FilePath "$BinDir\hexfeed-server.cmd" -Encoding ascii

Write-Host "  ✓ hexfeed installed!"
Write-Host
Write-Host "  Add $BinDir to your PATH environment variable."
Write-Host
