param(
    [ValidateRange(1, 30)]
    [int]$MinLength = 1,
    [ValidateRange(1, 30)]
    [int]$MaxLength = 30,
    [ValidateRange(1, 256)]
    [int]$Workers = [Math]::Max(1, [Environment]::ProcessorCount - 1),
    [string]$LogPath = '',
    [string]$StatePath = '',
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$ebooksDirectory = Join-Path $repoRoot 'ebooks'
$archives = @(Get-ChildItem -LiteralPath $ebooksDirectory -Filter '*.zip' -File)
if ($archives.Count -ne 1) {
    throw "Expected exactly one ZIP in $ebooksDirectory, found $($archives.Count)."
}
$archive = $archives[0].FullName
$python = 'D:\ProgramData\anaconda3\envs\pydev3.14\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Required Python 3.14 runtime not found: $python"
}

$runStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $LogPath = Join-Path $ebooksDirectory "password-recovery-$runStamp.log"
}
if ([string]::IsNullOrWhiteSpace($StatePath)) {
    $StatePath = Join-Path $ebooksDirectory "password-recovery-$runStamp.json"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $ebooksDirectory 'recovered'
}
$LogPath = [System.IO.Path]::GetFullPath($LogPath)
$StatePath = [System.IO.Path]::GetFullPath($StatePath)
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)

function Write-RecoveryLog {
    param([string]$Message)
    $line = '[{0}][INFO][scripts/run-ebook-zip-recovery.ps1][000] {1}' -f `
        (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $Message
    Write-Host $line
    Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
}

function Save-RecoveryState {
    param(
        [int]$CompletedLength,
        [string]$Status,
        [string]$Password = ''
    )
    $state = [ordered]@{
        archive = $archive
        archive_sha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
        charset = $charset
        charset_size = $charset.Length
        min_length = $MinLength
        max_length = $MaxLength
        completed_length = $CompletedLength
        status = $Status
        password = $Password
        updated_at = (Get-Date).ToUniversalTime().ToString('o')
        output_directory = $OutputDirectory
        log_path = $LogPath
    }
    $temporary = "$StatePath.tmp"
    $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $StatePath -Force
}

$lettersAndDigits = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
$specials = -join (33..126 | ForEach-Object { [char]$_ } | Where-Object {
        $lettersAndDigits.IndexOf([string]$_, [System.StringComparison]::Ordinal) -lt 0
    })
$charset = $lettersAndDigits + $specials

$taskTemp = Join-Path ([System.IO.Path]::GetTempPath()) 'tvbt-zip-password-recovery'
New-Item -ItemType Directory -Path $taskTemp -Force | Out-Null
$nativeExecutable = Join-Path $taskTemp 'find-zipcrypto-native.exe'
& go build -o $nativeExecutable (Join-Path $PSScriptRoot 'find-zipcrypto-numeric.go')
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to build native ZipCrypto candidate filter.'
}

Write-RecoveryLog "started archive=$archive charset_size=$($charset.Length) lengths=$MinLength..$MaxLength workers=$Workers"
Save-RecoveryState -CompletedLength ($MinLength - 1) -Status 'running'

for ($length = $MinLength; $length -le $MaxLength; $length++) {
    $space = [System.Numerics.BigInteger]::Pow(
        [System.Numerics.BigInteger]$charset.Length,
        $length
    )
    Write-RecoveryLog "length=$length started combinations=$space"
    $started = Get-Date
    $candidateFile = Join-Path $taskTemp "candidates-$length.txt"
    & $nativeExecutable `
        -zip $archive `
        -printable `
        -min-length $length `
        -max-length $length `
        -workers $Workers | Out-File -LiteralPath $candidateFile -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        throw "Native candidate filter failed at length $length."
    }

    $candidateCount = @(Get-Content -LiteralPath $candidateFile -Encoding utf8 | Where-Object {
        $_ -ne ''
    }).Count
    if ($candidateCount -gt 0) {
        $resultFile = Join-Path $taskTemp "result-$length.json"
        & $python `
            (Join-Path $PSScriptRoot 'find-ebook-zip-password.py') `
            --directory $ebooksDirectory `
            --wordlist $candidateFile `
            --max-attempts ([Math]::Max(100, $candidateCount * 2)) `
            --progress-every 0 `
            --json-output $resultFile `
            --extract-to $OutputDirectory
        $result = @(Get-Content -LiteralPath $resultFile -Raw -Encoding utf8 | ConvertFrom-Json)[0]
        if ($result.status -eq 'found') {
            Save-RecoveryState -CompletedLength $length -Status 'found' -Password $result.password
            Write-RecoveryLog "password found length=$length password=$($result.password) extracted_to=$OutputDirectory"
            exit 0
        }
    }

    $elapsed = ((Get-Date) - $started).TotalSeconds
    Save-RecoveryState -CompletedLength $length -Status 'running'
    Write-RecoveryLog "length=$length completed without match candidates=$candidateCount elapsed_seconds=$([Math]::Round($elapsed, 3))"
}

Save-RecoveryState -CompletedLength $MaxLength -Status 'not_found'
Write-RecoveryLog "completed lengths=$MinLength..$MaxLength without a matching password"
exit 1
