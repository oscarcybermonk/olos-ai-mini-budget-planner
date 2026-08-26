param(
    [switch]$Lan,
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host 'Preparing the local Python environment (first run only)...'
    python -m venv (Join-Path $projectRoot '.venv')
    & $venvPython -m pip install --disable-pip-version-check -r (Join-Path $projectRoot 'requirements.txt')
}

$hostAddress = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
Write-Host ''
Write-Host 'Olos-AI Mini Budget Planner'
Write-Host "Desktop: http://localhost:$Port"
if ($Lan) {
    $localAddress = Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '169.254.*' } |
        Select-Object -First 1 -ExpandProperty IPAddress
    if ($localAddress) { Write-Host "Phone (trusted network only): http://${localAddress}:$Port" }
    Write-Warning 'LAN mode is enabled. Stop the server with Ctrl+C when phone access is no longer needed.'
} else {
    Write-Host 'Localhost-only mode. Use .\run.ps1 -Lan for explicit trusted-network access.'
}
Write-Host ''
& $venvPython -m uvicorn backend.main:app --host $hostAddress --port $Port
