$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& "$PSScriptRoot/check-versions.ps1"
Push-Location "$projectRoot/web"
try {
    npm run build
    npm run preview
} finally {
    Pop-Location
}

