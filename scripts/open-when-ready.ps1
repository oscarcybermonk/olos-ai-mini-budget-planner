param(
    [Parameter(Mandatory = $true)]
    [string]$Url
)

$ErrorActionPreference = 'SilentlyContinue'
$healthUrl = "http://127.0.0.1:$(([uri]$Url).Port)/api/health"

for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $response = $null
    try { $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 1 } catch {}
    if ($response.StatusCode -eq 200) { Start-Process -FilePath $Url; exit 0 }
    Start-Sleep -Milliseconds 250
}

Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show(
    "Olos Personal Budget Tracker did not start. Try the desktop icon again, or run run.ps1 from the application folder to see startup details.",
    'Olos Personal Budget Tracker'
) | Out-Null
exit 1
