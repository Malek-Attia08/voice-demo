# LiveKit alone, in Docker (the only part that needs Docker when the API and the worker run from the venvs).
# Docker Desktop must be running.
. "$PSScriptRoot\_common.ps1"
Set-Location $Root
docker compose up livekit
