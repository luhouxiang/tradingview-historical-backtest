$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $projectRoot 'trading-data'
$historyRoot = Join-Path $dataRoot 'history'
$runtimeConfigRoot = Join-Path $dataRoot 'config'
New-Item -ItemType Directory -Force $historyRoot, $runtimeConfigRoot | Out-Null

function Get-InitialInstrument {
    $appConfig = Join-Path $projectRoot 'config/app.yaml'
    if (-not (Test-Path -LiteralPath $appConfig)) { return 'AOL9' }
    $match = Select-String -LiteralPath $appConfig -Pattern '^\s*initial_instrument:\s*["'']?([A-Za-z0-9_.-]+)["'']?\s*$' | Select-Object -First 1
    if ($match -and $match.Matches[0].Groups[1].Value.Trim()) {
        return $match.Matches[0].Groups[1].Value.Trim().ToUpperInvariant()
    }
    return 'AOL9'
}

function Write-Utf8Atomic([string]$Path, [string]$Content) {
    $temporary = Join-Path (Split-Path -Parent $Path) ".$(Split-Path -Leaf $Path).$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($temporary, $Content, [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}

$initialInstrument = Get-InitialInstrument
$target = Join-Path $historyRoot "30#$initialInstrument.txt"
if (-not (Test-Path -LiteralPath $target)) {
    throw "Configured initial instrument $initialInstrument has no history file in the single data source: $target"
}
foreach ($name in @('sessions.json')) {
    $destination = Join-Path $runtimeConfigRoot $name
    if (-not (Test-Path -LiteralPath $destination)) {
        Copy-Item -LiteralPath (Join-Path $projectRoot "config/examples/$name") -Destination $destination
    }
}

$instrumentExamplePath = Join-Path $projectRoot 'config/examples/instruments.json'
$instrumentPath = Join-Path $runtimeConfigRoot 'instruments.json'
if (-not (Test-Path -LiteralPath $instrumentPath)) {
    Copy-Item -LiteralPath $instrumentExamplePath -Destination $instrumentPath
} else {
    $runtimeInstruments = Get-Content -Raw -Encoding UTF8 -LiteralPath $instrumentPath | ConvertFrom-Json
    $supportsInitialInstrument = @($runtimeInstruments.instruments | Where-Object {
        try { [regex]::IsMatch($initialInstrument, $_.symbol_pattern) } catch { $false }
    }).Count -gt 0
    if (-not $supportsInitialInstrument) {
        $exampleInstruments = Get-Content -Raw -Encoding UTF8 -LiteralPath $instrumentExamplePath | ConvertFrom-Json
        $initialMapping = @($exampleInstruments.instruments | Where-Object { [regex]::IsMatch($initialInstrument, $_.symbol_pattern) })[0]
        if (-not $initialMapping) { throw "The example instrument config does not map $initialInstrument." }
        $runtimeInstruments.instruments = @($runtimeInstruments.instruments) + $initialMapping
        Write-Utf8Atomic -Path $instrumentPath -Content ($runtimeInstruments | ConvertTo-Json -Depth 10)
    }
}

$calendarPath = Join-Path $runtimeConfigRoot 'trading_calendar.csv'
$statePath = Join-Path $runtimeConfigRoot 'demo-data-state.json'
$sourceHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
$calendarCacheHit = $false
if ((Test-Path -LiteralPath $calendarPath) -and (Test-Path -LiteralPath $statePath)) {
    try {
        $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath | ConvertFrom-Json
        $calendarHash = (Get-FileHash -LiteralPath $calendarPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $calendarCacheHit = $state.schema_version -eq 1 -and
            $state.initial_instrument -eq $initialInstrument -and
            $state.source_sha256 -eq "sha256:$sourceHash" -and
            $state.calendar_sha256 -eq "sha256:$calendarHash"
    } catch { $calendarCacheHit = $false }
}

if (-not $calendarCacheHit) {
    $sourceText = [Text.Encoding]::GetEncoding(54936).GetString([IO.File]::ReadAllBytes($target))
    $tradingDays = @($sourceText -split "`r?`n" |
        ForEach-Object { if ($_ -match '^(\d{4}/\d{2}/\d{2}),') { $matches[1] } } |
        Select-Object -Unique)
    if ($tradingDays.Count -lt 2) { throw "Cannot derive trading days from trading-data/history/30#$initialInstrument.txt." }
    $calendarByDay = @{}
    if (Test-Path -LiteralPath $calendarPath) {
        foreach ($entry in @(Import-Csv -LiteralPath $calendarPath)) { $calendarByDay[$entry.trading_day] = $entry }
    }
    for ($index = 0; $index -lt $tradingDays.Count; $index++) {
        $day = [datetime]::ParseExact($tradingDays[$index], 'yyyy/MM/dd', $null)
        $dayText = $day.ToString('yyyy-MM-dd')
        if ($calendarByDay.ContainsKey($dayText)) { continue }
        $night = if ($index -eq 0) {
            $day.AddDays(-1)
        } else {
            [datetime]::ParseExact($tradingDays[$index - 1], 'yyyy/MM/dd', $null)
        }
        $calendarByDay[$dayText] = [pscustomobject]@{
            trading_day = $dayText
            night_session_date = $night.ToString('yyyy-MM-dd')
            is_open = 'true'
            note = 'demo'
        }
    }
    $calendarCsv = @($calendarByDay.Values | Sort-Object trading_day | ConvertTo-Csv -NoTypeInformation) -join [Environment]::NewLine
    $calendarContent = $calendarCsv + [Environment]::NewLine
    $existingCalendar = if (Test-Path -LiteralPath $calendarPath) { Get-Content -Raw -Encoding UTF8 -LiteralPath $calendarPath } else { $null }
    if ($existingCalendar -cne $calendarContent) {
        Write-Utf8Atomic -Path $calendarPath -Content $calendarContent
    }
    $calendarHash = (Get-FileHash -LiteralPath $calendarPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $state = [ordered]@{
        schema_version = 1
        initial_instrument = $initialInstrument
        source_path = "history/30#$initialInstrument.txt"
        source_sha256 = "sha256:$sourceHash"
        calendar_sha256 = "sha256:$calendarHash"
        updated_at = [datetime]::UtcNow.ToString('o')
    }
    Write-Utf8Atomic -Path $statePath -Content (($state | ConvertTo-Json) + [Environment]::NewLine)
}

[pscustomobject]@{
    data_root = $dataRoot
    initial_instrument = $initialInstrument
    dataset_id = "SHFE.$initialInstrument.5m"
    source = $target
    calendar = $calendarPath
    calendar_cache_hit = $calendarCacheHit
} | ConvertTo-Json -Compress
