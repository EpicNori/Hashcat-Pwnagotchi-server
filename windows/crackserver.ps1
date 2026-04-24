param(
    [string]$Command = "status",
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Is-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-RunAsSelf([string[]]$Arguments) {
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $Arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Elevated crackserver command failed with exit code $($process.ExitCode)."
    }
    return $true
}

if (-not (Is-Administrator) -and $Command.ToLowerInvariant() -in @("stop", "restart", "enable-autostart", "disable-autostart", "uninstall")) {
    $launchArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-Command", $Command,
        "-InstallRoot", $InstallRoot
    )
    if (-not (Invoke-RunAsSelf -Arguments $launchArgs)) {
        return
    }
}

$CurrentRoot = Join-Path $InstallRoot "current"
$PidFile = Join-Path $InstallRoot "server.pid"
$LogsRoot = Join-Path $InstallRoot "logs"
$RunScript = Join-Path $CurrentRoot "windows\run_server.ps1"
$AutostartScript = Join-Path $CurrentRoot "windows\autostart_service.ps1"
$UpdateScript = Join-Path $CurrentRoot "update.ps1"
$UninstallScript = Join-Path $CurrentRoot "windows\uninstall_app.ps1"
$NvidiaDriversScript = Join-Path $CurrentRoot "windows\install_nvidia_drivers.ps1"

function Invoke-CheckedPowerShellFile([string]$ScriptPath, [string[]]$Arguments = @()) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PowerShell helper failed: $ScriptPath"
    }
}

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

function Test-ServerHttpAlive {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:9111" -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Stop-ServerProcessesByRoot([string]$RootPath) {
    $escapedRoot = [Regex]::Escape($RootPath)
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath -match "^$escapedRoot") -or
        ($_.CommandLine -and $_.CommandLine -match $escapedRoot)
    } | ForEach-Object {
        try {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        } catch {
        }
    }
}

switch ($Command.ToLowerInvariant()) {
    "start" {
        Invoke-CheckedPowerShellFile -ScriptPath $RunScript -Arguments @("-InstallRoot", $InstallRoot)
    }
    "stop" {
        $process = Get-ServerProcess
        if ($process) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $deadline = (Get-Date).AddSeconds(15)
            while ((Get-Process -Id $process.Id -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {
                Start-Sleep -Milliseconds 200
            }
        }
        if (Test-ServerHttpAlive) {
            Stop-ServerProcessesByRoot -RootPath $CurrentRoot
            Start-Sleep -Seconds 2
        }
        Get-Process -Name hashcat -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        Write-Output "stopped"
    }
    "restart" {
        Invoke-CheckedPowerShellFile -ScriptPath $PSCommandPath -Arguments @("stop", "-InstallRoot", $InstallRoot)
        Invoke-CheckedPowerShellFile -ScriptPath $PSCommandPath -Arguments @("start", "-InstallRoot", $InstallRoot)
    }
    "status" {
        $process = Get-ServerProcess
        if ($process) {
            Write-Output "running (PID $($process.Id))"
        } elseif (Test-ServerHttpAlive) {
            Write-Output "running (HTTP 9111)"
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
        $anyLog = $false
        if (Test-Path $stdoutLog) {
            $anyLog = $true
            Write-Output "=== server_stdout.log ==="
            Get-Content -LiteralPath $stdoutLog -Tail 200
            Write-Output ""
        }
        if (Test-Path $stderrLog) {
            $anyLog = $true
            Write-Output "=== server_stderr.log ==="
            Get-Content -LiteralPath $stderrLog -Tail 200
        }
        if (-not $anyLog) {
            Write-Output "No logs available."
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
    "driver-check" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $NvidiaDriversScript check
    }
    "driver-status" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $NvidiaDriversScript status
    }
    default {
        Write-Output "Usage: crackserver {start|stop|restart|status|dashboard|update|logs|enable-autostart|disable-autostart|driver-check|driver-status|uninstall}"
        throw "Unsupported command: $Command"
    }
}
