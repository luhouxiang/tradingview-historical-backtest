param(
    [switch]$NoBrowser,
    [ValidateRange(0, 3600)][int]$HoldSeconds = 0
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
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

function Wait-Http([string]$Uri, [datetime]$Deadline) {
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return }
        } catch { Start-Sleep -Milliseconds 250 }
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

$requiredPorts = @(8080, 8091, 5173)
$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in $requiredPorts }
if ($listeners) { throw "Required port is already in use: $($listeners.LocalPort -join ', ')." }

& "$PSScriptRoot/check-versions.ps1"
& $PythonExecutable -c "import pyarrow; assert pyarrow.__version__ == '25.0.0'" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing locked Python runtime dependencies into the configured environment...'
    & $PythonExecutable -m pip install -r "$projectRoot/python/requirements.lock"
    if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed with exit code $LASTEXITCODE." }
}
if (-not (Test-Path -LiteralPath "$projectRoot/web/node_modules")) {
    Push-Location "$projectRoot/web"
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE." }
    } finally { Pop-Location }
}

& "$PSScriptRoot/prepare-demo-data.ps1" | Out-Null
New-Item -ItemType Directory -Force "$projectRoot/bin/runtime" | Out-Null
go build -o "$projectRoot/bin/chartd.exe" ./cmd/chartd
if ($LASTEXITCODE -ne 0) { throw "chartd build failed with exit code $LASTEXITCODE." }

$processes = [Collections.Generic.List[Diagnostics.Process]]::new()
$runtimeRoot = "$projectRoot/bin/runtime"
try {
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = "$projectRoot/python/src"
    try {
        $processes.Add((Start-Process $PythonExecutable -ArgumentList @(
            '-m', 'tvbt', '--config', "$projectRoot/config/app.yaml"
        ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput "$runtimeRoot/python.stdout.log" `
            -RedirectStandardError "$runtimeRoot/python.stderr.log"))
    } finally { $env:PYTHONPATH = $previousPythonPath }
    $processes.Add((Start-Process "$projectRoot/bin/chartd.exe" -ArgumentList @(
        '-config', "$projectRoot/config/app.yaml"
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "$runtimeRoot/go.stdout.log" `
        -RedirectStandardError "$runtimeRoot/go.stderr.log"))
    $processes.Add((Start-Process npm.cmd -ArgumentList @('run', 'dev') `
        -WorkingDirectory "$projectRoot/web" -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "$runtimeRoot/web.stdout.log" `
        -RedirectStandardError "$runtimeRoot/web.stderr.log"))

    $deadline = (Get-Date).AddSeconds(60)
    Wait-Json -Uri 'http://127.0.0.1:8091/internal/v1/health' -Deadline $deadline | Out-Null
    Wait-Json -Uri 'http://127.0.0.1:8080/api/v1/health' -Deadline $deadline | Out-Null
    Wait-Http -Uri 'http://127.0.0.1:5173/' -Deadline $deadline

    $scan = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets/scan' -Method Post
    Wait-Job -JobId $scan.job_id -Deadline ((Get-Date).AddSeconds(60))
    $catalog = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets'
    if (@($catalog.datasets).Count -eq 0) {
        $sources = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/source-files'
        $source = @($sources.items | Where-Object { $_.status -eq 'importable' -and $_.detected.symbol -eq 'AO2609' })[0]
        if (-not $source) { throw 'Prepared AO2609 sample was not detected as importable.' }
        $body = @{
            source_file_id = $source.source_file_id
            importer_id = 'tdx_txt_v1'
            exchange = 'SHFE'
            instrument = 'AO2609'
            timeframe = '5m'
            date_semantics = 'trading_day'
            timezone = 'Asia/Shanghai'
            timestamp_semantics = 'bar_end'
        } | ConvertTo-Json
        $import = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets/import' `
            -Method Post -ContentType 'application/json' -Body $body
        Wait-Job -JobId $import.job_id -Deadline ((Get-Date).AddSeconds(120))
        $catalog = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets'
    }
    if (@($catalog.datasets).Count -eq 0) { throw 'No ready dataset is available after demo import.' }

    Write-Host ''
    Write-Host 'TVBT is ready: http://127.0.0.1:5173/' -ForegroundColor Green
    Write-Host "Python: $PythonExecutable"
    Write-Host "Dataset: $($catalog.datasets[0].dataset_id), $($catalog.datasets[0].bar_count) bars"
    Write-Host 'The first dataset is selected automatically. Open the bottom Backtest tab and click Start formal backtest.'
    Write-Host 'Press Ctrl+C in this window to stop the exact service process trees.'
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:5173/' }

    if ($HoldSeconds -gt 0) {
        Start-Sleep -Seconds $HoldSeconds
    } else {
        while ($true) {
            foreach ($process in $processes) {
                $process.Refresh()
                if ($process.HasExited) {
                    throw "Service process $($process.Id) exited unexpectedly with code $($process.ExitCode). See bin/runtime logs."
                }
            }
            Start-Sleep -Milliseconds 500
        }
    }
} catch {
    foreach ($name in @('python.stderr.log', 'go.stderr.log', 'web.stderr.log')) {
        $path = Join-Path $runtimeRoot $name
        if (Test-Path -LiteralPath $path) {
            $content = Get-Content -Raw -LiteralPath $path
            if ($content) { Write-Host "${name}: $content" }
        }
    }
    throw
} finally {
    foreach ($process in $processes) { Stop-ExactProcessTree -TargetProcessId $process.Id }
}
