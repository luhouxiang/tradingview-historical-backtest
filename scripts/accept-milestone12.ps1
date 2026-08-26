param(
    [string]$BaseUrl = 'http://127.0.0.1:8080',
    [int]$TimeoutSeconds = 7200
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

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

function Wait-TvbtJob {
    param([string]$JobId)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $job = Invoke-TvbtApi -Method GET -Path "/api/v1/jobs/$JobId"
        if ($job.status -in @('completed', 'failed', 'cancelled', 'interrupted')) {
            if ($job.status -ne 'completed') {
                throw "Job $JobId ended as $($job.status): $($job.error.message)"
            }
            return $job
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Job $JobId exceeded ${TimeoutSeconds}s"
}

function Wait-ResearchStudy {
    param([string]$StudyId)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $status = Invoke-TvbtApi -Method GET -Path "/api/v1/research-studies/$StudyId"
        if ($status.status -in @('completed', 'failed', 'cancelled', 'interrupted')) {
            if ($status.status -ne 'completed') {
                throw "Research study $StudyId ended as $($status.status): $($status.error.message)"
            }
            return $status
        }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Research study $StudyId exceeded ${TimeoutSeconds}s"
}

$health = Invoke-TvbtApi -Method GET -Path '/api/v1/health'
$pythonHealth = $health.services.'python-engine'
if ($health.status -ne 'ok' -or $null -eq $pythonHealth -or $pythonHealth.status -ne 'ok') {
    throw 'Go and Python must both be healthy before milestone 12 acceptance.'
}

$scan = Invoke-TvbtApi -Method POST -Path '/api/v1/datasets/scan'
Wait-TvbtJob -JobId $scan.job_id | Out-Null
$sources = (Invoke-TvbtApi -Method GET -Path '/api/v1/source-files').items
$wantedSymbols = @('YL9', 'AOL9')
$selectedSources = @($sources | Where-Object { $_.detected.symbol -in $wantedSymbols })
if ($selectedSources.Count -ne 2) {
    throw "Expected local YL9 and AOL9 5-minute source files, found $($selectedSources.Count)."
}
$importItems = @($selectedSources | ForEach-Object {
    @{
        source_file_id = $_.source_file_id
        importer_id = 'tdx_txt_v1'
        exchange = $_.detected.exchange
        instrument = $_.detected.symbol
        timeframe = $_.detected.timeframe
        date_semantics = if ($_.detected.date_semantics) { $_.detected.date_semantics } else { 'trading_day' }
        timezone = if ($_.detected.timezone) { $_.detected.timezone } else { 'Asia/Shanghai' }
        timestamp_semantics = if ($_.detected.timestamp_semantics) { $_.detected.timestamp_semantics } else { 'bar_end' }
    }
})
$import = Invoke-TvbtApi -Method POST -Path '/api/v1/datasets/import-batch' -Body @{ items = $importItems }
Wait-TvbtJob -JobId $import.job_id | Out-Null

$catalog = Invoke-TvbtApi -Method GET -Path '/api/v1/datasets'
$datasetIds = @('DCE.YL9.5m', 'SHFE.AOL9.5m')
$datasets = @($datasetIds | ForEach-Object {
    $summary = $catalog.datasets | Where-Object dataset_id -eq $_
    if ($null -eq $summary) { throw "Imported dataset $_ is missing from catalog." }
    Invoke-TvbtApi -Method GET -Path "/api/v1/datasets/$([uri]::EscapeDataString($_))?revision=$([uri]::EscapeDataString($summary.active_revision))"
})
if (@($datasets | Where-Object { $_.coverage.trading_day_count -lt 504 }).Count -ne 0) {
    throw 'YL9 and AOL9 full-history imports must each contain at least 504 trading days.'
}

$algorithms = Invoke-TvbtApi -Method GET -Path '/api/v1/algorithms'
$strategy = $algorithms.algorithms | Where-Object {
    $_.algorithm_id -eq 'second_buy_only' -and $_.research_role -eq 'formal_strategy' -and $_.comparison_eligible
} | Select-Object -First 1
if ($null -eq $strategy) { throw 'The formal second_buy_only strategy is unavailable.' }
$request = @{
    datasets = @($datasets | ForEach-Object {
        @{
            dataset_id = $_.dataset_id
            data_revision = $_.data_revision
            range = @{
                warmup_from_bar_index = $_.coverage.first_bar_index
                from_bar_index = $_.coverage.first_bar_index
                to_bar_index = $_.coverage.last_bar_index
            }
        }
    })
    strategy = @{
        kind = 'strategy'
        algorithm_id = $strategy.algorithm_id
        algorithm_version = $strategy.algorithm_version
        source_hash = $strategy.source_hash
    }
    parameters = @{
        allow_normal = $true
        allow_strongest = $true
        allow_weakest = $true
        checkpoint_interval = 1024
        normal_quantity = 2
        strongest_quantity = 2
        weakest_quantity = 1
    }
    execution = @{
        signal_timing = 'bar_close'
        fill_timing = 'next_bar_open'
        commission = @{ mode = 'fixed_per_contract'; amount_i64 = 300; money_scale = 100 }
        slippage = @{ mode = 'ticks'; value = 1 }
        contract_multiplier = 1
        margin_ratio = 0.1
        intrabar_conflict_rule = 'worst_case'
    }
    capital = @{ initial_cash_i64 = 100000000; currency = 'CNY'; money_scale = 100 }
    random_seed = 20260824
    walk_forward = @{
        train_trading_days = 252
        validation_trading_days = 63
        step_trading_days = 63
        search_space = @(@{ name = 'normal_quantity'; type = 'integer'; candidates = @(1, 2, 3) })
        objectives = @(@{ metric = 'total_return'; direction = 'maximize' })
        constraints = @()
        search = @{ method = 'grid'; budget = 3; random_seed = 20260824 }
    }
    stress_test = @{ suite_version = '1.0.0'; volume_participation_rate = 0.1 }
    statistical_validation = @{
        method_version = '1.0.0'
        block_size_trading_days = 5
        iterations = 2000
        confidence_level = 0.95
        random_seed = 20260824
        holm_alpha = 0.05
    }
}
$accepted = Invoke-TvbtApi -Method POST -Path '/api/v1/research-studies' -Body $request
$status = Wait-ResearchStudy -StudyId $accepted.research_study_id
$results = Invoke-TvbtApi -Method GET -Path "/api/v1/research-studies/$($accepted.research_study_id)/results"
$aggregate = $results.aggregate

if ($status.manifest.study_mode -ne 'walk_forward_certification') { throw 'Certification study mode was not persisted.' }
if (@($aggregate.stress_scenarios).Count -ne 7) { throw 'The fixed seven-scenario stress suite is incomplete.' }
if ($aggregate.statistical_evidence.bootstrap.iterations -ne 2000) { throw 'The 2,000-iteration bootstrap was not persisted.' }
if (@($aggregate.attempted_parameter_combinations).Count -ne 3) { throw 'All attempted parameter combinations were not recorded.' }
if ($aggregate.statistical_evidence.parameter_neighborhood.evaluated_neighbor_count -lt 1) { throw 'No adjacent parameter neighborhood was evaluated.' }
if ($aggregate.certification.tier -eq 'reliable_candidate') { throw 'Two independent groups must not receive reliable-candidate certification.' }
$groupGate = $aggregate.certification.evidence_matrix | Where-Object gate_id -eq 'minimum_eligible_independence_groups'
if ($null -eq $groupGate -or $groupGate.passed) { throw 'The insufficient-independent-groups reason is missing.' }

$report = @{
    accepted_at = [DateTime]::UtcNow.ToString('o')
    research_study_id = $accepted.research_study_id
    datasets = @($datasets | ForEach-Object { @{ dataset_id = $_.dataset_id; data_revision = $_.data_revision; trading_day_count = $_.coverage.trading_day_count; independence_group = $_.independence_group } })
    study_mode = $status.manifest.study_mode
    certification = $aggregate.certification
    stress_scenario_count = @($aggregate.stress_scenarios).Count
    bootstrap = $aggregate.statistical_evidence.bootstrap
    attempted_parameter_combination_count = @($aggregate.attempted_parameter_combinations).Count
    parameter_neighborhood = $aggregate.statistical_evidence.parameter_neighborhood
}
$acceptanceDirectory = Join-Path $projectRoot 'trading-data/acceptance'
New-Item -ItemType Directory -Force -Path $acceptanceDirectory | Out-Null
$reportPath = Join-Path $acceptanceDirectory 'milestone12-real-e2e.json'
$temporaryPath = "$reportPath.tmp"
[IO.File]::WriteAllText($temporaryPath, ($report | ConvertTo-Json -Depth 30), [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temporaryPath -Destination $reportPath -Force
Write-Host "Milestone 12 real-data E2E passed: $reportPath"
