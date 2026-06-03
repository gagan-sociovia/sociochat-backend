# Local backend (no Docker): creates venv and installs requirements
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

if (-not (Test-Path "venv")) {
    python -m venv venv
}
.\venv\Scripts\pip.exe install --upgrade pip
.\venv\Scripts\pip.exe install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

Write-Host ""
Write-Host "Run backend:" -ForegroundColor Green
Write-Host "  cd $Root"
Write-Host "  .\venv\Scripts\Activate.ps1"
Write-Host "  python app.py"
Write-Host "  -> http://localhost:5000"
