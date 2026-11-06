# Registers millennium:// links for the current Windows user (no admin needed), so the dashboard
# buttons can open drafts and mark them sent. Undo: Remove-Item HKCU:\Software\Classes\millennium -Recurse
$pythonw = "$env:LOCALAPPDATA\Programs\Python\Python313\pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = Join-Path (Split-Path (Get-Command python).Source) "pythonw.exe" }
$action = Join-Path $PSScriptRoot "action.py"
$key = "HKCU:\Software\Classes\millennium"
New-Item -Path "$key\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path $key -Name "(Default)" -Value "URL:Millennium Dashboard"
Set-ItemProperty -Path $key -Name "URL Protocol" -Value ""
Set-ItemProperty -Path "$key\shell\open\command" -Name "(Default)" -Value "`"$pythonw`" `"$action`" `"%1`""
Write-Host "millennium:// registered -> $pythonw $action"
