param([switch]$Configure, [switch]$Check)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$pythonw = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$application = Join-Path $PSScriptRoot 'captions.py'
$keyDialog = Join-Path $PSScriptRoot 'deepseek_key.py'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'The subtitle Python environment is missing. See README.md.'
}

if ($Configure) {
    if ($Check) {
        & $python -X utf8 -B $keyDialog
        if ($LASTEXITCODE) { exit $LASTEXITCODE }
    } else {
        Start-Process -FilePath $pythonw -ArgumentList @('-B', ('"{0}"' -f $keyDialog)) -WorkingDirectory $PSScriptRoot -WindowStyle Normal
        return
    }
}
if ($Check) {
    & $python -X utf8 -B $application --check-deepseek
    exit $LASTEXITCODE
}
Start-Process -FilePath $pythonw -ArgumentList @('-B', ('"{0}"' -f $application)) -WorkingDirectory $PSScriptRoot
