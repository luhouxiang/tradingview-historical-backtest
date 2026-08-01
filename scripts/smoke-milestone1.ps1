param(
    [switch]$VerifyBars,
    [switch]$VerifyIndicators,
    [switch]$VerifyChan,
    [switch]$VerifyReplay,
    [switch]$VerifyBacktest,
    [switch]$VerifyOptimization,
    [switch]$VerifyWorkspace,
    [switch]$VerifyRecovery,
    [ValidateRange(0, 120)][int]$HoldSeconds = 0
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot/python-runtime.ps1"
& "$PSScriptRoot/check-versions.ps1" | Out-Null

$needsPython = $VerifyIndicators -or $VerifyChan -or $VerifyReplay -or $VerifyBacktest -or $VerifyOptimization
$requiredPorts = if ($needsPython) { @(8080, 8091) } else { @(8080) }
$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in $requiredPorts }
if ($listeners) { throw "Required port is already in use: $($listeners.LocalPort -join ', ')." }

$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$tempRoot = [IO.Path]::GetFullPath((Join-Path $tempBase "tvbt-m1-$([guid]::NewGuid().ToString('N'))"))
if (-not $tempRoot.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe temporary path: $tempRoot"
}
$dataRoot = Join-Path $tempRoot 'data'
$historyRoot = Join-Path $dataRoot 'history'
$runtimeConfigRoot = Join-Path $dataRoot 'config'
New-Item -ItemType Directory -Force $historyRoot, $runtimeConfigRoot | Out-Null

$sourcePath = Join-Path $historyRoot '30#AO2609.txt'
Copy-Item -LiteralPath "$projectRoot/samples/30#AO2609.txt" -Destination $sourcePath
Copy-Item -LiteralPath "$projectRoot/config/examples/instruments.json" -Destination $runtimeConfigRoot
Copy-Item -LiteralPath "$projectRoot/config/examples/sessions.json" -Destination $runtimeConfigRoot

$sourceText = [Text.Encoding]::GetEncoding(54936).GetString([IO.File]::ReadAllBytes($sourcePath))
$tradingDays = @($sourceText -split "`r?`n" |
    ForEach-Object { if ($_ -match '^(\d{4}/\d{2}/\d{2}),') { $matches[1] } } |
    Select-Object -Unique)
if ($tradingDays.Count -lt 2) { throw 'Could not derive trading days from the full sample.' }
$calendarLines = [Collections.Generic.List[string]]::new()
$calendarLines.Add('trading_day,night_session_date,is_open,note')
for ($index = 0; $index -lt $tradingDays.Count; $index++) {
    $day = [datetime]::ParseExact($tradingDays[$index], 'yyyy/MM/dd', $null)
    $night = if ($index -eq 0) { $day.AddDays(-1) } else {
        [datetime]::ParseExact($tradingDays[$index - 1], 'yyyy/MM/dd', $null)
    }
    $calendarLines.Add("$($day.ToString('yyyy-MM-dd')),$($night.ToString('yyyy-MM-dd')),true,smoke")
}
[IO.File]::WriteAllLines((Join-Path $runtimeConfigRoot 'trading_calendar.csv'), $calendarLines)

