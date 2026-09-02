$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcherScript = Join-Path $projectRoot 'scripts\launch-hidden.vbs'
$iconPath = Join-Path $projectRoot 'frontend\assets\olos-mini-budget.ico'
$desktopPath = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopPath 'Olos-AI Mini Budget Planner.lnk'

if (-not (Test-Path -LiteralPath $launcherScript)) { throw "Launcher not found: $launcherScript" }
if (-not (Test-Path -LiteralPath $iconPath)) { throw "Icon not found: $iconPath" }

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:SystemRoot\System32\wscript.exe"
$shortcut.Arguments = "`"$launcherScript`""
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = 'Open the isolated Olos-AI Mini Budget Planner without a console window'
$shortcut.WindowStyle = 1
$shortcut.Save()

Write-Output "Desktop launcher created: $shortcutPath"
