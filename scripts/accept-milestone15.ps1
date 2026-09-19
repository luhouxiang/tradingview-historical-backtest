param(
    [string]$BaseUrl = 'http://127.0.0.1:8080',
    [int]$TimeoutSeconds = 900,
    [string]$FullCacheRef = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot/python-runtime.ps1"

function Invoke-TvbtApi {
    param([string]$Method, [string]$Path, [object]$Body = $null)
    $headers = @{
        'Accept' = 'application/json'
        'X-Request-ID' = [guid]::NewGuid().ToString('N')
        'X-Trace-ID' = [guid]::NewGuid().ToString('N')
    }
    $arguments = @{ Method = $Method; Uri = "$BaseUrl$Path"; Headers = $headers }
    if ($null -ne $Body) {
        $arguments.ContentType = 'application/json'
        $arguments.Body = $Body | ConvertTo-Json -Depth 30 -Compress
    }
    return Invoke-RestMethod @arguments
}

function Wait-Replay {
    param([string]$ReplayId)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $status = Invoke-TvbtApi -Method GET -Path "/api/v1/replays/$ReplayId"
        if ($status.status -in @('completed', 'failed', 'cancelled', 'interrupted')) {
            if ($status.status -ne 'completed') {
                throw "Replay $ReplayId ended as $($status.status): $($status.error.message)"
            }
            return $status
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Replay $ReplayId exceeded ${TimeoutSeconds}s"
}

$health = Invoke-TvbtApi -Method GET -Path '/api/v1/health'
if ($health.status -ne 'ok' -or $health.services.'python-engine'.status -ne 'ok') {
    throw 'Go and Python must both be healthy before milestone 15 acceptance.'
}
$catalog = Invoke-TvbtApi -Method GET -Path '/api/v1/datasets'
$dataset = $catalog.datasets | Where-Object dataset_id -eq 'SHFE.AOL9.5m' | Select-Object -First 1
$expectedRevision = 'sha256:2904362e62173a418d63feaf8855a3aef4b61ce5b0027e7801785baf9d8302fe'
if ($null -eq $dataset -or $dataset.active_revision -ne $expectedRevision) {
    throw 'The accepted AOL9 revision is not active.'
}
$algorithms = (Invoke-TvbtApi -Method GET -Path '/api/v1/algorithms').algorithms
$chan = $algorithms | Where-Object { $_.kind -eq 'chan' -and $_.algorithm_id -eq 'chan_engineering' } | Select-Object -First 1
if ($null -eq $chan -or $chan.algorithm_version -ne '17.0.0') {
    throw 'Chan 17.0.0 is not available through the public API.'
}
$parameters = @{}
foreach ($property in $chan.parameter_schema.properties.PSObject.Properties) {
    $parameters[$property.Name] = $property.Value.default
}
if ($parameters.center_boundary_profile -ne 'local_center_boundary_v1') {
    throw 'The local center boundary profile is not the default.'
}
$request = @{
    dataset_id = $dataset.dataset_id
    data_revision = $dataset.active_revision
    strategy = @{
        kind = $chan.kind
        algorithm_id = $chan.algorithm_id
        algorithm_version = $chan.algorithm_version
        source_hash = $chan.source_hash
    }
    parameters = $parameters
    from_bar_index = 3479
    to_bar_index = 5200
    warmup_from_bar_index = 0
}
$created = Invoke-TvbtApi -Method POST -Path '/api/v1/replays' -Body $request
$completed = Wait-Replay -ReplayId $created.replay_id
$before = Invoke-TvbtApi -Method GET -Path "/api/v1/replays/$($created.replay_id)/events?known_from_bar_index=0&known_to_bar_index=5100"
$after = Invoke-TvbtApi -Method GET -Path "/api/v1/replays/$($created.replay_id)/events?known_from_bar_index=0&known_to_bar_index=5200"
if ($before.event_count -ge $after.event_count) {
    throw 'Advancing the replay cursor did not reveal later causal events.'
}
if (@($before.events | Where-Object { $_.known_at_bar_index -gt 5100 }).Count -ne 0) {
    throw 'Replay returned a future event before the K5100 cursor.'
}
$replayCache = Join-Path "$projectRoot/trading-data" $completed.result_ref
foreach ($name in @('manifest.json', 'events.parquet', '_SUCCESS')) {
    if (-not (Test-Path -LiteralPath (Join-Path $replayCache $name))) {
        throw "Replay cache is missing $name."
    }
}
node "$projectRoot/web/scripts/validate-replay-cache.mjs" "$replayCache/manifest.json"
if ($LASTEXITCODE -ne 0) { throw 'Replay cache contract validation failed.' }
& $PythonExecutable "$projectRoot/python/scripts/validate_replay_cache.py" $replayCache | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Replay cache Parquet validation failed.' }

$reportPath = "$projectRoot/trading-data/acceptance/milestone15-aol9-center-boundary.json"
$exportArguments = @("$projectRoot/python/scripts/export_milestone15_acceptance.py", '--output', $reportPath)
if ($FullCacheRef) { $exportArguments += @('--full-cache-ref', $FullCacheRef) }
& $PythonExecutable @exportArguments
if ($LASTEXITCODE -ne 0) { throw 'AOL9 center-boundary evidence validation failed.' }
$report = Get-Content -Raw -Encoding utf8 $reportPath | ConvertFrom-Json
$report | Add-Member -NotePropertyName full_stack_replay -NotePropertyValue ([ordered]@{
    replay_id = $created.replay_id
    result_ref = $completed.result_ref
    event_count_at_k5100 = $before.event_count
    event_count_at_k5200 = $after.event_count
    checksum_at_k5200 = $after.checksum
    future_event_guard = $true
}) -Force
$temporaryPath = "$reportPath.tmp"
[IO.File]::WriteAllText($temporaryPath, ($report | ConvertTo-Json -Depth 100), [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temporaryPath -Destination $reportPath -Force
Write-Host "Milestone 15 real-data acceptance passed: $reportPath"
