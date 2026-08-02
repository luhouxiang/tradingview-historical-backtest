$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $projectRoot 'trading-data'
$historyRoot = Join-Path $dataRoot 'history'
$runtimeConfigRoot = Join-Path $dataRoot 'config'
New-Item -ItemType Directory -Force $historyRoot, $runtimeConfigRoot | Out-Null

function Write-Utf8Atomic([string]$Path, [string]$Content) {
    $temporary = Join-Path (Split-Path -Parent $Path) ".$(Split-Path -Leaf $Path).$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($temporary, $Content, [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}

$source = Join-Path $projectRoot 'samples/30#AOL9.txt'
$target = Join-Path $historyRoot '30#AOL9.txt'
if (-not (Test-Path -LiteralPath $target)) {
    Copy-Item -LiteralPath $source -Destination $target
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
    $supportsAol9 = @($runtimeInstruments.instruments | Where-Object {
        try { [regex]::IsMatch('AOL9', $_.symbol_pattern) } catch { $false }
    }).Count -gt 0
    if (-not $supportsAol9) {
        $exampleInstruments = Get-Content -Raw -Encoding UTF8 -LiteralPath $instrumentExamplePath | ConvertFrom-Json
        $aol9Mapping = @($exampleInstruments.instruments | Where-Object { [regex]::IsMatch('AOL9', $_.symbol_pattern) })[0]
        if (-not $aol9Mapping) { throw 'The example instrument config does not map AOL9.' }
        $runtimeInstruments.instruments = @($runtimeInstruments.instruments) + $aol9Mapping
        Write-Utf8Atomic -Path $instrumentPath -Content ($runtimeInstruments | ConvertTo-Json -Depth 10)
    }
}

$calendarPath = Join-Path $runtimeConfigRoot 'trading_calendar.csv'
$sourceText = [Text.Encoding]::GetEncoding(54936).GetString([IO.File]::ReadAllBytes($target))
$tradingDays = @($sourceText -split "`r?`n" |
    ForEach-Object { if ($_ -match '^(\d{4}/\d{2}/\d{2}),') { $matches[1] } } |
    Select-Object -Unique)
if ($tradingDays.Count -lt 2) { throw 'Cannot derive trading days from samples/30#AOL9.txt.' }
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
Write-Utf8Atomic -Path $calendarPath -Content ($calendarCsv + [Environment]::NewLine)

[pscustomobject]@{
    data_root = $dataRoot
    source = $target
    calendar = $calendarPath
} | ConvertTo-Json -Compress
