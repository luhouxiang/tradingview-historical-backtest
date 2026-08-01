param(
    [switch]$CpuProfile
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot/python-runtime.ps1"

Push-Location $projectRoot
try {
    Write-Output "runtime=$([System.Runtime.InteropServices.RuntimeInformation]::OSDescription)"
    Write-Output "processor_count=$([Environment]::ProcessorCount)"
    Write-Output "go=$(go version)"
    & $PythonExecutable --version
    if ($LASTEXITCODE -ne 0) { throw "Python version check failed with exit code $LASTEXITCODE." }
    if ($CpuProfile) {
        New-Item -ItemType Directory -Force -Path bin | Out-Null
        go test ./internal/marketdata -run '^$' -bench BenchmarkReaderHotTail3000 -benchmem -count 5 -cpuprofile bin/marketdata.cpu.pprof
    } else {
        go test ./internal/marketdata -run '^$' -bench BenchmarkReaderHotTail3000 -benchmem -count 5
    }
    if ($LASTEXITCODE -ne 0) { throw "Market data benchmark failed with exit code $LASTEXITCODE." }
    go test ./internal/logx -run '^$' -bench BenchmarkStructuredLogDiscard -benchmem -count 5
    if ($LASTEXITCODE -ne 0) { throw "Logging benchmark failed with exit code $LASTEXITCODE." }
    Push-Location python
    try {
        $env:PYTHONPATH = "$projectRoot/python/src"
        & $PythonExecutable -m pytest tests/test_chan_engine.py tests/test_strategy_backtest.py --durations=10
        if ($LASTEXITCODE -ne 0) { throw "Python benchmark tests failed with exit code $LASTEXITCODE." }
    } finally { Pop-Location }
    Push-Location web
    try {
        npm run test -- src/chart/chanPrimitive.test.ts src/replay/eventIndex.test.ts
        if ($LASTEXITCODE -ne 0) { throw "Vue benchmark tests failed with exit code $LASTEXITCODE." }
    } finally { Pop-Location }
} finally { Pop-Location }
