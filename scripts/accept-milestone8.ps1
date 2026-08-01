param([switch]$CpuProfile)

$ErrorActionPreference = 'Stop'
& "$PSScriptRoot/test-all.ps1"
if ($LASTEXITCODE -ne 0) { throw "Full test gate failed with exit code $LASTEXITCODE." }
& "$PSScriptRoot/smoke-milestone8.ps1"
if ($LASTEXITCODE -ne 0) { throw "Milestone 8 smoke failed with exit code $LASTEXITCODE." }
& "$PSScriptRoot/benchmark-milestone8.ps1" -CpuProfile:$CpuProfile
if ($LASTEXITCODE -ne 0) { throw "Performance gate failed with exit code $LASTEXITCODE." }
