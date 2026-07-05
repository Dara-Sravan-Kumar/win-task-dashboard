# Optional: run the dashboard in the background at logon (always-on local API).
# Hidden window via pythonw; serves http://127.0.0.1:8787 without opening a browser.

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Pyw  = Join-Path $Root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $Pyw)) { $Pyw = (Get-Command pythonw).Source }
$App  = Join-Path $Root "app.py"
$User = "$env:USERDOMAIN\$env:USERNAME"
$TaskName = "win-task-dashboard"

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>win-task-dashboard local API (autostart at logon).</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>$User</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>$User</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <Enabled>true</Enabled>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author"><Exec>
    <Command>$Pyw</Command>
    <Arguments>"$App" --no-browser</Arguments>
    <WorkingDirectory>$Root</WorkingDirectory>
  </Exec></Actions>
</Task>
"@

Register-ScheduledTask -TaskName $TaskName -Xml $xml -User $User -Force | Out-Null
Write-Host "Registered '$TaskName' (autostart at logon). Start now: Start-ScheduledTask -TaskName $TaskName"
