# Starts the whole demo with one command:  .\scripts\start.ps1
#   1. first run only: setup (.env, master key, the two virtual environments)
#   2. Docker Desktop, if it is not running (LiveKit's server needs it)
#   3. LiveKit (Docker), the API and the worker (Python, in the background, logs in data\logs\)
#   4. waits until the API answers, then opens http://localhost:8000
# Stop it with  .\scripts\stop.ps1
. "$PSScriptRoot\_common.ps1"
Set-Location $Root

$logs = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$pidFile = Join-Path $Root "data\pids.json"
if (Test-Path $pidFile) { throw "The demo seems to be running already (data\pids.json exists). Run .\scripts\stop.ps1 first." }

# 1. Setup, once.
$needsSetup = -not (Test-Path (Join-Path $Root ".venv-api")) -or -not (Test-Path (Join-Path $Root ".venv-worker")) `
    -or -not (Test-Path (Join-Path $Root ".env"))
if ($needsSetup) { & "$PSScriptRoot\setup.ps1" }
Import-DotEnv

if (-not $env:GOOGLE_API_KEY) {
    throw "GOOGLE_API_KEY is empty in .env. Put your Gemini key there and run start.ps1 again."
}
if (-not $env:MISTRAL_API_KEY) {
    Write-Host "Note: MISTRAL_API_KEY is empty, so cloning is off (Gemini's own voice still works)."
}

# 2. Docker Desktop.
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $desktop = @("C:\Program Files\Docker\Docker\Docker Desktop.exe",
                 (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe")) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $desktop) { throw "Docker is not running and Docker Desktop was not found. Start Docker, then run this again." }
    Write-Host "Starting Docker Desktop (this can take a minute)..."
    Start-Process $desktop
    $deadline = (Get-Date).AddSeconds(180)
    do { Start-Sleep 3; docker info *> $null } while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $deadline)
    if ($LASTEXITCODE -ne 0) { throw "Docker did not start within 3 minutes." }
}

# 3. LiveKit, the API, the worker.
Write-Host "Starting LiveKit..."
docker compose up -d livekit
if ($LASTEXITCODE -ne 0) { throw "LiveKit could not start (see the message above)." }

function Start-Background([string]$Name, [string[]]$Arguments, [string]$Python) {
    $process = Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs "$Name.out.log") -RedirectStandardError (Join-Path $logs "$Name.err.log")
    return $process.Id
}

Write-Host "Starting the API..."
$apiPid = Start-Background "api" @("-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000") `
    (Join-Path $Root ".venv-api\Scripts\python.exe")

# The worker holds the Google key only: it never gets the Mistral key or the master key.
Remove-Item Env:MISTRAL_API_KEY, Env:VOICE_MASTER_KEYS, Env:DEMO_SECRET -ErrorAction SilentlyContinue
Write-Host "Starting the worker..."
$workerPid = Start-Background "worker" @("-m", "worker.agent", "start") (Join-Path $Root ".venv-worker\Scripts\python.exe")

@{ api = $apiPid; worker = $workerPid } | ConvertTo-Json | Set-Content $pidFile

# 4. Wait for the API, open the page.
$up = $false
for ($i = 0; $i -lt 30 -and -not $up; $i++) {
    Start-Sleep 1
    try { $up = (Invoke-RestMethod "http://localhost:8000/api/health").ok } catch { }
}
if (-not $up) {
    Write-Host "The API did not answer in 30 seconds. See data\logs\api.err.log"
    exit 1
}
Write-Host ""
Write-Host "CareVoice is running: http://localhost:8000"
Write-Host "Logs: data\logs\    Stop: .\scripts\stop.ps1"
Start-Process "http://localhost:8000"
