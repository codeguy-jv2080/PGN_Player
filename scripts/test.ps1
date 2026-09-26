$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements.lock first. See docs/BUILD.md.' }
$env:QT_QPA_PLATFORM = 'offscreen'
Push-Location $taskRoot
try {
    & $taskPython -m pytest @args
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
} finally { Pop-Location }
