param(
    [string]$Python = "python"
)

& "$PSScriptRoot\release.ps1" -Python $Python
if (-not $?) { exit 1 }
