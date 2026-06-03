# Adds D: (or C:) Docker bin to the current user's PATH permanently
$dockerBin = @(
    "D:\Docker\Docker\resources\bin",
    "C:\Program Files\Docker\Docker\resources\bin"
) | Where-Object { Test-Path (Join-Path $_ "docker.exe") } | Select-Object -First 1

if (-not $dockerBin) {
    Write-Error "docker.exe not found under D:\Docker or C:\Program Files\Docker"
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$segments = $userPath -split ';' | Where-Object { $_ -and $_.Trim() }

# Remove stale C:\ path if D:\ install exists and C:\ docker.exe is gone
$stale = "C:\Program Files\Docker\Docker\resources\bin"
if ($dockerBin -like "D:*" -and ($segments -contains $stale) -and -not (Test-Path "$stale\docker.exe")) {
    $segments = $segments | Where-Object { $_ -ne $stale }
    Write-Host "Removed stale PATH entry: $stale"
}

if ($segments -notcontains $dockerBin) {
    $segments += $dockerBin
    [Environment]::SetEnvironmentVariable("Path", ($segments -join ';'), "User")
    Write-Host "Added to user PATH: $dockerBin"
} else {
    Write-Host "PATH already contains: $dockerBin"
}

$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
Write-Host ""
Write-Host "Close and reopen PowerShell / Cursor terminal, then run: docker version" -ForegroundColor Green
