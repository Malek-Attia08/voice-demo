# The voice worker, without Docker. It joins LiveKit and waits to be dispatched to a call's room.
. "$PSScriptRoot\_common.ps1"
Set-Location $Root
Import-DotEnv
# The worker holds the Google key only: it never gets the Mistral key or the master key.
Remove-Item Env:MISTRAL_API_KEY, Env:VOICE_MASTER_KEYS, Env:DEMO_SECRET -ErrorAction SilentlyContinue
& (Join-Path $Root ".venv-worker\Scripts\python.exe") -m worker.agent start
