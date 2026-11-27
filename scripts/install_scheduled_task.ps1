<#
.SYNOPSIS
    Registers the permanent Windows Scheduled Task 'CUCM-AI-HealthMonitor'
    matching the Access Compliance Portal / PDFStudio-TestEnvironment settings.
    Run this script in an elevated (Administrator) PowerShell window.
#>
$ErrorActionPreference = "Stop"

$TaskName = "CUCM-AI-HealthMonitor"
$Root = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $Root "scripts\run.ps1"

$Action = New-ScheduledTaskAction `
    -Execute "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RunScript`"" `
    -WorkingDirectory $Root

$Trigger = New-ScheduledTaskTrigger -AtStartup

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

$Principal = New-ScheduledTaskPrincipal `
    -UserId "admin" `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Permanent background service for CUCM AI Health Monitor (http://10.106.44.24:8020)" `
    -Force

Write-Host "Scheduled Task '$TaskName' registered successfully." -ForegroundColor Green
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State, @{N='User';E={$_.Principal.UserId}}, @{N='TimeLimit';E={$_.Settings.ExecutionTimeLimit}}, @{N='RestartCount';E={$_.Settings.RestartCount}}
