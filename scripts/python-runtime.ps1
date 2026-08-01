$PythonExecutable = if ($env:TVBT_PYTHON) {
    [IO.Path]::GetFullPath($env:TVBT_PYTHON)
} else {
    'D:\ProgramData\anaconda3\envs\pydev3.14\python.exe'
}

if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Configured Python executable does not exist: $PythonExecutable"
}
