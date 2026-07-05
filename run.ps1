# Launch the Windows Task Dashboard. Uses the project venv python if present,
# otherwise the system python.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = (Get-Command python).Source }
& $Py (Join-Path $Root "app.py") @args
