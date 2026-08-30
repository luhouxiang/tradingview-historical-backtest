param(
    [switch]$SkipBrowserInstall,
    [switch]$UseInstalledChrome,
    [switch]$InspectionOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'bin/e2e-runtime'))
$projectPrefix = $projectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $runtimeRoot.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe E2E runtime path: $runtimeRoot"
}
. "$PSScriptRoot/python-runtime.ps1"

$goPort = 18080
$pythonPort = 18091
$webPort = 15173
$apiBaseUrl = "http://127.0.0.1:$goPort"

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
        $job = Invoke-RestMethod -Uri "$apiBaseUrl/api/v1/jobs/$JobId" -TimeoutSec 2
        if ($job.status -eq 'completed') { return }
        if ($job.status -in @('failed', 'cancelled', 'interrupted')) {
            throw "Job $JobId ended as $($job.status): $($job.error | ConvertTo-Json -Compress)"
        }
        Start-Sleep -Milliseconds 150
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for job $JobId"
}

$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @($goPort, $pythonPort, $webPort) }
if ($listeners) { throw "E2E port is already in use: $($listeners.LocalPort -join ', ')." }

if (Test-Path -LiteralPath $runtimeRoot) { Remove-Item -LiteralPath $runtimeRoot -Recurse -Force }
$dataRoot = Join-Path $runtimeRoot 'data'
$historyRoot = Join-Path $dataRoot 'history'
$configRoot = Join-Path $dataRoot 'config'
$logRoot = Join-Path $runtimeRoot 'logs'
New-Item -ItemType Directory -Force $historyRoot, $configRoot, $logRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'config/examples/instruments.json') -Destination $configRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'config/examples/sessions.json') -Destination $configRoot

