param([switch]$Force)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$binaryPath = Join-Path $projectRoot 'bin/chartd.exe'
$inputPaths = [Collections.Generic.List[string]]::new()
foreach ($root in @('cmd', 'internal')) {
    foreach ($item in Get-ChildItem -LiteralPath (Join-Path $projectRoot $root) -Recurse -File -Filter '*.go') {
        $inputPaths.Add($item.FullName)
    }
}
foreach ($name in @('go.mod', 'go.sum')) { $inputPaths.Add((Join-Path $projectRoot $name)) }

$needsBuild = $Force -or -not (Test-Path -LiteralPath $binaryPath)
if (-not $needsBuild) {
    $binaryTime = (Get-Item -LiteralPath $binaryPath).LastWriteTimeUtc
    $needsBuild = @($inputPaths | Where-Object { (Get-Item -LiteralPath $_).LastWriteTimeUtc -gt $binaryTime }).Count -gt 0
}

$stopwatch = [Diagnostics.Stopwatch]::StartNew()
if ($needsBuild) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $binaryPath) | Out-Null
    Push-Location $projectRoot
    try {
        go build -o $binaryPath ./cmd/chartd
        if ($LASTEXITCODE -ne 0) { throw "chartd build failed with exit code $LASTEXITCODE." }
    } finally {
        Pop-Location
    }
}
$stopwatch.Stop()

[pscustomobject]@{
    path = $binaryPath
    rebuilt = $needsBuild
    elapsed_ms = [math]::Round($stopwatch.Elapsed.TotalMilliseconds, 2)
} | ConvertTo-Json -Compress
