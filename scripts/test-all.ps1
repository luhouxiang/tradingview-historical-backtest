$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot/python-runtime.ps1"
& "$PSScriptRoot/check-versions.ps1"
& "$PSScriptRoot/generate-contracts.ps1"

Push-Location $projectRoot
try {
    go test ./...
    if ($LASTEXITCODE -ne 0) { throw "Go tests failed with exit code $LASTEXITCODE." }
    go vet ./...
    if ($LASTEXITCODE -ne 0) { throw "Go vet failed with exit code $LASTEXITCODE." }
    $unformatted = gofmt -l cmd internal
    if ($unformatted) { throw "Unformatted Go files:`n$unformatted" }

    $env:PYTHONPATH = "$projectRoot/python/src"
    Push-Location python
    try {
        & $PythonExecutable -m pytest
        if ($LASTEXITCODE -ne 0) { throw "Python tests failed with exit code $LASTEXITCODE." }
        & $PythonExecutable -m ruff check .
        if ($LASTEXITCODE -ne 0) { throw "Ruff check failed with exit code $LASTEXITCODE." }
        & $PythonExecutable -m ruff format --check .
        if ($LASTEXITCODE -ne 0) { throw "Ruff format check failed with exit code $LASTEXITCODE." }
        & $PythonExecutable -m mypy
        if ($LASTEXITCODE -ne 0) { throw "mypy failed with exit code $LASTEXITCODE." }
    } finally { Pop-Location }

    Push-Location web
    try {
        npm run contracts:validate
        if ($LASTEXITCODE -ne 0) { throw "Contract validation failed with exit code $LASTEXITCODE." }
        npm run contracts:generate
        if ($LASTEXITCODE -ne 0) { throw "TypeScript contract generation failed with exit code $LASTEXITCODE." }
        npm run typecheck
        if ($LASTEXITCODE -ne 0) { throw "Vue typecheck failed with exit code $LASTEXITCODE." }
        npm run test
        if ($LASTEXITCODE -ne 0) { throw "Vue tests failed with exit code $LASTEXITCODE." }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Vue production build failed with exit code $LASTEXITCODE." }
    } finally { Pop-Location }
} finally { Pop-Location }
