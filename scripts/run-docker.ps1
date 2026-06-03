# Run SocioChat backend in Docker (works after Docker/WSL move to D: drive)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$dockerCandidates = @(
    "D:\Docker\Docker\resources\bin\docker.exe",
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
)
$docker = $dockerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $docker) {
    Write-Error "docker.exe not found. Install Docker Desktop or add its bin folder to PATH."
}

# Quick daemon check
& $docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker CLI found at: $docker" -ForegroundColor Yellow
    Write-Host "Docker daemon is not running. Start Docker Desktop, wait until it shows Running, then run this script again." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "Created .env from .env.example — review before production use."
    } else {
        Write-Error "Missing .env file. Copy .env.example to .env first."
    }
}

$compose = Join-Path (Split-Path $docker -Parent) "docker-compose.exe"
if (Test-Path $compose) {
    & $compose up --build
} else {
    & $docker build -t sociochat-backend .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $docker run --rm -p 8080:8080 --env-file .env -v sociochat-data:/app --name sociochat-api sociochat-backend
}
