param([Parameter(Mandatory)][string]$Archive)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
. "$PSScriptRoot/python-runtime.ps1"
$smokeRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'bin/release-smoke'))
$projectPrefix = $projectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $smokeRoot.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe smoke path: $smokeRoot" }
if (Test-Path -LiteralPath $smokeRoot) { Remove-Item -LiteralPath $smokeRoot -Recurse -Force }
New-Item -ItemType Directory -Force $smokeRoot | Out-Null
Expand-Archive -LiteralPath $Archive -DestinationPath $smokeRoot
$packageRoot = Get-ChildItem -LiteralPath $smokeRoot -Directory | Select-Object -First 1
if (-not $packageRoot) { throw 'Release archive has no package root.' }
$manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $packageRoot.FullName 'release-manifest.json') | ConvertFrom-Json
if ($manifest.data_included -ne $false) { throw 'Release package must not contain market data.' }
foreach ($file in $manifest.files) {
    $path = Join-Path $packageRoot.FullName $file.path
    if (-not (Test-Path -LiteralPath $path)) { throw "Release file is missing: $($file.path)" }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $file.sha256) { throw "Release checksum mismatch: $($file.path)" }
}
if (Get-ChildItem -LiteralPath $packageRoot.FullName -Recurse -File | Where-Object { $_.Name -like '*#*.txt' -or $_.Extension -eq '.parquet' }) {
    throw 'Release archive unexpectedly contains history or Parquet data.'
}
$helpOutput = Join-Path $smokeRoot 'chartd-help.stdout.log'
$helpError = Join-Path $smokeRoot 'chartd-help.stderr.log'
$chartd = Start-Process (Join-Path $packageRoot.FullName 'bin/chartd.exe') -ArgumentList '-help' `
    -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput $helpOutput -RedirectStandardError $helpError
if ($chartd.ExitCode -ne 0) { throw 'Packaged chartd binary did not start.' }

function Stop-ExactProcessTree([int]$TargetProcessId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$TargetProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) { Stop-ExactProcessTree -TargetProcessId $child.ProcessId }
    Stop-Process -Id $TargetProcessId -Force -ErrorAction SilentlyContinue
    Wait-Process -Id $TargetProcessId -Timeout 5 -ErrorAction SilentlyContinue
}

$goPort = 19080
$pythonPort = 19091
$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @($goPort, $pythonPort) }
if ($listeners) { throw "Release smoke port is already in use: $($listeners.LocalPort -join ', ')." }
$smokeData = Join-Path $smokeRoot 'data'
New-Item -ItemType Directory -Force $smokeData | Out-Null
$smokeDataYaml = $smokeData.Replace('\', '/')
$config = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $packageRoot.FullName 'config/app.yaml')
$config = $config.Replace('127.0.0.1:8080', "127.0.0.1:$goPort")
$config = $config.Replace('http://127.0.0.1:8091', "http://127.0.0.1:$pythonPort")
$config = $config.Replace('data_root: ./trading-data', "data_root: `"$smokeDataYaml`"")
$configPath = Join-Path $smokeRoot 'app.yaml'
[IO.File]::WriteAllText($configPath, $config, [Text.UTF8Encoding]::new($false))
$processes = [Collections.Generic.List[Diagnostics.Process]]::new()
try {
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = Join-Path $packageRoot.FullName 'python/src'
    try {
        $processes.Add((Start-Process $PythonExecutable -ArgumentList @('-m', 'tvbt', '--config', $configPath) `
            -WorkingDirectory $packageRoot.FullName -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $smokeRoot 'python.stdout.log') `
            -RedirectStandardError (Join-Path $smokeRoot 'python.stderr.log')))
    } finally { $env:PYTHONPATH = $previousPythonPath }
    $processes.Add((Start-Process (Join-Path $packageRoot.FullName 'bin/chartd.exe') -ArgumentList @(
        '-config', $configPath, '-web-root', (Join-Path $packageRoot.FullName 'web/dist')
    ) -WorkingDirectory $packageRoot.FullName -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $smokeRoot 'go.stdout.log') `
        -RedirectStandardError (Join-Path $smokeRoot 'go.stderr.log')))
    $deadline = (Get-Date).AddSeconds(30)
    do {
        try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$pythonPort/internal/v1/health" -TimeoutSec 2 }
        catch { $health = $null; Start-Sleep -Milliseconds 250 }
    } while ($health.status -ne 'ok' -and (Get-Date) -lt $deadline)
    if ($health.status -ne 'ok') { throw 'Packaged Python engine did not become healthy.' }
    do {
        try { $page = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$goPort/" -TimeoutSec 2 }
        catch { $page = $null; Start-Sleep -Milliseconds 250 }
    } while ((-not $page -or $page.StatusCode -ne 200 -or $page.Content -notmatch '<div id="app">') -and (Get-Date) -lt $deadline)
    if (-not $page -or $page.StatusCode -ne 200 -or $page.Content -notmatch '<div id="app">') {
        throw 'Packaged Go API and Web UI did not become healthy.'
    }
} finally {
    foreach ($process in $processes) { Stop-ExactProcessTree -TargetProcessId $process.Id }
}
$launcherDatasetBootstrap = $false
$e2eFixture = Join-Path $projectRoot 'bin/e2e-runtime/data/history/30#AOL9.txt'
if (Test-Path -LiteralPath $e2eFixture) {
    $releaseHistory = Join-Path $packageRoot.FullName 'trading-data/history'
    New-Item -ItemType Directory -Force $releaseHistory | Out-Null
    Copy-Item -LiteralPath $e2eFixture -Destination (Join-Path $releaseHistory '30#AOL9.txt')
    & (Join-Path $packageRoot.FullName 'scripts/start-release.ps1') -NoBrowser -HoldSeconds 1
    if ($LASTEXITCODE -ne 0) { throw 'Packaged release launcher failed its dataset bootstrap.' }
    $launcherDatasetBootstrap = $true
}
[pscustomobject]@{
    status = 'passed'
    archive = [IO.Path]::GetFullPath($Archive)
    manifest_files = @($manifest.files).Count
    data_included = $false
    runtime_health = @('python', 'go_api', 'web_ui')
    launcher_dataset_bootstrap = $launcherDatasetBootstrap
} | ConvertTo-Json -Compress
