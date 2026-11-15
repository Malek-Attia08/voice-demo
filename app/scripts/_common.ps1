# Shared by the scripts in this folder. Dot-source it:  . "$PSScriptRoot\_common.ps1"
# "Continue", not "Stop": Windows PowerShell 5.1 turns anything a program writes to stderr (log lines, pip warnings)
# into an error under "Stop". The scripts check exit codes themselves, and `throw` still stops them.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot

# Reads .env (KEY=VALUE lines; blank lines and # comments are skipped) into this process's environment.
# A value already set in the real environment wins, like docker compose does.
function Import-DotEnv([string]$Path = (Join-Path $Root ".env")) {
    if (-not (Test-Path $Path)) {
        throw ".env not found at $Path. Run scripts\setup.ps1 first (it copies .env.example)."
    }
    foreach ($line in Get-Content $Path) {
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith("#")) { continue }
        $name, $value = $text -split "=", 2
        $name = $name.Trim()
        if ($null -eq $value) { continue }
        if (-not [Environment]::GetEnvironmentVariable($name)) {
            [Environment]::SetEnvironmentVariable($name, $value.Trim(), "Process")
        }
    }
}
