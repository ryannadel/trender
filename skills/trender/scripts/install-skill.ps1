param(
    [string] $Destination = (Join-Path $HOME ".copilot\skills\trender"),
    [switch] $Force
)

$ErrorActionPreference = "Stop"

$SkillDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DestinationParent = Split-Path -Parent $Destination

if ((Test-Path $Destination) -and -not $Force) {
    throw "Destination already exists: $Destination. Re-run with -Force to replace it."
}

if (Test-Path $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $DestinationParent | Out-Null
Copy-Item -Path $SkillDir -Destination $Destination -Recurse -Force

Get-ChildItem -Path $Destination -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Get-ChildItem -Path $Destination -Recurse -File -Include "*.pyc", "*.pyo" |
    Remove-Item -Force

Write-Host "Installed Trender skill:"
Write-Host "  $Destination"
Write-Host ""
Write-Host "Restart or refresh your agent host if it does not detect new skills automatically."

