<#
.SYNOPSIS
    Builds the sideloadable Teams app package.

.DESCRIPTION
    Substitutes the bot's app id into the manifest and zips it with the two icons. The manifest
    in source control keeps the ${MICROSOFT_APP_ID} placeholder so no real id is committed.

.EXAMPLE
    .\teams_app\package.ps1 -AppId 00000000-0000-0000-0000-000000000000
    .\teams_app\package.ps1              # reads MICROSOFT_APP_ID from .env
#>
[CmdletBinding()]
param(
    [string]$AppId,
    [string]$OutFile = "learnforge-teams-app.zip"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

if (-not $AppId) {
    $envFile = Join-Path $root ".env"
    if (Test-Path $envFile) {
        $line = Select-String -Path $envFile -Pattern '^\s*MICROSOFT_APP_ID\s*=\s*(.+)$' |
            Select-Object -First 1
        if ($line) { $AppId = $line.Matches[0].Groups[1].Value.Trim().Trim('"') }
    }
}
if (-not $AppId) {
    throw "No app id. Pass -AppId, or set MICROSOFT_APP_ID in .env."
}
if ($AppId -notmatch '^[0-9a-fA-F-]{36}$') {
    throw "App id '$AppId' is not a GUID. Teams rejects the package at upload with an unhelpful error."
}

# Icons are generated rather than committed as binaries nobody can diff.
& (Join-Path $root ".venv\Scripts\python.exe") (Join-Path $here "make_icons.py") | Out-Null

$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("learnforge-teams-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $staging | Out-Null
try {
    (Get-Content (Join-Path $here "manifest.json") -Raw).Replace('${MICROSOFT_APP_ID}', $AppId) |
        Set-Content (Join-Path $staging "manifest.json") -Encoding UTF8
    Copy-Item (Join-Path $here "color.png"), (Join-Path $here "outline.png") $staging

    $target = Join-Path $root $OutFile
    if (Test-Path $target) { Remove-Item $target }
    # The three files must sit at the ZIP ROOT; a wrapping folder is the usual upload failure.
    Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $target
    Write-Host "Built $target for app id $AppId" -ForegroundColor Green
}
finally {
    Remove-Item $staging -Recurse -Force
}
