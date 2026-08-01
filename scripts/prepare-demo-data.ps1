$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $projectRoot 'trading-data'
$historyRoot = Join-Path $dataRoot 'history'
$runtimeConfigRoot = Join-Path $dataRoot 'config'
New-Item -ItemType Directory -Force $historyRoot, $runtimeConfigRoot | Out-Null

$source = Join-Path $projectRoot 'samples/30#AO2609.txt'
$target = Join-Path $historyRoot '30#AO2609.txt'
if (-not (Test-Path -LiteralPath $target)) {
    Copy-Item -LiteralPath $source -Destination $target
}
foreach ($name in @('instruments.json', 'sessions.json')) {
    $destination = Join-Path $runtimeConfigRoot $name
    if (-not (Test-Path -LiteralPath $destination)) {
        Copy-Item -LiteralPath (Join-Path $projectRoot "config/examples/$name") -Destination $destination
    }
}

$calendarPath = Join-Path $runtimeConfigRoot 'trading_calendar.csv'
if (-not (Test-Path -LiteralPath $calendarPath)) {
    $sourceText = [Text.Encoding]::GetEncoding(54936).GetString([IO.File]::ReadAllBytes($target))
    $tradingDays = @($sourceText -split "`r?`n" |
        ForEach-Object { if ($_ -match '^(\d{4}/\d{2}/\d{2}),') { $matches[1] } } |
        Select-Object -Unique)
    if ($tradingDays.Count -lt 2) { throw 'Cannot derive trading days from samples/30#AO2609.txt.' }
    $calendar = [Collections.Generic.List[string]]::new()
    $calendar.Add('trading_day,night_session_date,is_open,note')
    for ($index = 0; $index -lt $tradingDays.Count; $index++) {
        $day = [datetime]::ParseExact($tradingDays[$index], 'yyyy/MM/dd', $null)
        $night = if ($index -eq 0) {
            $day.AddDays(-1)
        } else {
            [datetime]::ParseExact($tradingDays[$index - 1], 'yyyy/MM/dd', $null)
        }
        $calendar.Add("$($day.ToString('yyyy-MM-dd')),$($night.ToString('yyyy-MM-dd')),true,demo")
    }
    [IO.File]::WriteAllLines($calendarPath, $calendar, [Text.UTF8Encoding]::new($false))
}

[pscustomobject]@{
    data_root = $dataRoot
    source = $target
    calendar = $calendarPath
} | ConvertTo-Json -Compress
