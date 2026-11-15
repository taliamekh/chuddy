$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $bundledPython = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) {
        & $bundledPython -m venv .venv
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        py -3.12 -m venv .venv
    } else {
        throw 'Install Python 3.12, then run setup.ps1 again.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& $taskPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Output 'Desktop dependencies ready. Run .\start.ps1 to open Chuddy.'
Write-Output 'For free local AI, run .\.venv\Scripts\python.exe scripts\setup_local_ai.py once (large download).'
