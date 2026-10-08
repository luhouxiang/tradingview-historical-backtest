param(
    [string]$BaseUrl = 'http://127.0.0.1:8080',
    [string]$DatasetId = 'SHFE.AOL9.5m',
    [long]$EndBarIndex = -1,
    [int]$TimeoutSeconds = 1800,
    [switch]$RunBatchComparison
)
$ErrorActionPreference = 'Stop'
function Call-Api([string]$Method, [string]$Path, $Body = $null) {
    $arguments = @{Method=$Method; Uri="$BaseUrl$Path"; TimeoutSec=30; Headers=@{'X-Request-ID'=[guid]::NewGuid().ToString('N'); 'X-Trace-ID'=[guid]::NewGuid().ToString('N')}}
    if ($null -ne $Body) { $arguments.ContentType='application/json'; $arguments.Body=$Body | ConvertTo-Json -Depth 30 -Compress }
    Invoke-RestMethod @arguments
}
function Wait-Training($Accepted) {
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $state=Call-Api GET "/api/v1/training-sessions/$($Accepted.session_id)"
        if ($state.job_id -ne $Accepted.job_id) { throw 'Training command identity changed' }
        if ($state.status -eq 'completed') { return $state.snapshot }
        if ($state.status -in @('failed','cancelled','interrupted')) { throw "Training failed: $($state.error | ConvertTo-Json -Compress)" }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Training command timeout'
}
function Command-Training($Snapshot, [string]$Action, [long]$Count=0, [long]$Target=0) {
    $command=@{session_id=$Snapshot.session_id;operation_id=[guid]::NewGuid().ToString('N');action=$Action;expected_cursor=$Snapshot.cursor}
    if ($Action -eq 'advance') { $command.count=$Count }
    if ($Action -eq 'seek') { $command.target_bar_index=$Target }
    $accepted=Call-Api POST "/api/v1/training-sessions/$($Snapshot.session_id)/commands" $command
    $result=Wait-Training $accepted
    $duplicate=Call-Api POST "/api/v1/training-sessions/$($Snapshot.session_id)/commands" $command
    if ($duplicate.job_id -ne $accepted.job_id) { throw 'Duplicate command created a new task' }
    foreach ($event in $result.events) { if ($event.known_at_bar_index -gt $result.cursor) { throw 'Future event leaked' } }
    if ($result.account.known_at_bar_index -ne $result.cursor) { throw 'Account cursor mismatch' }
    return $result
}
$health=Call-Api GET '/api/v1/health'
if ($health.status -ne 'ok') { throw 'Both servers must be healthy' }
$listed=(Call-Api GET '/api/v1/datasets').datasets | Where-Object dataset_id -eq $DatasetId | Select-Object -First 1
if ($null -eq $listed) { throw 'Dataset not listed' }
$meta=Call-Api GET "/api/v1/datasets/$DatasetId`?revision=$([uri]::EscapeDataString($listed.active_revision))"
$definition=(Call-Api GET '/api/v1/algorithms').algorithms | Where-Object algorithm_id -eq 'divergence_segment_end_simulation' | Select-Object -First 1
if ($null -eq $definition -or $null -eq $meta.coverage) { throw 'Dataset or simulation strategy unavailable' }
if ($EndBarIndex -lt 0) { $EndBarIndex=$meta.coverage.last_bar_index }
$request=@{
    dataset_id=$meta.dataset_id;data_revision=$meta.data_revision
    strategy=@{kind='strategy';algorithm_id=$definition.algorithm_id;algorithm_version=$definition.algorithm_version;source_hash=$definition.source_hash}
    parameters=@{checkpoint_interval=1024;enable_trend=$true;enable_consolidation=$true;enable_oscillation=$true;operation_quantity=1;allow_short=$true}
    range=@{warmup_from_bar_index=$meta.coverage.first_bar_index;from_bar_index=$meta.coverage.first_bar_index;to_bar_index=$EndBarIndex}
    capital=@{initial_cash_i64=100000000;currency='CNY';money_scale=100};random_seed=16
    execution=@{semantic_version='1.0.0';signal_timing='bar_close';fill_timing='next_bar_open';commission=@{mode='fixed_per_contract';amount_i64=300;money_scale=100};slippage=@{mode='ticks';value=1};margin_ratio=0.12;intrabar_conflict_rule='worst_case'}
}
$create=@{operation_id=[guid]::NewGuid().ToString('N');backtest=$request}
$accepted=Call-Api POST '/api/v1/training-sessions' $create
$snapshot=Wait-Training $accepted
if ($snapshot.cursor -ne $request.range.from_bar_index -or $snapshot.processed_until_bar_index -ne $snapshot.cursor) { throw 'Creation executed beyond its start' }
$duplicate=Call-Api POST '/api/v1/training-sessions' $create
if ($duplicate.session_id -ne $accepted.session_id -or $duplicate.job_id -ne $accepted.job_id) { throw 'Creation was not idempotent' }
Start-Sleep -Milliseconds 300
$paused=Call-Api GET "/api/v1/training-sessions/$($accepted.session_id)"
if ($paused.snapshot.processed_until_bar_index -ne $snapshot.cursor) { throw 'Paused session advanced' }
Write-Output "Training $($accepted.session_id) prepared at K$($snapshot.cursor), source $($definition.source_hash)"
$lookbackChecked=$false
while ($snapshot.state -ne 'completed') {
    $snapshot=Command-Training $snapshot 'advance' ([Math]::Min(100,$EndBarIndex-$snapshot.cursor))
    if (-not $lookbackChecked -and $snapshot.cursor -ge 1000 -and $snapshot.state -ne 'completed') {
        $previous=$snapshot
        $snapshot=Command-Training $snapshot 'seek' 0 $request.range.from_bar_index
        if ($snapshot.account.position_quantity -ne 0 -or $snapshot.processed_until_bar_index -ne $previous.processed_until_bar_index) { throw 'Lookback mutated execution head' }
        while ($snapshot.cursor -lt $previous.cursor) { $snapshot=Command-Training $snapshot 'advance' ([Math]::Min(100,$previous.cursor-$snapshot.cursor)) }
        if ($snapshot.account.total_commission_i64 -ne $previous.account.total_commission_i64 -or $snapshot.account.equity_i64 -ne $previous.account.equity_i64) { throw 'Lookback replay changed account or fees' }
        $lookbackChecked=$true
    }
    if ($snapshot.cursor % 5000 -eq 0) { Write-Output "Processed K$($snapshot.cursor), closed $($snapshot.account.closed_trade_count), fees $($snapshot.account.total_commission_i64)" }
}
Write-Output "Training completed: $($snapshot.completed_run_ref), equity $($snapshot.account.equity_i64), closed $($snapshot.account.closed_trade_count)"
if ($RunBatchComparison) {
    $batch=Call-Api POST '/api/v1/backtests' $request
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $status=Call-Api GET "/api/v1/backtests/$($batch.run_id)"
        if ($status.status -in @('failed','cancelled','interrupted')) { throw 'Comparison backtest failed' }
        if ($status.status -eq 'completed') { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($status.status -ne 'completed') { throw 'Comparison backtest timeout' }
    if ($status.run_signature -ne $snapshot.run_signature) { throw 'Training and batch normalized semantics differ' }
    Write-Output "Compare run: $($batch.run_id), same signature $($snapshot.run_signature)"
}
