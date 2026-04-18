param(
    [ValidateSet("enable", "disable", "status")]
    [string]$Action = "status",
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName = "HashcatWPAServer"
$Runner = Join-Path $InstallRoot "current\windows\run_server.ps1"
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -InstallRoot `"$InstallRoot`""

switch ($Action) {
    "enable" {
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
        Write-Output "enabled"
    }
    "disable" {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        Write-Output "disabled"
    }
    "status" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Output "disabled"
        } elseif ($task.State -eq "Disabled") {
            Write-Output "disabled"
        } else {
            Write-Output "enabled"
        }
    }
}