$sourceLines = [Collections.Generic.List[string]]::new()
$sourceLines.Add([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('QU9MOSDmsKfljJbpk53liqDmnYMgNeWIhumSn+e6vyDkuI3lpI3mnYM=')))
$sourceLines.Add([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5pel5pyfIOaXtumXtCDlvIDnm5gg5pyA6auYIOacgOS9jiDmlLbnm5gg5oiQ5Lqk6YePIOaMgeS7k+mHjyDnu5Pnrpfku7c=')))
$calendarLines = [Collections.Generic.List[string]]::new()
$calendarLines.Add('trading_day,night_session_date,is_open,note')
$day = [datetime]'2026-01-05'
$createdDays = 0
$barIndex = 0
while ($createdDays -lt 30) {
    if ($day.DayOfWeek -notin @([DayOfWeek]::Saturday, [DayOfWeek]::Sunday)) {
        $dayText = $day.ToString('yyyy/MM/dd')
        $calendarLines.Add("$($day.ToString('yyyy-MM-dd')),$($day.AddDays(-1).ToString('yyyy-MM-dd')),true,e2e")
        $times = @()
        for ($minute = 9 * 60 + 5; $minute -le 10 * 60 + 15; $minute += 5) { $times += $minute }
        for ($minute = 10 * 60 + 35; $minute -le 11 * 60 + 30; $minute += 5) { $times += $minute }
        for ($minute = 13 * 60 + 35; $minute -le 15 * 60; $minute += 5) { $times += $minute }
        foreach ($minute in $times) {
            $base = 2600 + [math]::Floor($barIndex / 12) + (($barIndex % 20) - 10)
            $open = [int]$base
            $close = $open + (($barIndex % 5) - 2)
            $high = [math]::Max($open, $close) + 3
            $low = [math]::Min($open, $close) - 3
            $hhmm = '{0:D2}{1:D2}' -f [int][math]::Floor($minute / 60), [int]($minute % 60)
            $sourceLines.Add("$dayText,$hhmm,$open,$high,$low,$close,$(1000 + $barIndex),$(50000 + $barIndex),0")
            $barIndex++
        }
        $createdDays++
    }
    $day = $day.AddDays(1)
}
[IO.File]::WriteAllLines((Join-Path $historyRoot '30#AOL9.txt'), $sourceLines, [Text.Encoding]::GetEncoding(54936))
[IO.File]::WriteAllLines((Join-Path $configRoot 'trading_calendar.csv'), $calendarLines, [Text.UTF8Encoding]::new($false))

$dataRootYaml = $dataRoot.Replace('\', '/')
$appConfig = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $projectRoot 'config/app.yaml')
$appConfig = $appConfig.Replace('127.0.0.1:8080', "127.0.0.1:$goPort")
$appConfig = $appConfig.Replace('http://127.0.0.1:8091', "http://127.0.0.1:$pythonPort")
$appConfig = $appConfig.Replace('data_root: ./trading-data', "data_root: `"$dataRootYaml`"")
$appConfigPath = Join-Path $runtimeRoot 'app.yaml'
[IO.File]::WriteAllText($appConfigPath, $appConfig, [Text.UTF8Encoding]::new($false))

& "$PSScriptRoot/check-versions.ps1"
& "$PSScriptRoot/build-chartd.ps1" -Force | Out-Null
if (-not $SkipBrowserInstall) {
    Push-Location (Join-Path $projectRoot 'web')
    try {
        npx playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw "Playwright browser installation failed with exit code $LASTEXITCODE." }
    } finally { Pop-Location }
}

$processes = [Collections.Generic.List[Diagnostics.Process]]::new()
try {
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = Join-Path $projectRoot 'python/src'
    try {
        $processes.Add((Start-Process $PythonExecutable -ArgumentList @('-m', 'tvbt', '--config', $appConfigPath) `
            -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logRoot 'python.stdout.log') `
            -RedirectStandardError (Join-Path $logRoot 'python.stderr.log')))
    } finally { $env:PYTHONPATH = $previousPythonPath }
    $processes.Add((Start-Process (Join-Path $projectRoot 'bin/chartd.exe') -ArgumentList @('-config', $appConfigPath) `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logRoot 'go.stdout.log') `
        -RedirectStandardError (Join-Path $logRoot 'go.stderr.log')))

    $deadline = (Get-Date).AddSeconds(60)
    Wait-Json -Uri "http://127.0.0.1:$pythonPort/internal/v1/health" -Deadline $deadline | Out-Null
    Wait-Json -Uri "$apiBaseUrl/api/v1/health" -Deadline $deadline | Out-Null
    $scan = Invoke-RestMethod -Uri "$apiBaseUrl/api/v1/datasets/scan" -Method Post
    Wait-Job -JobId $scan.job_id -Deadline ((Get-Date).AddSeconds(60))
    $sources = Invoke-RestMethod -Uri "$apiBaseUrl/api/v1/source-files"
    $source = @($sources.items | Where-Object { $_.status -eq 'importable' -and $_.detected.symbol -eq 'AOL9' })[0]
    if (-not $source) { throw 'The generated AOL9 E2E source was not importable.' }
    $body = @{
        source_file_id = $source.source_file_id
        importer_id = 'tdx_txt_v1'
        exchange = $source.detected.exchange
        instrument = $source.detected.symbol
        timeframe = $source.detected.timeframe
        date_semantics = 'trading_day'
        timezone = 'Asia/Shanghai'
        timestamp_semantics = 'bar_end'
    } | ConvertTo-Json
    $import = Invoke-RestMethod -Uri "$apiBaseUrl/api/v1/datasets/import" -Method Post -ContentType 'application/json' -Body $body
    Wait-Job -JobId $import.job_id -Deadline ((Get-Date).AddSeconds(120))

    $previousAppConfig = $env:TVBT_APP_CONFIG
    $previousGoBaseUrl = $env:TVBT_GO_BASE_URL
    $previousWebPort = $env:TVBT_WEB_PORT
    $env:TVBT_APP_CONFIG = $appConfigPath
    $env:TVBT_GO_BASE_URL = $apiBaseUrl
    $env:TVBT_WEB_PORT = "$webPort"
    try {
        $processes.Add((Start-Process npm.cmd -ArgumentList @('run', 'dev') `
            -WorkingDirectory (Join-Path $projectRoot 'web') -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logRoot 'web.stdout.log') `
            -RedirectStandardError (Join-Path $logRoot 'web.stderr.log')))
    } finally {
        $env:TVBT_APP_CONFIG = $previousAppConfig
        $env:TVBT_GO_BASE_URL = $previousGoBaseUrl
        $env:TVBT_WEB_PORT = $previousWebPort
    }
    $catalog = Invoke-RestMethod -Uri "$apiBaseUrl/api/v1/datasets"
    if (-not @($catalog.datasets | Where-Object { $_.dataset_id -eq 'SHFE.AOL9.5m' })[0]) {
        throw 'The imported AOL9 E2E dataset is missing from the catalog.'
    }
    $pageDeadline = (Get-Date).AddSeconds(30)
    do {
        try { $page = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$webPort/" -TimeoutSec 2 }
        catch { $page = $null; Start-Sleep -Milliseconds 250 }
    } while ((-not $page -or $page.StatusCode -ne 200) -and (Get-Date) -lt $pageDeadline)
    if (-not $page -or $page.StatusCode -ne 200) { throw 'Timed out waiting for the E2E Vue server.' }

    if ($InspectionOnly) {
        Write-Host "E2E inspection stack ready: http://127.0.0.1:$webPort/"
        while ($true) {
            foreach ($process in $processes) {
                $process.Refresh()
                if ($process.HasExited) { throw "Inspection service $($process.Id) exited unexpectedly." }
            }
            Start-Sleep -Milliseconds 500
        }
    } else {
        Push-Location (Join-Path $projectRoot 'web')
        try {
            $env:TVBT_E2E_BASE_URL = "http://127.0.0.1:$webPort"
            if ($UseInstalledChrome) { $env:TVBT_E2E_BROWSER_CHANNEL = 'chrome' }
            npm run e2e
            if ($LASTEXITCODE -ne 0) { throw "Browser E2E failed with exit code $LASTEXITCODE." }
        } finally {
            Remove-Item Env:TVBT_E2E_BASE_URL -ErrorAction SilentlyContinue
            Remove-Item Env:TVBT_E2E_BROWSER_CHANNEL -ErrorAction SilentlyContinue
            Pop-Location
        }
    }

    [pscustomobject]@{
        milestone = '13C'
        status = 'passed'
        browser = 'chromium'
        dataset_id = 'SHFE.AOL9.5m'
        fixture_bars = $barIndex
        isolated_ports = @($webPort, $goPort, $pythonPort)
        verified = @('startup_selection', 'candles_visible', 'formal_backtest', 'polling_completion', 'reload_result_restore')
    } | ConvertTo-Json -Compress
} catch {
    foreach ($name in @('python.stderr.log', 'go.stderr.log', 'web.stderr.log')) {
        $path = Join-Path $logRoot $name
        if (Test-Path -LiteralPath $path) {
            $content = Get-Content -Raw -LiteralPath $path
            if ($content) { Write-Host "${name}: $content" }
        }
    }
    throw
} finally {
    foreach ($process in $processes) { Stop-ExactProcessTree -TargetProcessId $process.Id }
}
