$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location "$projectRoot/internal/api"
try {
    go generate
    if ($LASTEXITCODE -ne 0) { throw "Go contract generation failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}
Push-Location "$projectRoot/web"
try {
    npm run contracts:generate
    if ($LASTEXITCODE -ne 0) { throw "TypeScript contract generation failed with exit code $LASTEXITCODE." }
    npm run contracts:validate
    if ($LASTEXITCODE -ne 0) { throw "Contract validation failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}