$appConfigPath = Join-Path $tempRoot 'app.yaml'
$dataRootYaml = $dataRoot.Replace('\', '/')
$appConfig = (Get-Content -Raw "$projectRoot/config/app.yaml").Replace(
    'data_root: ./trading-data',
    "data_root: `"$dataRootYaml`""
)
[IO.File]::WriteAllText($appConfigPath, $appConfig)

New-Item -ItemType Directory -Force "$projectRoot/bin" | Out-Null
go build -o "$projectRoot/bin/chartd.exe" ./cmd/chartd
if ($LASTEXITCODE -ne 0) { throw 'chartd build failed.' }
if ($VerifyRecovery) {
    go build -o "$projectRoot/bin/cachectl.exe" ./cmd/cachectl
    if ($LASTEXITCODE -ne 0) { throw 'cachectl build failed.' }
    $jobStore = Join-Path $dataRoot 'tasks/jobs'
    $staleTemp = Join-Path $dataRoot 'cache/indicators/.smoke.tmp-interrupted'
    New-Item -ItemType Directory -Force $jobStore, $staleTemp | Out-Null
    $oldTimestamp = (Get-Date).ToUniversalTime().AddHours(-25).ToString('o')
    @{
        job_id = 'job-restart-smoke'; kind = 'backtest'; status = 'running'; progress = 0.4
        metadata = @{ run_signature = "sha256:$('1' * 64)" }
        created_at = $oldTimestamp; updated_at = $oldTimestamp
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $jobStore 'job-restart-smoke.json') -Encoding utf8
    (Get-Item -LiteralPath $staleTemp).LastWriteTimeUtc = (Get-Date).ToUniversalTime().AddHours(-25)
}

function Stop-ExactProcessTree([int]$TargetProcessId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$TargetProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) { Stop-ExactProcessTree -TargetProcessId $child.ProcessId }
    Stop-Process -Id $TargetProcessId -Force -ErrorAction SilentlyContinue
    Wait-Process -Id $TargetProcessId -Timeout 5 -ErrorAction SilentlyContinue
}

function Wait-Json([string]$Uri, [datetime]$Deadline) {
    do {
        try { return Invoke-RestMethod -Uri $Uri -TimeoutSec 2 } catch { Start-Sleep -Milliseconds 200 }
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for $Uri"
}

function Wait-Job([string]$JobId, [datetime]$Deadline) {
    do {
        $job = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/jobs/$JobId" -TimeoutSec 2
        if ($job.status -eq 'completed') { return $job }
        if ($job.status -in @('failed', 'cancelled', 'interrupted')) {
            throw "Job $JobId ended as $($job.status): $($job.error | ConvertTo-Json -Compress)"
        }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for job $JobId"
}

function Wait-Calculation([string]$JobId, [datetime]$Deadline) {
    do {
        $job = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/calculations/$JobId" -TimeoutSec 2
        if ($job.status -eq 'completed') { return $job }
        if ($job.status -in @('failed', 'cancelled', 'interrupted')) {
            throw "Calculation $JobId ended as $($job.status): $($job.error | ConvertTo-Json -Compress)"
        }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for calculation $JobId"
}

function Wait-Replay([string]$ReplayId, [datetime]$Deadline) {
    do {
        $job = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/replays/$ReplayId" -TimeoutSec 2
        if ($job.status -eq 'completed') { return $job }
        if ($job.status -in @('failed', 'cancelled', 'interrupted')) {
            throw "Replay $ReplayId ended as $($job.status): $($job.error | ConvertTo-Json -Compress)"
        }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for replay $ReplayId"
}

function Wait-Backtest([string]$RunId, [datetime]$Deadline) {
    do {
        $job = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/backtests/$RunId" -TimeoutSec 2
        if ($job.status -eq 'completed') { return $job }
        if ($job.status -in @('failed', 'cancelled', 'interrupted')) {
            throw "Backtest $RunId ended as $($job.status): $($job.error | ConvertTo-Json -Compress)"
        }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for backtest $RunId"
}

function Wait-Study([string]$StudyId, [datetime]$Deadline) {
    do {
        $job = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/studies/$StudyId" -TimeoutSec 2
        if ($job.status -eq 'completed') { return $job }
        if ($job.status -in @('failed', 'cancelled', 'interrupted')) {
            throw "Study $StudyId ended as $($job.status): $($job.error | ConvertTo-Json -Compress)"
        }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for study $StudyId"
}

$chartd = $null
$pythonEngine = $null
$chartdStdout = Join-Path $tempRoot 'chartd.stdout.log'
$chartdStderr = Join-Path $tempRoot 'chartd.stderr.log'
try {
    if ($needsPython) {
        $pythonStdout = Join-Path $tempRoot 'python.stdout.log'
        $pythonStderr = Join-Path $tempRoot 'python.stderr.log'
        $previousPythonPath = $env:PYTHONPATH
        $env:PYTHONPATH = "$projectRoot/python/src"
        try {
            $pythonEngine = Start-Process $PythonExecutable -ArgumentList @(
                '-m', 'tvbt', '--config', $appConfigPath
            ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $pythonStdout -RedirectStandardError $pythonStderr
        } finally { $env:PYTHONPATH = $previousPythonPath }
        Start-Sleep -Milliseconds 300
        $pythonEngine.Refresh()
        if ($pythonEngine.HasExited) {
            throw "Python engine exited during startup: $(Get-Content -Raw $pythonStderr)"
        }
    }
    $chartd = Start-Process "$projectRoot/bin/chartd.exe" -ArgumentList @(
        '-config', $appConfigPath
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $chartdStdout -RedirectStandardError $chartdStderr
    Start-Sleep -Milliseconds 300
    $chartd.Refresh()
    if ($chartd.HasExited) {
        $startupError = if (Test-Path -LiteralPath $chartdStderr) { Get-Content -Raw $chartdStderr } else { '' }
        throw "chartd exited during startup: $startupError"
    }
    $deadline = (Get-Date).AddSeconds($(if ($VerifyOptimization) { 240 } else { 45 }))
    Wait-Json -Uri 'http://127.0.0.1:8080/api/v1/source-files' -Deadline $deadline | Out-Null
    $restartRecovery = $null
    if ($VerifyRecovery) {
        $recoveredJob = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/jobs/job-restart-smoke'
        if ($recoveredJob.status -ne 'interrupted' -or $recoveredJob.error.code -ne 'PROCESS_RESTARTED') {
            throw "Restarted job was not interrupted: $($recoveredJob | ConvertTo-Json -Compress)"
        }
        if (Test-Path -LiteralPath $staleTemp) { throw 'Stale calculation temp was not recovered.' }
        $recoveredTemps = @(Get-ChildItem -Path (Join-Path $dataRoot 'trash/interrupted') -Recurse -Directory -Filter '.smoke.tmp-interrupted')
        if ($recoveredTemps.Count -ne 1) { throw 'Recovered temp was not moved to the expected trash tree.' }
        $restartRecovery = $true
    }

    $sourceHashBefore = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
    $scan = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets/scan' -Method Post
    Wait-Job -JobId $scan.job_id -Deadline $deadline | Out-Null
    $sources = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/source-files'
    if ($sources.items.Count -ne 1 -or $sources.items[0].status -ne 'importable') {
        throw "Unexpected scan result: $($sources | ConvertTo-Json -Depth 8 -Compress)"
    }

    $importBody = @{
        source_file_id = $sources.items[0].source_file_id
        importer_id = 'tdx_txt_v1'
        exchange = 'SHFE'
        instrument = 'AO2609'
        timeframe = '5m'
        date_semantics = 'trading_day'
        timezone = 'Asia/Shanghai'
        timestamp_semantics = 'bar_end'
    } | ConvertTo-Json
    $started = Get-Date
    $import = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets/import' `
        -Method Post -ContentType 'application/json' -Body $importBody
    Wait-Job -JobId $import.job_id -Deadline $deadline | Out-Null
    $elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds

    $catalog = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets'
    if ($catalog.datasets.Count -ne 1 -or $catalog.datasets[0].bar_count -ne 17017 -or $catalog.catalog_revision -ne 1) {
        throw "Unexpected catalog: $($catalog | ConvertTo-Json -Depth 8 -Compress)"
    }
    $revision = $catalog.datasets[0].active_revision
    $escapedRevision = [uri]::EscapeDataString($revision)
    $meta = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/datasets/SHFE.AO2609.5m?revision=$escapedRevision"
    if ($meta.coverage.bar_count -ne 17017 -or $meta.quality.zero_volume_count -ne 1224) {
        throw "Unexpected metadata: $($meta | ConvertTo-Json -Depth 8 -Compress)"
    }

    $barsP95 = $null
    $prefetchCount = $null
    if ($VerifyBars) {
        $tailUri = "http://127.0.0.1:8080/api/v1/datasets/SHFE.AO2609.5m/bars?revision=$escapedRevision&generation_id=gen-smoke&tail=3000"
        $tail = Invoke-RestMethod -Uri $tailUri
        if ($tail.bars.bar_index.Count -ne 3000 -or $tail.coverage.first_bar_index -ne 14017 -or $tail.coverage.last_bar_index -ne 17016 -or -not $tail.has_more_before) {
            throw "Unexpected tail range: $($tail.coverage | ConvertTo-Json -Compress)"
        }
        if ($tail.generation_id -ne 'gen-smoke' -or $tail.checksum -notmatch '^sha256:[0-9a-f]{64}$') {
            throw 'Tail response identity or checksum is invalid.'
        }
        $before = $tail.coverage.first_bar_index
        $prefetch = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/datasets/SHFE.AO2609.5m/bars?revision=$escapedRevision&generation_id=gen-smoke&before_bar_index=$before&limit=1500"
        $prefetchCount = $prefetch.bars.bar_index.Count
        if ($prefetchCount -ne 1500 -or $prefetch.coverage.first_bar_index -ne 12517 -or $prefetch.coverage.last_bar_index -ne 14016) {
            throw "Unexpected prefetch range: $($prefetch.coverage | ConvertTo-Json -Compress)"
        }
        $timings = [Collections.Generic.List[double]]::new()
        1..25 | ForEach-Object {
            $timer = [Diagnostics.Stopwatch]::StartNew()
            Invoke-RestMethod -Uri $tailUri | Out-Null
            $timer.Stop()
            $timings.Add($timer.Elapsed.TotalMilliseconds)
        }
        $sorted = @($timings | Sort-Object)
        $barsP95 = [math]::Round($sorted[[math]::Ceiling($sorted.Count * 0.95) - 1], 2)
        if ($barsP95 -gt 200) { throw "Hot-cache 3000-bar HTTP p95 exceeded 200 ms: ${barsP95}ms" }

        $badRevision = [uri]::EscapeDataString("sha256:$('f' * 64)")
        $conflictStatus = 0
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8080/api/v1/datasets/SHFE.AO2609.5m/bars?revision=$badRevision&generation_id=gen-smoke&tail=3000" | Out-Null
        } catch {
            $conflictStatus = [int]$_.Exception.Response.StatusCode
        }
        if ($conflictStatus -ne 409) { throw "Revision mismatch returned HTTP $conflictStatus instead of 409." }
    }

    $algorithmResponse = $null
    if ($needsPython) {
        $algorithmResponse = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/algorithms'
    }
    $indicatorCacheHit = $null
    $indicatorAlgorithms = $null
    $maAlgorithmRef = $null
    if ($VerifyIndicators) {
        $indicatorAlgorithms = @($algorithmResponse.algorithms | Where-Object kind -eq 'indicator').Count
        if ($indicatorAlgorithms -ne 3) { throw "Expected 3 indicator algorithms, found $indicatorAlgorithms." }
        foreach ($algorithmId in @('ma', 'macd', 'atr')) {
            $algorithm = $algorithmResponse.algorithms | Where-Object algorithm_id -eq $algorithmId
            if (-not $algorithm -or $algorithm.source_hash -notmatch '^sha256:[0-9a-f]{64}$') {
                throw "Invalid algorithm definition for $algorithmId."
            }
            $calculationBody = @{
                dataset_id = $meta.dataset_id
                data_revision = $revision
                algorithm = @{
                    kind = $algorithm.kind
                    algorithm_id = $algorithm.algorithm_id
                    algorithm_version = $algorithm.algorithm_version
                    source_hash = $algorithm.source_hash
                }
                parameters = @{}
                calculation_mode = 'full_history'
            } | ConvertTo-Json -Depth 8
            if ($algorithmId -eq 'ma') { $maAlgorithmRef = $algorithm }
            $created = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/calculations' `
                -Method Post -ContentType 'application/json' -Body $calculationBody
            $completed = Wait-Calculation -JobId $created.job_id -Deadline $deadline
            $results = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/calculations/$($created.job_id)/results?from_bar_index=16990&to_bar_index=17016"
            if ($results.coverage.returned_count -ne 27 -or $results.checksum -notmatch '^sha256:[0-9a-f]{64}$') {
                throw "Invalid $algorithmId range results."
            }
            foreach ($output in $algorithm.outputs) {
                if (-not $results.values.PSObject.Properties[$output.name]) { throw "$algorithmId output $($output.name) is missing." }
            }
            if ($algorithmId -eq 'ma') {
                $cachePath = Join-Path $dataRoot $completed.result_ref
                if (-not (Test-Path "$cachePath/_SUCCESS") -or -not (Test-Path "$cachePath/manifest.json") -or -not (Test-Path "$cachePath/values.parquet")) {
                    throw 'MA cache was not atomically committed.'
                }
                node "$projectRoot/web/scripts/validate-indicator-cache.mjs" "$cachePath/manifest.json"
                if ($LASTEXITCODE -ne 0) { throw 'Indicator cache manifest failed schema validation.' }
                $cached = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/calculations' `
                    -Method Post -ContentType 'application/json' -Body $calculationBody
                $indicatorCacheHit = $cached.status -eq 'completed' -and $cached.result_ref -eq $completed.result_ref
                if (-not $indicatorCacheHit) { throw 'Equivalent MA calculation did not hit cache.' }
            }
        }
    }

    $chanCacheHit = $null
    $chanObjectCount = $null
    $chanEventCount = $null
    $chanCheckpointCount = $null
    $chanAlgorithmRef = $null
    $chanParameters = $null
    if ($VerifyChan -or $VerifyReplay) {
        $chanAlgorithmRef = $algorithmResponse.algorithms | Where-Object kind -eq 'chan'
        if (-not $chanAlgorithmRef -or $chanAlgorithmRef.algorithm_id -ne 'chan_engineering') {
            throw 'Causal Chan algorithm definition is missing.'
        }
        $chanParameters = @{}
        foreach ($property in $chanAlgorithmRef.parameter_schema.properties.PSObject.Properties) {
            $chanParameters[$property.Name] = $property.Value.default
        }
        $chanBodyValue = @{
            dataset_id = $meta.dataset_id
            data_revision = $revision
            algorithm = @{
                kind = $chanAlgorithmRef.kind
                algorithm_id = $chanAlgorithmRef.algorithm_id
                algorithm_version = $chanAlgorithmRef.algorithm_version
                source_hash = $chanAlgorithmRef.source_hash
            }
            parameters = $chanParameters
            calculation_mode = 'causal_events'
        }
        $chanBody = $chanBodyValue | ConvertTo-Json -Depth 10
        $chanCreated = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/calculations' `
            -Method Post -ContentType 'application/json' -Body $chanBody
        $chanCompleted = Wait-Calculation -JobId $chanCreated.job_id -Deadline ((Get-Date).AddSeconds(120))
        $chanResults = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/calculations/$($chanCreated.job_id)/results?from_bar_index=12017&to_bar_index=17016"
        if ($chanResults.result_kind -ne 'chan' -or $chanResults.checksum -notmatch '^sha256:[0-9a-f]{64}$' -or $chanResults.PSObject.Properties['bar_index'] -or $chanResults.PSObject.Properties['values']) {
            throw 'Chan range response did not use the semantic-object contract.'
        }
        if ($chanResults.objects.PSObject.Properties['segments']) { throw 'Chan response unexpectedly advertises line segments.' }
        $chanObjectCount = @($chanResults.objects.fractals).Count + @($chanResults.objects.bi).Count + @($chanResults.objects.zhongshu).Count
        if (@($chanResults.objects.bi).Count -lt 1 -or @($chanResults.objects.zhongshu).Count -lt 1) {
            throw "Full sample did not produce both bi and zhongshu objects: $($chanResults.objects | ConvertTo-Json -Depth 4 -Compress)"
        }
        $chanCachePath = Join-Path $dataRoot $chanCompleted.result_ref
        foreach ($name in @('manifest.json', 'fractals.parquet', 'bi.parquet', 'zhongshu.parquet', 'events.parquet', '_SUCCESS')) {
            if (-not (Test-Path -LiteralPath (Join-Path $chanCachePath $name))) { throw "Chan cache is missing $name." }
        }
        if (Test-Path -LiteralPath (Join-Path $chanCachePath 'segments.parquet')) { throw 'Chan cache unexpectedly contains segments.parquet.' }
        node "$projectRoot/web/scripts/validate-chan-cache.mjs" "$chanCachePath/manifest.json"
        if ($LASTEXITCODE -ne 0) { throw 'Chan cache manifest failed schema validation.' }
        $chanAudit = & $PythonExecutable "$projectRoot/python/scripts/validate_chan_cache.py" $chanCachePath
        if ($LASTEXITCODE -ne 0) { throw 'Chan Parquet/event/checkpoint audit failed.' }
        $chanAuditValue = $chanAudit | ConvertFrom-Json
        $chanEventCount = $chanAuditValue.event_count
        $chanCheckpointCount = $chanAuditValue.checkpoint_count
        $chanCached = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/calculations' `
            -Method Post -ContentType 'application/json' -Body $chanBody
        $chanCacheHit = $chanCached.status -eq 'completed' -and $chanCached.result_ref -eq $chanCompleted.result_ref
        if (-not $chanCacheHit) { throw 'Equivalent Chan calculation did not hit cache.' }
    }

    $replayCacheHit = $null
    $replayEventCount = $null
    if ($VerifyReplay) {
        $replayBodyValue = @{
            dataset_id = $meta.dataset_id
            data_revision = $revision
            strategy = @{
                kind = $chanAlgorithmRef.kind
                algorithm_id = $chanAlgorithmRef.algorithm_id
                algorithm_version = $chanAlgorithmRef.algorithm_version
                source_hash = $chanAlgorithmRef.source_hash
            }
            parameters = $chanParameters
            from_bar_index = 14017
            to_bar_index = 17016
            warmup_from_bar_index = 0
        }
        $replayBody = $replayBodyValue | ConvertTo-Json -Depth 10
        $replayCreated = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/replays' `
            -Method Post -ContentType 'application/json' -Body $replayBody
        if (-not $replayCreated.replay_id) { throw 'Replay creation did not return replay_id.' }
        $replayCompleted = Wait-Replay -ReplayId $replayCreated.replay_id -Deadline ((Get-Date).AddSeconds(120))
        $eventsBefore = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/replays/$($replayCreated.replay_id)/events?known_from_bar_index=0&known_to_bar_index=14016"
        $eventsAll = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/replays/$($replayCreated.replay_id)/events?known_from_bar_index=0&known_to_bar_index=17016"
        if ($eventsAll.checksum -notmatch '^sha256:[0-9a-f]{64}$' -or $eventsAll.event_count -lt 1) {
            throw 'Replay event response is empty or has an invalid checksum.'
        }
        if (@($eventsBefore.events | Where-Object { $_.known_at_bar_index -gt 14016 }).Count -ne 0) {
            throw 'Replay returned a future event before the cursor.'
        }
        if ($eventsBefore.event_count -ge $eventsAll.event_count) {
            throw 'Moving the replay cursor did not reveal later causal events.'
        }
        $replayEventCount = $eventsAll.event_count
        $replayCachePath = Join-Path $dataRoot $replayCompleted.result_ref
        foreach ($name in @('manifest.json', 'events.parquet', '_SUCCESS')) {
            if (-not (Test-Path -LiteralPath (Join-Path $replayCachePath $name))) { throw "Replay cache is missing $name." }
        }
        foreach ($name in @('fractals.parquet', 'bi.parquet', 'zhongshu.parquet', 'segments.parquet')) {
            if (Test-Path -LiteralPath (Join-Path $replayCachePath $name)) { throw "Replay cache unexpectedly contains $name." }
        }
        node "$projectRoot/web/scripts/validate-replay-cache.mjs" "$replayCachePath/manifest.json"
        if ($LASTEXITCODE -ne 0) { throw 'Replay cache manifest failed schema validation.' }
        & $PythonExecutable "$projectRoot/python/scripts/validate_replay_cache.py" $replayCachePath | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Replay event cache audit failed.' }
        $replayCached = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/replays' `
            -Method Post -ContentType 'application/json' -Body $replayBody
        $replayCacheHit = $replayCached.status -eq 'completed' -and $replayCached.result_ref -eq $replayCompleted.result_ref
        if (-not $replayCacheHit) { throw 'Equivalent replay did not hit cache.' }
    }

    $backtestTradeCount = $null
    $backtestRunSignatureMatch = $null
    $backtestSignalMatch = $null
    if ($VerifyBacktest) {
        $strategyAlgorithm = $algorithmResponse.algorithms | Where-Object kind -eq 'strategy'
        if (-not $strategyAlgorithm -or $strategyAlgorithm.algorithm_id -ne 'ma20_retest_short') {
            throw 'MA20 retest short strategy definition is missing.'
        }
        $strategyParameters = @{}
        foreach ($property in $strategyAlgorithm.parameter_schema.properties.PSObject.Properties) {
            $strategyParameters[$property.Name] = $property.Value.default
        }
        $strategyRef = @{
            kind = $strategyAlgorithm.kind; algorithm_id = $strategyAlgorithm.algorithm_id
            algorithm_version = $strategyAlgorithm.algorithm_version; source_hash = $strategyAlgorithm.source_hash
        }
        $backtestBodyValue = @{
            dataset_id = $meta.dataset_id; data_revision = $revision; strategy = $strategyRef
            parameters = $strategyParameters
            range = @{ warmup_from_bar_index = 0; from_bar_index = 100; to_bar_index = 17016 }
            execution = @{
                signal_timing = 'bar_close'; fill_timing = 'next_bar_open'
                commission = @{ mode = 'fixed_per_contract'; amount_i64 = 300; money_scale = 100 }
                slippage = @{ mode = 'ticks'; value = 1 }; contract_multiplier = 20
                margin_ratio = 0.12; intrabar_conflict_rule = 'worst_case'
            }
            capital = @{ initial_cash_i64 = 100000000; currency = 'CNY'; money_scale = 100 }
            random_seed = 20260801; trace_id = 'm7-smoke'
        }
        $backtestBody = $backtestBodyValue | ConvertTo-Json -Depth 12
        $runCreated = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/backtests' -Method Post `
            -ContentType 'application/json' -Headers @{ 'Idempotency-Key' = 'm7-smoke-first' } -Body $backtestBody
        $retry = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/backtests' -Method Post `
            -ContentType 'application/json' -Headers @{ 'Idempotency-Key' = 'm7-smoke-first' } -Body $backtestBody
        if ($retry.run_id -ne $runCreated.run_id) { throw 'Backtest idempotency retry created a second run.' }
        $runCompleted = Wait-Backtest -RunId $runCreated.run_id -Deadline ((Get-Date).AddSeconds(120))
        $runPath = Join-Path $dataRoot "runs/$($runCreated.run_id)"
        foreach ($name in @('run.json', 'status.json', 'summary.json', 'indicator_values.parquet', 'strategy_states.parquet', 'stage_signals.parquet', 'trade_signals.parquet', 'chart_events.parquet', 'orders.parquet', 'fills.parquet', 'trades.parquet', 'positions.parquet', 'equity.parquet', 'log.ndjson', '_SUCCESS')) {
            if (-not (Test-Path -LiteralPath (Join-Path $runPath $name))) { throw "Backtest run is missing $name." }
        }
        node "$projectRoot/web/scripts/validate-run-manifest.mjs" "$runPath/run.json"
        if ($LASTEXITCODE -ne 0) { throw 'Backtest run manifest failed schema validation.' }
        $audit = & $PythonExecutable "$projectRoot/python/scripts/validate_backtest_run.py" $runPath
        if ($LASTEXITCODE -ne 0) { throw 'Backtest run audit failed.' }
        $auditValue = $audit | ConvertFrom-Json
        if ($auditValue.trade_signal_count -lt 1 -or $auditValue.fill_count -lt 1) { throw 'Full sample produced no executable strategy signal.' }
        $summary = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/backtests/$($runCreated.run_id)/summary"
        $trades = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/backtests/$($runCreated.run_id)/trades"
        $equity = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/backtests/$($runCreated.run_id)/equity"
        $chartEvents = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/backtests/$($runCreated.run_id)/chart-events"
        if ($summary.run_id -ne $runCreated.run_id -or @($equity.rows).Count -ne 16917) { throw 'Backtest result APIs returned incomplete facts.' }
        $backtestTradeCount = $summary.trade_count

        $strategyReplayBody = @{
            dataset_id = $meta.dataset_id; data_revision = $revision; strategy = $strategyRef
            parameters = $strategyParameters; from_bar_index = 100; to_bar_index = 17016; warmup_from_bar_index = 0
        } | ConvertTo-Json -Depth 10
        $strategyReplay = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/replays' -Method Post -ContentType 'application/json' -Body $strategyReplayBody
        $strategyReplayCompleted = Wait-Replay -ReplayId $strategyReplay.replay_id -Deadline ((Get-Date).AddSeconds(120))
        $strategyReplayEvents = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/replays/$($strategyReplay.replay_id)/events?known_from_bar_index=0&known_to_bar_index=17016"
        $runSignals = @($chartEvents.events | Where-Object object_type -eq 'trade_signal' | ForEach-Object object_id)
        $replaySignals = @($strategyReplayEvents.events | Where-Object object_type -eq 'trade_signal' | ForEach-Object object_id)
        $backtestSignalMatch = ($runSignals -join ',') -eq ($replaySignals -join ',')
        if (-not $backtestSignalMatch) { throw 'Replay and backtest trade signals differ.' }

        $secondRun = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/backtests' -Method Post `
            -ContentType 'application/json' -Headers @{ 'Idempotency-Key' = 'm7-smoke-second' } -Body $backtestBody
        if ($secondRun.run_id -eq $runCreated.run_id) { throw 'A new formal backtest reused a completed run_id.' }
        $secondCompleted = Wait-Backtest -RunId $secondRun.run_id -Deadline ((Get-Date).AddSeconds(120))
        $backtestRunSignatureMatch = $secondRun.run_signature -eq $runCreated.run_signature
        if (-not $backtestRunSignatureMatch) { throw 'Equivalent formal runs have different run_signature.' }
        $firstFacts = (Get-FileHash -LiteralPath (Join-Path $runPath 'trade_signals.parquet') -Algorithm SHA256).Hash
        $secondFacts = (Get-FileHash -LiteralPath (Join-Path $dataRoot "runs/$($secondRun.run_id)/trade_signals.parquet") -Algorithm SHA256).Hash
        if ($firstFacts -ne $secondFacts) { throw 'Equivalent formal runs produced different signal fact hashes.' }
        if (-not (Test-Path -LiteralPath (Join-Path $runPath '_SUCCESS'))) { throw 'First completed run was overwritten.' }
    }

    $studyEvaluationCount = $null
    $studySelectedValidationRank = $null
    if ($VerifyOptimization) {
        $optimizationStrategy = $algorithmResponse.algorithms | Where-Object kind -eq 'strategy'
        if (-not $optimizationStrategy -or $optimizationStrategy.algorithm_id -ne 'ma20_retest_short') {
            throw 'Optimization strategy definition is missing.'
        }
        $optimizationParameters = @{}
        foreach ($property in $optimizationStrategy.parameter_schema.properties.PSObject.Properties) {
            $optimizationParameters[$property.Name] = $property.Value.default
        }
        $studyBody = @{
            dataset_id = $meta.dataset_id; data_revision = $revision
            strategy = @{
                kind = $optimizationStrategy.kind; algorithm_id = $optimizationStrategy.algorithm_id
                algorithm_version = $optimizationStrategy.algorithm_version; source_hash = $optimizationStrategy.source_hash
            }
            base_parameters = $optimizationParameters
            search_space = @(@{ name = 'ma_period'; type = 'integer'; candidates = @(10, 20, 30) })
            objectives = @(@{ metric = 'total_return'; direction = 'maximize' })
            constraints = @(@{ metric = 'trade_count'; operator = '>='; value = 1 })
            search = @{ method = 'random'; budget = 3; random_seed = 20260801 }
            ranges = @{
                train = @{ warmup_from_bar_index = 0; from_bar_index = 100; to_bar_index = 10000 }
                validation = @{ warmup_from_bar_index = 0; from_bar_index = 10001; to_bar_index = 17016 }
            }
            execution = @{
                signal_timing = 'bar_close'; fill_timing = 'next_bar_open'
                commission = @{ mode = 'fixed_per_contract'; amount_i64 = 300; money_scale = 100 }
                slippage = @{ mode = 'ticks'; value = 1 }; contract_multiplier = 20
                margin_ratio = 0.12; intrabar_conflict_rule = 'worst_case'
            }
            capital = @{ initial_cash_i64 = 100000000; currency = 'CNY'; money_scale = 100 }
            trace_id = 'm9-smoke'
        } | ConvertTo-Json -Depth 14
        $studyCreated = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/studies' -Method Post -ContentType 'application/json' -Body $studyBody
        if (-not $studyCreated.study_id) { throw 'Optimization submission did not return study_id.' }
        $studyCompleted = Wait-Study -StudyId $studyCreated.study_id -Deadline ((Get-Date).AddSeconds(180))
        $studyResults = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/studies/$($studyCreated.study_id)/evaluations"
        $studyEvaluationCount = @($studyResults.evaluations).Count
        $studyRanks = @($studyResults.evaluations.train_rank | Sort-Object)
        if ($studyEvaluationCount -ne 3 -or ($studyRanks -join ',') -ne '1,2,3') {
            throw "Optimization results are incomplete: $($studyResults | ConvertTo-Json -Depth 5 -Compress)"
        }
        if ($studyResults.stability.constraint_feasible_count -lt 1) { throw 'No optimization candidate satisfied the hard constraint.' }
        $studySelectedValidationRank = $studyResults.stability.selected_validation_rank
        $studyPath = Join-Path $dataRoot $studyCompleted.result_ref
        foreach ($name in @('study.json', 'evaluations.json', 'stability.json', 'log.ndjson', '_SUCCESS')) {
            if (-not (Test-Path -LiteralPath (Join-Path $studyPath $name))) { throw "Optimization study is missing $name." }
        }
        node "$projectRoot/web/scripts/validate-study-manifest.mjs" "$studyPath/study.json"
        if ($LASTEXITCODE -ne 0) { throw 'Optimization study manifest failed schema validation.' }
        foreach ($evaluation in $studyResults.evaluations) {
            foreach ($runId in @($evaluation.train_run_id, $evaluation.validation_run_id)) {
                if (-not (Test-Path -LiteralPath (Join-Path $dataRoot "runs/$runId/_SUCCESS"))) {
                    throw "Optimization formal run $runId is incomplete."
                }
            }
        }
    }

    $workspaceRevision = $null
    if ($VerifyWorkspace) {
        [object[]]$seriesSources = @()
        if ($maAlgorithmRef) {
            $seriesSources = @([pscustomobject]@{
                source_id = 'series-ma-smoke'; name = 'Moving Average'; pane_id = 'price'; visible = $true; locked = $false
                z_band = 400; order_in_band = 0; dataset_id = $meta.dataset_id; data_revision = $revision
                algorithm = @{ kind = $maAlgorithmRef.kind; algorithm_id = $maAlgorithmRef.algorithm_id; algorithm_version = $maAlgorithmRef.algorithm_version; source_hash = $maAlgorithmRef.source_hash }
                parameters = @{ period = 20; source = 'close' }
            })
        }
        [object[]]$strategySources = @()
        if ($chanAlgorithmRef) {
            $strategySources = @([pscustomobject]@{
                source_id = 'strategy-chan-smoke'; name = $chanAlgorithmRef.name; pane_id = 'price'; visible = $true; locked = $true
                z_band = 500; order_in_band = 0; dataset_id = $meta.dataset_id; data_revision = $revision
                algorithm = @{ kind = $chanAlgorithmRef.kind; algorithm_id = $chanAlgorithmRef.algorithm_id; algorithm_version = $chanAlgorithmRef.algorithm_version; source_hash = $chanAlgorithmRef.source_hash }
                parameters = $chanParameters; category_visibility = @{ fractals = $true; bi = $true; zhongshu = $true }
            })
        }
        $layoutBody = @{
            schema_version = 1; layout_id = 'default-three-pane'; profile_id = 'default'; revision = 1
            panes = @(
                @{ id = 'price'; role = 'price'; weight = 6; min_height = 240; visible = $true; collapsed = $false; order = 0 },
                @{ id = 'macd'; role = 'indicator'; weight = 1; min_height = 80; visible = $true; collapsed = $false; order = 1 },
                @{ id = 'volume'; role = 'indicator'; weight = 1; min_height = 80; visible = $true; collapsed = $false; order = 2 }
            )
            right_panel = @{ width = 400; collapsed = $false; active_tab = 'object_tree' }
            bottom_panel = @{ height = 300; collapsed = $true; active_tab = 'tasks' }
            object_order = @(@{ id = 'series-candles'; pane_id = 'price'; z_band = 300; order_in_band = 0; visible = $true; locked = $true })
            series_sources = $seriesSources
            strategy_sources = $strategySources
            updated_at = '2026-08-01T00:00:00Z'
        } | ConvertTo-Json -Depth 12
        $layoutUri = 'http://127.0.0.1:8080/api/v1/workspaces/default/layouts/default-three-pane'
        $savedLayout = Invoke-RestMethod -Uri $layoutUri -Method Put -ContentType 'application/json' -Headers @{ 'If-Match' = '0' } -Body $layoutBody
        if ($savedLayout.revision -ne 1 -or $savedLayout.right_panel.width -ne 400) { throw 'Layout save did not return revision 1.' }
        $drawingBody = @{
            schema_version = 1; profile_id = 'default'; layout_id = 'default-three-pane'
            dataset_id = $meta.dataset_id; data_revision = $revision; revision = 1
            drawings = @(@{
                id = 'drawing-smoke'; name = '矩形 1'; type = 'rectangle'; pane_id = 'main'; visible = $true; locked = $false
                z_band = 600; order_in_band = 0; style = @{ color = '#2962ff'; line_width = 1; fill_opacity = 0.15 }
                anchors = @(
                    @{ time = [int64]$meta.coverage.first_timestamp_utc; price_i64 = 2351; price_scale = 1 },
                    @{ time = [int64]$meta.coverage.last_timestamp_utc; price_i64 = 2386; price_scale = 1 }
                )
                revision = 1; created_at = '2026-08-01T00:00:00Z'; updated_at = '2026-08-01T00:00:00Z'
            })
            updated_at = '2026-08-01T00:00:00Z'
        } | ConvertTo-Json -Depth 12
        $drawingUri = "http://127.0.0.1:8080/api/v1/workspaces/default/drawings/default-three-pane/$($meta.dataset_id)"
        $savedDrawings = Invoke-RestMethod -Uri $drawingUri -Method Put -ContentType 'application/json' -Headers @{ 'If-Match' = '0' } -Body $drawingBody
        $restoredLayout = Invoke-RestMethod -Uri $layoutUri
        $restoredDrawings = Invoke-RestMethod -Uri $drawingUri
        if ($savedDrawings.revision -ne 1 -or $restoredDrawings.drawings[0].anchors[0].PSObject.Properties['x'] -or $restoredDrawings.drawings[0].anchors[0].PSObject.Properties['y']) {
            throw 'Drawing anchors were not restored with time/fixed-price semantics.'
        }
        $conflictStatus = 0
        try { Invoke-WebRequest -UseBasicParsing -Uri $layoutUri -Method Put -ContentType 'application/json' -Headers @{ 'If-Match' = '0' } -Body $layoutBody | Out-Null }
        catch { $conflictStatus = [int]$_.Exception.Response.StatusCode }
        if ($conflictStatus -ne 409) { throw "Stale workspace save returned HTTP $conflictStatus instead of 409." }
        $layoutPath = Join-Path $dataRoot 'workspaces/default/layouts/default-three-pane.json'
        $drawingPath = Join-Path $dataRoot "workspaces/default/drawings/default-three-pane/$($meta.dataset_id).json"
        node "$projectRoot/web/scripts/validate-workspace.mjs" $layoutPath $drawingPath
        if ($LASTEXITCODE -ne 0) { throw 'Saved workspace failed schema validation.' }
        $workspaceRevision = $restoredLayout.revision
    }

    $metaRelative = ($meta.files | Where-Object role -eq 'bars').path.Replace('bars.parquet', 'meta.json')
    $metaPath = Join-Path $dataRoot $metaRelative
    $successPath = Join-Path (Split-Path -Parent $metaPath) '_SUCCESS'
    if (-not (Test-Path -LiteralPath $successPath)) { throw 'Committed dataset is missing _SUCCESS.' }
    node "$projectRoot/web/scripts/validate-dataset-meta.mjs" $metaPath
    if ($LASTEXITCODE -ne 0) { throw 'Generated meta.json failed schema validation.' }

    $secondImport = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets/import' `
        -Method Post -ContentType 'application/json' -Body $importBody
    Wait-Job -JobId $secondImport.job_id -Deadline $deadline | Out-Null
    $secondCatalog = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/datasets'
    $sourcesAfter = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/source-files'
    $sourceHashAfter = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
    if ($secondCatalog.catalog_revision -ne 1 -or $sourcesAfter.items[0].status -ne 'imported') {
        throw 'Repeat import was not idempotent or source status was not updated.'
    }
    if ($sourceHashAfter -ne $sourceHashBefore) { throw 'Raw source file changed during import.' }
    if ($elapsedMs -gt 5000) { throw "Full sample API import exceeded 5 seconds: ${elapsedMs}ms" }

    $cacheCleanupCount = $null
    if ($VerifyRecovery) {
        $cleanup = & "$projectRoot/bin/cachectl.exe" -config $appConfigPath -kind replay -dry-run=false | ConvertFrom-Json
        if ($cleanup.dry_run -or $cleanup.count -lt 1) {
            throw "Cache cleanup did not move a replay cache: $($cleanup | ConvertTo-Json -Compress)"
        }
        foreach ($move in $cleanup.moves) {
            if (Test-Path -LiteralPath (Join-Path $dataRoot $move.source)) { throw "Cache remained at $($move.source)." }
            if (-not (Test-Path -LiteralPath (Join-Path $dataRoot $move.destination))) { throw "Trash target missing at $($move.destination)." }
        }
        $cacheCleanupCount = $cleanup.count
    }

    [pscustomobject]@{
        dataset_id = $meta.dataset_id
        data_revision = $revision
        bar_count = $meta.coverage.bar_count
        zero_volume_count = $meta.quality.zero_volume_count
        catalog_revision = $secondCatalog.catalog_revision
        source_status = $sourcesAfter.items[0].status
        import_elapsed_ms = $elapsedMs
        bars_tail_count = if ($VerifyBars) { 3000 } else { $null }
        bars_prefetch_count = $prefetchCount
        bars_hot_p95_ms = $barsP95
        metadata_schema = 'valid'
        source_unchanged = $true
        indicator_algorithms = $indicatorAlgorithms
        indicator_cache_hit = $indicatorCacheHit
        chan_cache_hit = $chanCacheHit
        chan_object_count = $chanObjectCount
        chan_event_count = $chanEventCount
        chan_checkpoint_count = $chanCheckpointCount
        replay_cache_hit = $replayCacheHit
        replay_event_count = $replayEventCount
        backtest_trade_count = $backtestTradeCount
        backtest_signature_match = $backtestRunSignatureMatch
        replay_backtest_signal_match = $backtestSignalMatch
        study_evaluation_count = $studyEvaluationCount
        study_selected_validation_rank = $studySelectedValidationRank
        workspace_revision = $workspaceRevision
        restart_recovery = $restartRecovery
        cache_cleanup_count = $cacheCleanupCount
    } | ConvertTo-Json -Compress
    if ($HoldSeconds -gt 0) {
        Write-Host "Holding API for browser verification on http://127.0.0.1:8080 for $HoldSeconds seconds."
        Start-Sleep -Seconds $HoldSeconds
    }
} catch {
    $stderr = if (Test-Path -LiteralPath $chartdStderr) { Get-Content -Raw $chartdStderr } else { '' }
    $stdout = if (Test-Path -LiteralPath $chartdStdout) { Get-Content -Raw $chartdStdout } else { '' }
    $appLog = Join-Path $dataRoot 'logs/go/app.ndjson'
    $runtimeLog = if (Test-Path -LiteralPath $appLog) { Get-Content -Raw $appLog } else { '' }
    $pythonError = if ($needsPython -and (Test-Path -LiteralPath $pythonStderr)) { Get-Content -Raw $pythonStderr } else { '' }
    $pythonLogPath = Join-Path $dataRoot 'logs/python/strategy.ndjson'
    $pythonRuntimeLog = if ($needsPython -and (Test-Path -LiteralPath $pythonLogPath)) { Get-Content -Raw $pythonLogPath } else { '' }
    Write-Host "chartd stdout: $stdout"
    Write-Host "chartd stderr: $stderr"
    Write-Host "chartd runtime log: $runtimeLog"
    if ($needsPython) {
        Write-Host "python stderr: $pythonError"
        Write-Host "python runtime log: $pythonRuntimeLog"
    }
    throw
} finally {
    if ($chartd) { Stop-ExactProcessTree -TargetProcessId $chartd.Id }
    if ($pythonEngine) { Stop-ExactProcessTree -TargetProcessId $pythonEngine.Id }
    if ((Test-Path -LiteralPath $tempRoot) -and $tempRoot.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) {
        for ($attempt = 1; $attempt -le 10; $attempt++) {
            try {
                Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction Stop
                break
            } catch {
                if ($attempt -eq 10) { throw }
                Start-Sleep -Milliseconds 100
            }
        }
    }
}
