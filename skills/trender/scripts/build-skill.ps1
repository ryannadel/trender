$ErrorActionPreference = "Stop"

$SkillDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Root = (Resolve-Path (Join-Path $SkillDir "..\..")).Path
$Dist = Join-Path $Root "dist"
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

$Stage = Join-Path ([System.IO.Path]::GetTempPath()) ("trender-skill-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Copy-Item (Join-Path $SkillDir "SKILL.md") $Stage -Force
Copy-Item (Join-Path $SkillDir "scripts") $Stage -Recurse -Force

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
}
finally {
    if (Test-Path $Stage) {
        Remove-Item $Stage -Recurse -Force
    }
    if (Test-Path $TempArchive) {
        Remove-Item $TempArchive -Force
    }
}

