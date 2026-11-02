$ErrorActionPreference = "Stop"

if (-not $env:ADMIN_PASSWORD) {
  $env:ADMIN_PASSWORD = "host123"
  Write-Host "ADMIN_PASSWORD was not set; using a temporary local default." -ForegroundColor Yellow
  Write-Host "Set a private password before sharing this invitation." -ForegroundColor Yellow
}

$candidates = @(
    (Get-Command py -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    "$env:APPDATA\uv\python\cpython-3.15.1-windows-x86_64-none\python.exe",
    "$PSScriptRoot\.tools\python\python.exe",
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
) | Where-Object { $_ -and (Test-Path $_) -and $_ -notmatch '\\WindowsApps\\python\.exe$' }

if (-not $candidates) {
  throw "Python was not found. Install Python 3.9+ and try again."
}

$python = $candidates | Select-Object -First 1
Write-Host "Starting wedding invitation with $python"
Write-Host "Open http://localhost:8080"
& $python -u "$PSScriptRoot\server.py"
