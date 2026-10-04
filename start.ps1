$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (!(Test-Path '.venv/Scripts/python.exe')) {
    python -m venv .venv
    & .venv/Scripts/python.exe -m pip install -r requirements.txt
    & .venv/Scripts/python.exe -m pip install -r indexer/requirements.txt
}
# The indexer is independent and may instead live at FONTHUB_INDEXER_URL.
if (!$env:FONTHUB_INDEXER_URL) {
    try { $null = Invoke-RestMethod 'http://127.0.0.1:8766/api/v1/health' -TimeoutSec 2 }
    catch {
        Start-Process -FilePath (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') -ArgumentList '-m','indexer.service' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'indexer.log') -RedirectStandardError (Join-Path $PSScriptRoot 'indexer-error.log')
    }
}
& .venv/Scripts/python.exe app.py
