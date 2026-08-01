$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot/python-runtime.ps1"
& "$PSScriptRoot/check-versions.ps1"
$env:PYTHONPATH = "$projectRoot/python/src"
Push-Location "$projectRoot/python"
try {
    & $PythonExecutable -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed with exit code $LASTEXITCODE." }
    & $PythonExecutable -m tvbt --config "$projectRoot/config/app.yaml"
} finally {
    Pop-Location
}
