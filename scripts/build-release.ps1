param([string]$Version = '0.1.0')

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'bin/release'))
$stageRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "bin/release-stage/tvbt-$Version-windows-x64"))
$projectPrefix = $projectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
foreach ($target in @($releaseRoot, $stageRoot)) {
    if (-not $target.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe release path: $target" }
}

& "$PSScriptRoot/build-chartd.ps1" -Force | Out-Null
Push-Location (Join-Path $projectRoot 'web')
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Web release build failed with exit code $LASTEXITCODE." }
} finally { Pop-Location }

$stageParent = Split-Path -Parent $stageRoot
if (Test-Path -LiteralPath $stageParent) { Remove-Item -LiteralPath $stageParent -Recurse -Force }
if (Test-Path -LiteralPath $releaseRoot) { Remove-Item -LiteralPath $releaseRoot -Recurse -Force }
New-Item -ItemType Directory -Force $stageRoot, $releaseRoot | Out-Null
foreach ($directory in @('bin', 'config', 'python', 'scripts', 'web')) {
    New-Item -ItemType Directory -Force (Join-Path $stageRoot $directory) | Out-Null
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'bin/chartd.exe') -Destination (Join-Path $stageRoot 'bin/chartd.exe')
Copy-Item -LiteralPath (Join-Path $projectRoot 'config/app.yaml') -Destination (Join-Path $stageRoot 'config/app.yaml')
Copy-Item -LiteralPath (Join-Path $projectRoot 'config/examples') -Destination (Join-Path $stageRoot 'config') -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot 'python/src') -Destination (Join-Path $stageRoot 'python') -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot 'python/requirements.lock') -Destination (Join-Path $stageRoot 'python/requirements.lock')
Copy-Item -LiteralPath (Join-Path $projectRoot 'web/dist') -Destination (Join-Path $stageRoot 'web') -Recurse
foreach ($name in @('python-runtime.ps1', 'prepare-demo-data.ps1', 'start-release.ps1')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "scripts/$name") -Destination (Join-Path $stageRoot "scripts/$name")
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'docs') -Destination $stageRoot -Recurse

$manifestFiles = @('bin/chartd.exe', 'web/dist/index.html', 'python/requirements.lock', 'config/app.yaml', 'scripts/start-release.ps1')
$manifest = [ordered]@{
    schema_version = 1
    version = $Version
    platform = 'windows-x64'
    created_at = [datetime]::UtcNow.ToString('o')
    data_included = $false
    files = @($manifestFiles | ForEach-Object {
        $path = Join-Path $stageRoot $_
        [ordered]@{ path = $_.Replace('\', '/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
}
[IO.File]::WriteAllText((Join-Path $stageRoot 'release-manifest.json'), (($manifest | ConvertTo-Json -Depth 5) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))

$archivePath = Join-Path $releaseRoot "tvbt-$Version-windows-x64.zip"
Compress-Archive -LiteralPath $stageRoot -DestinationPath $archivePath -CompressionLevel Optimal
[pscustomobject]@{
    status = 'passed'
    version = $Version
    archive = $archivePath
    sha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    data_included = $false
} | ConvertTo-Json -Compress
