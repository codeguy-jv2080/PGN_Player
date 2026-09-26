param([string]$InnoCompiler = '')
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskCache = Join-Path $taskRoot '.build'
$taskPayload = Join-Path $taskCache 'payload'

function Assert-AppStopped {
    $taskRunning = @(Get-Process -ErrorAction Stop | Where-Object { $_.ProcessName -eq 'PGN Player' })
    if ($taskRunning.Count) { throw 'PGN Player is running. Close it before building again. It has not been closed automatically.' }
}
function Remove-BuildPayload {
    $taskResolved = [IO.Path]::GetFullPath($taskPayload)
    if ($taskResolved -ne (Join-Path $taskRoot '.build\payload') -or -not $taskResolved.StartsWith($taskRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe build cache path.' }
    if (Test-Path -LiteralPath $taskResolved) { Remove-Item -LiteralPath $taskResolved -Recurse -Force }
}

Assert-AppStopped
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements.lock first. See docs/BUILD.md.' }
if (-not $InnoCompiler) {
    $taskCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    $InnoCompiler = $taskCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler)) { throw 'Inno Setup compiler not found. Pass -InnoCompiler with its ISCC.exe path.' }
New-Item -ItemType Directory -Path $taskCache -Force | Out-Null
Push-Location $taskRoot
$taskPreviousPath = $env:PATH
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & $taskPython -m pytest
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; application was not rebuilt.' }
    & $taskPython scripts\prepare_assets.py
    if ($LASTEXITCODE -ne 0) { throw 'Asset preparation failed.' }
    # A desktop host may prepend unrelated tools (Poppler, Git, etc.) to PATH.
    # Their same-named DLLs must never shadow Windows or Qt dependencies.
    $taskPythonBase = (& $taskPython -c 'import sys; print(sys.base_prefix)').Trim()
    $env:PATH = @((Split-Path -Parent $taskPython), $taskPythonBase, (Join-Path $taskPythonBase 'DLLs'), (Join-Path $env:WINDIR 'System32'), $env:WINDIR) -join ';'
    Remove-BuildPayload
    & $taskPython scripts\freeze.py --clean --noconfirm --distpath $taskPayload --workpath (Join-Path $taskCache 'pyinstaller') packaging\pgn_player.spec
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
    # Verify the installed configuration in the temporary compiler payload.
    $taskBundle = Join-Path $taskPayload 'PGN Player'
    Copy-Item -LiteralPath packaging\installed.json -Destination (Join-Path $taskBundle 'edition.json')
    $taskSmokeReport = Join-Path $taskCache 'installed-smoke.json'
    if (Test-Path -LiteralPath $taskSmokeReport) { Remove-Item -LiteralPath $taskSmokeReport }
    $taskSmoke = Start-Process -FilePath (Join-Path $taskBundle 'PGN Player.exe') -ArgumentList @('--smoke-test', '--smoke-output', ('"' + $taskSmokeReport + '"')) -WindowStyle Hidden -PassThru -Wait
    if ($taskSmoke.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $taskSmokeReport)) { throw 'Installed payload smoke test failed.' }
    if (-not (Get-Content -LiteralPath $taskSmokeReport -Raw | ConvertFrom-Json).ok) { throw 'Installed payload reported a failed smoke test.' }
    Remove-Item -LiteralPath (Join-Path $taskBundle 'edition.json')
    Assert-AppStopped
    & $taskPython scripts\package.py
    if ($LASTEXITCODE -ne 0) { throw 'Portable packaging failed. Close the app if its files are locked; no alternate output folder was created.' }
    & $InnoCompiler ('/DSourceRoot=' + $taskRoot) packaging\installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
    $taskPortable = Join-Path $taskRoot 'dist\portable\PGN Player\PGN Player.exe'
    $taskPortableReport = Join-Path $taskCache 'portable-smoke.json'
    if (Test-Path -LiteralPath $taskPortableReport) { Remove-Item -LiteralPath $taskPortableReport }
    $taskSmoke = Start-Process -FilePath $taskPortable -ArgumentList @('--smoke-test', '--smoke-output', ('"' + $taskPortableReport + '"')) -WindowStyle Hidden -PassThru -Wait
    if ($taskSmoke.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $taskPortableReport)) { throw 'Portable smoke test failed.' }
    if (-not (Get-Content -LiteralPath $taskPortableReport -Raw | ConvertFrom-Json).ok) { throw 'Portable application reported a failed smoke test.' }
    Remove-BuildPayload
    Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $taskRoot 'dist\PGN Player Portable.zip'),(Join-Path $taskRoot 'dist\PGN Player Setup.exe'),(Join-Path $taskRoot 'dist\PGN Player Source.zip') | ForEach-Object { $_.Hash.ToLowerInvariant() + '  ' + (Split-Path -Leaf $_.Path) } | Set-Content -LiteralPath (Join-Path $taskRoot 'dist\SHA256SUMS.txt') -Encoding ascii
    Write-Output 'Portable ZIP, Windows installer, source ZIP, and checksums are ready in dist.'
} finally { $env:PATH = $taskPreviousPath; Pop-Location }
