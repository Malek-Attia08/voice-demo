# One-time setup for a run WITHOUT Docker for the API and the worker:
#   - .env from .env.example (if there is none yet) with a fresh master key and secret
#   - one virtual environment for the API (.venv-api) and one for the worker (.venv-worker)
# Two environments on purpose: LiveKit Agents pins its own library versions, the API has its own.
. "$PSScriptRoot\_common.ps1"
Set-Location $Root

$env = Join-Path $Root ".env"
if (-not (Test-Path $env)) {
    Copy-Item (Join-Path $Root ".env.example") $env
    $secret = -join ((1..32) | ForEach-Object { "{0:x2}" -f (Get-Random -Maximum 256) })
    (Get-Content $env) -replace "^DEMO_SECRET=.*$", "DEMO_SECRET=$secret" | Set-Content $env -Encoding utf8
    Write-Host ".env made from .env.example."
}

if (-not (Select-String -Path $env -Pattern "^VOICE_MASTER_KEYS=.+" -Quiet)) {
    # The key is written into .env and never printed.
    & python -m api.make_master_key $env
}

foreach ($pair in @(@(".venv-api", "api\requirements.txt"), @(".venv-worker", "worker\requirements.txt"))) {
    $venv, $requirements = $pair
    if (-not (Test-Path (Join-Path $Root $venv))) { & python -m venv $venv }
    & (Join-Path $Root "$venv\Scripts\python.exe") -m pip install -q -r $requirements
    Write-Host "$venv ready."
}

Write-Host ""
Write-Host "Next: put GOOGLE_API_KEY (and MISTRAL_API_KEY for cloning) in .env, then see README.md 'Run it'."
