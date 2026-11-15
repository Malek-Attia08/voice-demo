# Stops what start.ps1 started: the API, the worker and the LiveKit container.
. "$PSScriptRoot\_common.ps1"
Set-Location $Root

$pidFile = Join-Path $Root "data\pids.json"
if (Test-Path $pidFile) {
    $pids = Get-Content $pidFile | ConvertFrom-Json
    foreach ($name in "api", "worker") {
        $id = $pids.$name
        # /T also stops the worker's child processes (one per call).
        if ($id) { taskkill /PID $id /T /F *> $null; Write-Host "Stopped $name." }
    }
    Remove-Item $pidFile
} else {
    Write-Host "No data\pids.json: the API and the worker were not started by start.ps1."
}

docker info *> $null
if ($LASTEXITCODE -eq 0) { docker compose stop livekit }
