param(
    [switch]$NoBrowser,
    [ValidateRange(0, 3600)][int]$HoldSeconds = 0
)

$ErrorActionPreference = 'Stop'
$releaseRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot/python-runtime.ps1"

function Stop-ExactProcessTree([int]$TargetProcessId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$TargetProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) { Stop-ExactProcessTree -TargetProcessId $child.ProcessId }
    Stop-Process -Id $TargetProcessId -Force -ErrorAction SilentlyContinue
    Wait-Process -Id $TargetProcessId -Timeout 5 -ErrorAction SilentlyContinue
}

function Wait-Json([string]$Uri, [datetime]$Deadline) {
    do {
        try { return Invoke-RestMethod -Uri $Uri -TimeoutSec 2 }
        catch { Start-Sleep -Milliseconds 250 }
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for $Uri"
}

function Wait-Job([string]$JobId, [datetime]$Deadline) {
    do {
        $job = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/jobs/$JobId" -TimeoutSec 2
        if ($job.status -eq 'completed') { return }
        if ($job.status -in @('failed', 'cancelled', 'interrupted')) {
            throw "Job $JobId ended as $($job.status): $($job.error | ConvertTo-Json -Compress)"
        }
        Start-Sleep -Milliseconds 150
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for job $JobId"
}

$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @(8080, 8091) }
if ($listeners) { throw "Required release port is already in use: $($listeners.LocalPort -join ', ')." }

$pythonVersion = & $PythonExecutable -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
if ($LASTEXITCODE -ne 0 -or $pythonVersion -notmatch '^3\.14\.') {
    throw "Python 3.14 is required; configured executable: $PythonExecutable"
}
& $PythonExecutable -c "import pyarrow; assert pyarrow.__version__ == '25.0.0'" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'Locked Python dependencies are missing. Run the installation command from docs/10-installation-and-operations.md.'
}

$chartd = Join-Path $releaseRoot 'bin/chartd.exe'
$webRoot = Join-Path $releaseRoot 'web/dist'
if (-not (Test-Path -LiteralPath $chartd)) { throw "Release binary is missing: $chartd" }
if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'index.html'))) { throw "Built Web UI is missing: $webRoot" }
$demoData = & "$PSScriptRoot/prepare-demo-data.ps1" | ConvertFrom-Json
$runtimeRoot = Join-Path $releaseRoot 'bin/runtime'
New-Item -ItemType Directory -Force $runtimeRoot | Out-Null

$processes = [Collections.Generic.List[Diagnostics.Process]]::new()
try {
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = Join-Path $releaseRoot 'python/src'
    try {
        $processes.Add((Start-Process $PythonExecutable -ArgumentList @('-m', 'tvbt', '--config', (Join-Path $releaseRoot 'config/app.yaml')) `
            -WorkingDirectory $releaseRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $runtimeRoot 'python.stdout.log') `
            -RedirectStandardError (Join-Path $runtimeRoot 'python.stderr.log')))
    } finally { $env:PYTHONPATH = $previousPythonPath }
    $processes.Add((Start-Process $chartd -ArgumentList @(
        '-config', (Join-Path $releaseRoot 'config/app.yaml'), '-web-root', $webRoot
    ) -WorkingDirectory $releaseRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeRoot 'go.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeRoot 'go.stderr.log')))

    $deadline = (Get-Date).AddSeconds(60)
    Wait-Json -Uri 'http://127.0.0.1:8091/internal/v1/health' -Deadline $deadline | Out-Null
    Wait-Json -Uri 'http://127.0.0.1:8080/api/v1/health' -Deadline $deadline | Out-Null
    $scan = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets/scan' -Method Post
    Wait-Job -JobId $scan.job_id -Deadline ((Get-Date).AddSeconds(60))
    $catalog = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets'
    $preferredDataset = @($catalog.datasets | Where-Object { $_.dataset_id -eq $demoData.dataset_id })[0]
    $sources = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/source-files'
    $source = @($sources.items | Where-Object { $_.status -eq 'importable' -and $_.detected.symbol -eq $demoData.initial_instrument })[0]
    if ($source) {
        $body = @{
            source_file_id = $source.source_file_id
            importer_id = 'tdx_txt_v1'
            exchange = $source.detected.exchange
            instrument = $source.detected.symbol
            timeframe = $source.detected.timeframe
            date_semantics = if ($source.detected.date_semantics) { $source.detected.date_semantics } else { 'trading_day' }
            timezone = if ($source.detected.timezone) { $source.detected.timezone } else { 'Asia/Shanghai' }
            timestamp_semantics = if ($source.detected.timestamp_semantics) { $source.detected.timestamp_semantics } else { 'bar_end' }
        } | ConvertTo-Json
        $import = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets/import' -Method Post -ContentType 'application/json' -Body $body
        Wait-Job -JobId $import.job_id -Deadline ((Get-Date).AddSeconds(120))
        $catalog = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets'
        $preferredDataset = @($catalog.datasets | Where-Object { $_.dataset_id -eq $demoData.dataset_id })[0]
    }
    if (-not $preferredDataset) { throw "Dataset $($demoData.dataset_id) is unavailable after release bootstrap." }

    Write-Host "TVBT release is ready: http://127.0.0.1:8080/ ($($preferredDataset.bar_count) bars)" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8080/' }
    if ($HoldSeconds -gt 0) { Start-Sleep -Seconds $HoldSeconds }
    else {
        while ($true) {
            foreach ($process in $processes) {
                $process.Refresh()
                if ($process.HasExited) { throw "Release service $($process.Id) exited unexpectedly." }
            }
            Start-Sleep -Milliseconds 500
        }
    }
} finally {
    foreach ($process in $processes) { Stop-ExactProcessTree -TargetProcessId $process.Id }
}
