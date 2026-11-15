# Runs every test: the API's, then the worker's and the tools'. No network, no keys, no LiveKit server.
# Pass other Pythons with -ApiPython / -WorkerPython if the environments live elsewhere.
param(
    [string]$ApiPython = (Join-Path (Split-Path -Parent $PSScriptRoot) ".venv-api\Scripts\python.exe"),
    [string]$WorkerPython = (Join-Path (Split-Path -Parent $PSScriptRoot) ".venv-worker\Scripts\python.exe")
)
. "$PSScriptRoot\_common.ps1"
Set-Location $Root

& $ApiPython -m unittest discover -s api/tests -t .
$apiFailed = $LASTEXITCODE
& $WorkerPython -m unittest discover -s worker/tests -t .
$workerFailed = $LASTEXITCODE

if ($apiFailed -or $workerFailed) { Write-Host "FAILED: api=$apiFailed worker=$workerFailed"; exit 1 }
Write-Host "All tests passed."
