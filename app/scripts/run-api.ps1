# The page and the API, without Docker, on http://localhost:8000 (the port LiveKit's webhook expects).
. "$PSScriptRoot\_common.ps1"
Set-Location $Root
Import-DotEnv
& (Join-Path $Root ".venv-api\Scripts\python.exe") -m uvicorn api.main:app --host 0.0.0.0 --port 8000
