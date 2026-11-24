param([switch]$Configure, [switch]$Check)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$pythonw = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$application = Join-Path $PSScriptRoot 'captions.py'
$stateDirectory = Join-Path $PSScriptRoot '.runtime'
$credentialFile = Join-Path $stateDirectory 'deepseek-key.dpapi'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'The subtitle Python environment is missing. See README.md.'
}

if ($Configure -or (-not $env:DEEPSEEK_API_KEY -and -not (Test-Path -LiteralPath $credentialFile -PathType Leaf))) {
    $secret = Read-Host 'DeepSeek API key' -AsSecureString
    try {
        if ($secret.Length -eq 0) { throw 'No key was entered.' }
        New-Item -Path $stateDirectory -ItemType Directory -Force | Out-Null
        [IO.File]::WriteAllText($credentialFile, (ConvertFrom-SecureString -SecureString $secret))
    } finally {
        $secret.Dispose()
    }
    if ($Configure -and -not $Check) { return }
}

$originalKey = $env:DEEPSEEK_API_KEY
try {
    if (-not $originalKey) {
        $secret = ConvertTo-SecureString ([IO.File]::ReadAllText($credentialFile).Trim())
        try {
            $env:DEEPSEEK_API_KEY = [Net.NetworkCredential]::new('', $secret).Password
        } finally {
            $secret.Dispose()
        }
    }
    if ($Check) {
        & $python -X utf8 -B $application --check-deepseek
        exit $LASTEXITCODE
    }
    Start-Process -FilePath $pythonw -ArgumentList @('-B', ('"{0}"' -f $application)) -WorkingDirectory $PSScriptRoot
} finally {
    $env:DEEPSEEK_API_KEY = $originalKey
}
