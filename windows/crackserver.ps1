param(
    [string]$Command = "status",
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CurrentRoot = Join-Path $InstallRoot "current"
$PidFile = Join-Path $InstallRoot "server.pid"
$LogsRoot = Join-Path $InstallRoot "logs"
$RunScript = Join-Path $CurrentRoot "windows\run_server.ps1"
$AutostartScript = Join-Path $CurrentRoot "windows\autostart_service.ps1"
$UpdateScript = Join-Path $CurrentRoot "update.ps1"
$UninstallScript = Join-Path $CurrentRoot "windows\uninstall_app.ps1"

function Get-ServerProcess {
    if (-not (Test-Path $PidFile)) {
        return $null
    }
    $pidValue = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($pidValue)) {
        return $null
    }
    $process = Get-Process -Id $pidValue.Trim() -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
    return $process
}

switch ($Command.ToLowerInvariant()) {
    "start" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $RunScript -InstallRoot $InstallRoot
    }
    "stop" {
        $process = Get-ServerProcess
        if ($process) {
            Stop-Process -Id $process.Id -Force
        }
        Get-Process -Name hashcat -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        Write-Output "stopped"
    }
    "restart" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath stop -InstallRoot $InstallRoot
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath start -InstallRoot $InstallRoot
    }
    "status" {
        $process = Get-ServerProcess
        if ($process) {
            Write-Output "running (PID $($process.Id))"
        } else {
            Write-Output "stopped"
        }
    }
    "dashboard" {
        $ipAddresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike "169.254.*" -and $_.IPAddress -ne "127.0.0.1" } |
            Select-Object -ExpandProperty IPAddress -Unique
        $networkIp = if ($ipAddresses) { $ipAddresses[0] } else { "YOUR_SERVER_IP" }
        Write-Output "Local:   http://127.0.0.1:9111"
        Write-Output "Network: http://$networkIp`:9111"
    }
    "update" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $UpdateScript
    }
    "logs" {
        $stdoutLog = Join-Path $LogsRoot "server_stdout.log"
        $stderrLog = Join-Path $LogsRoot "server_stderr.log"
        if (Test-Path $stdoutLog) {
            Get-Content -LiteralPath $stdoutLog -Wait
        }
        if (Test-Path $stderrLog) {
            Get-Content -LiteralPath $stderrLog -Wait
        }
    }
    "enable-autostart" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $AutostartScript enable -InstallRoot $InstallRoot
    }
    "disable-autostart" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $AutostartScript disable -InstallRoot $InstallRoot
    }
    "uninstall" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $UninstallScript -InstallRoot $InstallRoot
    }
    default {
        Write-Output "Usage: crackserver {start|stop|restart|status|dashboard|update|logs|enable-autostart|disable-autostart|uninstall}"
        exit 1
    }
}
