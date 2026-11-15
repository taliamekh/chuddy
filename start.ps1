param([switch]$Browser, [switch]$Headless)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run .\setup.ps1 first.' }
$runArguments = @('run.py')
if ($Browser) { $runArguments += '--browser' }
if ($Headless) { $runArguments += '--headless' }
& $taskPython @runArguments
