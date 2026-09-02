$ErrorActionPreference = 'SilentlyContinue'
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$runScript = Join-Path $projectRoot 'run.ps1'
$appUrl = 'http://localhost:8876'
$healthUrl = 'http://127.0.0.1:8876/api/health'
$expectedApplication = 'olos-ai-mini-budget-planner-hackathon'

try {
    $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
    $health = $response.Content | ConvertFrom-Json
    if ($response.StatusCode -eq 200 -and $health.application -eq $expectedApplication) {
        Start-Process -FilePath $appUrl
        exit 0
    }
} catch {
    # The server is not running yet; start it below.
}

Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$runScript`"",'-OpenBrowser') `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden
