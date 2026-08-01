$ErrorActionPreference = 'Stop'
& "$PSScriptRoot/test-all.ps1"
if ($LASTEXITCODE -ne 0) { throw "Full test gate failed with exit code $LASTEXITCODE." }
& "$PSScriptRoot/smoke-milestone9.ps1"
if ($LASTEXITCODE -ne 0) { throw "Milestone 9 smoke failed with exit code $LASTEXITCODE." }
& "$PSScriptRoot/benchmark-milestone8.ps1"
if ($LASTEXITCODE -ne 0) { throw "Performance gate failed with exit code $LASTEXITCODE." }
