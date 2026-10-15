# Shared Python core owns parameters, defaults and exit codes on every platform.
$ErrorActionPreference = 'Stop'
$taskPython = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
try {
    & $taskPython (Join-Path $PSScriptRoot 'acoustic_e2e.py') @args
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine('Cannot launch Python. Install Python or set PYTHON to its executable.')
    exit 127
}
