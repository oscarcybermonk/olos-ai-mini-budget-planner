param(
    [Parameter(Mandatory = $true)]
    [string]$Url
)

$ErrorActionPreference = 'SilentlyContinue'
$healthUrl = "http://127.0.0.1:$(([uri]$Url).Port)/api/health"
$expectedApplication = 'olos-ai-mini-budget-planner-hackathon'

for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $response = $null
    try { $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 1 } catch {}
    $health = if ($response) { $response.Content | ConvertFrom-Json } else { $null }
    if ($response.StatusCode -eq 200 -and $health.application -eq $expectedApplication) { Start-Process -FilePath $Url; exit 0 }
    Start-Sleep -Milliseconds 250
}

Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show(
    "Olos-AI Mini Budget Planner did not start. Its port may be in use by another application. Run run.ps1 from this project folder to see startup details.",
    'Olos-AI Mini Budget Planner'
) | Out-Null
exit 1
