$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    go test ./internal/jobs
    if ($LASTEXITCODE -ne 0) { throw "Job recovery tests failed with exit code $LASTEXITCODE." }

    & "$PSScriptRoot/prepare-demo-data.ps1" | Out-Null
    $prepareTimer = [Diagnostics.Stopwatch]::StartNew()
    $prepared = & "$PSScriptRoot/prepare-demo-data.ps1" | ConvertFrom-Json
    $prepareTimer.Stop()
    if (-not $prepared.calendar_cache_hit) { throw 'Unchanged demo data did not use the calendar cache.' }
    if ($prepareTimer.Elapsed.TotalMilliseconds -gt 1500) {
        throw "Cached demo preparation exceeded 1500 ms: $($prepareTimer.Elapsed.TotalMilliseconds) ms."
    }

    & "$PSScriptRoot/build-chartd.ps1" | Out-Null
    $buildTimer = [Diagnostics.Stopwatch]::StartNew()
    $built = & "$PSScriptRoot/build-chartd.ps1" | ConvertFrom-Json
    $buildTimer.Stop()
    if ($built.rebuilt) { throw 'Unchanged Go sources rebuilt chartd.' }
    if ($buildTimer.Elapsed.TotalMilliseconds -gt 1500) {
        throw "Cached chartd build check exceeded 1500 ms: $($buildTimer.Elapsed.TotalMilliseconds) ms."
    }

    [pscustomobject]@{
        milestone = '13A'
        job_recovery = 'passed'
        calendar_cache_hit = $prepared.calendar_cache_hit
        cached_prepare_ms = [math]::Round($prepareTimer.Elapsed.TotalMilliseconds, 2)
        chartd_rebuilt = $built.rebuilt
        cached_build_check_ms = [math]::Round($buildTimer.Elapsed.TotalMilliseconds, 2)
    } | ConvertTo-Json -Compress
} finally {
    Pop-Location
}
