# Deploy SocioChat backend to Google Cloud Run
# Prerequisites: gcloud CLI authenticated (`gcloud auth login`)
#
# Usage:
#   .\scripts\deploy-gcp.ps1
#   .\scripts\deploy-gcp.ps1 -CopyEnvFromService "sociovia-backend"

param(
    [string]$ProjectId = $env:GCP_PROJECT_ID,
    [string]$Region = "europe-west1",
    [string]$ServiceName = "sociochat-backend-v2",
    [string]$CopyEnvFromService = "sociovia-backend",
    [switch]$SkipEnvUpdate,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$Gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
if (-not $Gcloud) {
    $GcloudPath = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    if (Test-Path $GcloudPath) { $Gcloud = $GcloudPath } else { throw "gcloud not found. Install Google Cloud SDK." }
}

if (-not $ProjectId) {
    $ProjectId = & $Gcloud config get-value project 2>$null
}
# Project ID (not project number). 362038465411 is only used in *.run.app hostnames.
if (-not $ProjectId -or $ProjectId -match '^\d+$') {
    $ProjectId = "angular-sorter-473216-k8"
}

Write-Host "Project:  $ProjectId" -ForegroundColor Cyan
Write-Host "Region:   $Region" -ForegroundColor Cyan
Write-Host "Service:  $ServiceName" -ForegroundColor Cyan

$AuthList = & $Gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>$null
if (-not $AuthList) {
    throw "No active gcloud account. Run: gcloud auth login"
}

& $Gcloud config set project $ProjectId | Out-Null
& $Gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project=$ProjectId

function Convert-CloudRunEnvToYaml {
    param([array]$EnvVars)
    $yaml = @()
    $SkipKeys = @("APP_BASE_URL", "WEBHOOK_PUBLIC_URL", "OAUTH_REDIRECT_BASE")
    foreach ($item in $EnvVars) {
        $key = $item.name
        $val = $item.value
        if (-not $key -or $key -in $SkipKeys) { continue }
        if ($val -match '^sqlite:') { continue }
        $escaped = $val -replace '\\', '\\\\' -replace '"', '\"'
        $yaml += "${key}: `"$escaped`""
    }
    return $yaml
}

$EnvYaml = Join-Path $Root ".env.cloudrun.yaml"
$yamlLines = @()

if ($CopyEnvFromService -and -not $SkipEnvUpdate) {
    Write-Host "Copying env vars from existing service: $CopyEnvFromService" -ForegroundColor Yellow
    $existingJson = & $Gcloud run services describe $CopyEnvFromService `
        --region=$Region --project=$ProjectId --format=json 2>$null
    if ($existingJson) {
        $existing = $existingJson | ConvertFrom-Json
        $envList = $existing.spec.template.spec.containers[0].env
        if ($envList) {
            $yamlLines = Convert-CloudRunEnvToYaml -EnvVars $envList
            Write-Host "  Copied $($yamlLines.Count) variables from $CopyEnvFromService" -ForegroundColor Green
        }
    } else {
        Write-Host "  Could not read $CopyEnvFromService - falling back to .env" -ForegroundColor DarkYellow
    }
}

if ($yamlLines.Count -eq 0 -and (Test-Path (Join-Path $Root ".env")) -and -not $SkipEnvUpdate) {
    $lines = Get-Content (Join-Path $Root ".env") | Where-Object {
        $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*\s*=' -and $_ -notmatch '^\s*#'
    }
    foreach ($line in $lines) {
        if ($line -match '^([^=]+)=(.*)$') {
            $key = $Matches[1].Trim()
            $val = $Matches[2].Trim().Trim('"').Trim("'")
            if ($val -match '^sqlite:') { continue }
            $escaped = $val -replace '\\', '\\\\' -replace '"', '\"'
            $yamlLines += "${key}: `"$escaped`""
        }
    }
}

if ($yamlLines.Count -gt 0 -and -not $SkipEnvUpdate) {
    $yamlLines | Set-Content -Encoding utf8 $EnvYaml
    Write-Host ("Wrote {0} ({1} env vars)" -f $EnvYaml, $yamlLines.Count) -ForegroundColor Green
}

if (-not $SkipBuild) {
    Write-Host "Submitting Cloud Build..." -ForegroundColor Yellow
    & $Gcloud builds submit `
        --config=cloudbuild.yaml `
        --project=$ProjectId `
        --substitutions="_SERVICE_NAME=$ServiceName,_REGION=$Region"
} else {
    Write-Host "Skipping Cloud Build (-SkipBuild)" -ForegroundColor DarkYellow
}

if ((Test-Path $EnvYaml) -and -not $SkipEnvUpdate) {
    Write-Host "Updating Cloud Run environment variables..." -ForegroundColor Yellow
    & $Gcloud run services update $ServiceName `
        --region=$Region `
        --project=$ProjectId `
        --env-vars-file=$EnvYaml
}

$Url = & $Gcloud run services describe $ServiceName --region=$Region --project=$ProjectId --format="value(status.url)" 2>$null
if ($Url) {
    Write-Host ""
    Write-Host "Deployed: $Url" -ForegroundColor Green

    $FrontendBase = $env:FRONTEND_ORIGIN
    if (-not $FrontendBase) { $FrontendBase = "https://sociovia.com" }

    & $Gcloud run services update $ServiceName `
        --region=$Region `
        --project=$ProjectId `
        --update-env-vars="APP_BASE_URL=$Url,WEBHOOK_PUBLIC_URL=$Url,OAUTH_REDIRECT_BASE=$Url,FRONTEND_BASE_URL=$FrontendBase"

    Write-Host ""
    & "$PSScriptRoot\meta-developer-urls.ps1" -BaseUrl $Url
}
