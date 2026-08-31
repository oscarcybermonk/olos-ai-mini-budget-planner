Option Explicit

Dim shell, fileSystem, scriptDirectory, powershellScript, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
powershellScript = fileSystem.BuildPath(scriptDirectory, "launch-hidden.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & powershellScript & """"

shell.Run command, 0, False
