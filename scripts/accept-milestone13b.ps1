$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot/python-runtime.ps1"
$started = Get-Date

& "$PSScriptRoot/check-versions.ps1"
& "$PSScriptRoot/generate-contracts.ps1"

Push-Location $projectRoot
try {
    go test ./internal/backtest ./internal/comparison ./internal/optimization ./internal/research ./internal/importer ./internal/api
    if ($LASTEXITCODE -ne 0) { throw "Go 13B tests failed with exit code $LASTEXITCODE." }

    $env:PYTHONPATH = "$projectRoot/python/src"
    & $PythonExecutable -m pytest `
        python/tests/test_execution_contract.py `
        python/tests/test_comparison.py `
        python/tests/test_optimization.py `
        python/tests/test_research.py `
        python/tests/test_walk_forward.py `
        python/tests/test_stress_test.py -q
    if ($LASTEXITCODE -ne 0) { throw "Python 13B tests failed with exit code $LASTEXITCODE." }

    Push-Location web
    try {
        npm run typecheck
        if ($LASTEXITCODE -ne 0) { throw "Vue typecheck failed with exit code $LASTEXITCODE." }
        npx vitest run `
            src/execution/config.test.ts `
            src/components/BacktestPanel.test.ts `
            src/components/OptimizationPanel.test.ts `
            src/components/StrategyResearchPanel.test.ts `
            src/components/MultiDatasetResearchPanel.test.ts --reporter=dot
        if ($LASTEXITCODE -ne 0) { throw "Vue 13B tests failed with exit code $LASTEXITCODE." }
    } finally { Pop-Location }

    $researchSource = Get-Content -Raw -Encoding UTF8 -LiteralPath "$projectRoot/web/src/components/MultiDatasetResearchPanel.vue"
    if ($researchSource -match 'contract_multiplier\s*:') {
        throw 'Multi-dataset research still supplies one browser-side contract multiplier.'
    }
    $executionSource = Get-Content -Raw -Encoding UTF8 -LiteralPath "$projectRoot/web/src/execution/config.ts"
    if ($executionSource -notmatch "EXECUTION_SEMANTIC_VERSION = '1\.0\.0'") {
        throw 'Vue execution semantic version is not pinned to 1.0.0.'
    }

    [pscustomobject]@{
        milestone = '13B'
        status = 'passed'
        execution_semantic_version = '1.0.0'
        contract_examples = 24
        authoritative_multiplier_source = 'instrument_config'
        research_multiplier_scope = 'per_dataset'
        elapsed_ms = [math]::Round(((Get-Date) - $started).TotalMilliseconds, 2)
    } | ConvertTo-Json -Compress
} finally { Pop-Location }
