$ErrorActionPreference = "Stop"

$SkillDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Root = (Resolve-Path (Join-Path $SkillDir "..\..")).Path
$Dist = Join-Path $Root "dist"
$License = Join-Path $Root "LICENSE"
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

function Copy-SkillContent {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Destination
    )

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item (Join-Path $SkillDir "SKILL.md") $Destination -Force
    Copy-Item (Join-Path $SkillDir "README.md") $Destination -Force
    if (Test-Path $License) {
        Copy-Item $License $Destination -Force
    }
    Copy-Item (Join-Path $SkillDir "scripts") $Destination -Recurse -Force
    if (Test-Path (Join-Path $SkillDir "vendor")) {
        Copy-Item (Join-Path $SkillDir "vendor") $Destination -Recurse -Force
    }
    if (Test-Path (Join-Path $SkillDir "agents")) {
        Copy-Item (Join-Path $SkillDir "agents") $Destination -Recurse -Force
    }
    Get-ChildItem -Path $Destination -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force
    Get-ChildItem -Path $Destination -Recurse -File -Include "*.pyc", "*.pyo" |
        Remove-Item -Force
}

$Stage = Join-Path ([System.IO.Path]::GetTempPath()) ("trender-skill-" + [guid]::NewGuid().ToString("N"))
Copy-SkillContent -Destination $Stage

$TempArchive = Join-Path ([System.IO.Path]::GetTempPath()) ("trender.skill." + [guid]::NewGuid().ToString("N") + ".zip")
$SkillArchive = Join-Path $Dist "trender.skill"
if (Test-Path $SkillArchive) {
    Remove-Item $SkillArchive -Force
}

try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($Stage, $TempArchive)
    Copy-Item $TempArchive $SkillArchive -Force
    Write-Host "Built $SkillArchive"

    $PluginStage = Join-Path ([System.IO.Path]::GetTempPath()) ("trender-plugin-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $PluginStage | Out-Null
    Copy-Item (Join-Path $Root "plugin.json") $PluginStage -Force
    Copy-Item (Join-Path $Root ".claude-plugin") $PluginStage -Recurse -Force
    Copy-Item (Join-Path $Root ".codex-plugin") $PluginStage -Recurse -Force
    Copy-Item (Join-Path $Root "README.md") $PluginStage -Force
    if (Test-Path $License) {
        Copy-Item $License $PluginStage -Force
    }
    Copy-SkillContent -Destination (Join-Path $PluginStage "skills\trender")

    $PluginTempArchive = Join-Path ([System.IO.Path]::GetTempPath()) ("trender-plugin." + [guid]::NewGuid().ToString("N") + ".zip")
    $PluginArchive = Join-Path $Dist "trender-plugin.zip"
    if (Test-Path $PluginArchive) {
        Remove-Item $PluginArchive -Force
    }
    [System.IO.Compression.ZipFile]::CreateFromDirectory($PluginStage, $PluginTempArchive)
    Copy-Item $PluginTempArchive $PluginArchive -Force
    Write-Host "Built $PluginArchive"
}
finally {
    if (Test-Path $Stage) {
        Remove-Item $Stage -Recurse -Force
    }
    if ((Get-Variable PluginStage -ErrorAction SilentlyContinue) -and (Test-Path $PluginStage)) {
        Remove-Item $PluginStage -Recurse -Force
    }
    if (Test-Path $TempArchive) {
        Remove-Item $TempArchive -Force
    }
    if ((Get-Variable PluginTempArchive -ErrorAction SilentlyContinue) -and (Test-Path $PluginTempArchive)) {
        Remove-Item $PluginTempArchive -Force
    }
}

