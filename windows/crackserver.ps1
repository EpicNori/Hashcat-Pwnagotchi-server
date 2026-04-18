Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$defaultInstallRoot = Join-Path $env:USERPROFILE "Hashcat-Pwnagotchi-server"
$script:InstallRoot = if ($env:HASHCAT_WPA_SERVER_DIR) { $env:HASHCAT_WPA_SERVER_DIR } else { $defaultInstallRoot }
$script:RunDir = Join-Path $script:InstallRoot ".windows-runtime"
$script:PidFile = Join-Path $script:RunDir "server.pid"
$script:LogFile = Join-Path $script:RunDir "server.log"
$script:OutFile = Join-Path $script:RunDir "server.stdout.log"
$script:ErrFile = Join-Path $script:RunDir "server.stderr.log"
$script:VenvPython = Join-Path $script:InstallRoot ".venv\Scripts\python.exe"

function Write-Step {
    param([string]$Message)
    Write-Host "[*] $Message"
}

function Ensure-Install {
    if (-not (Test-Path $script:InstallRoot)) {
        throw "No Windows install was found at $script:InstallRoot. Run the Windows install one-liner first."
    }
    if (-not (Test-Path $script:VenvPython)) {
        throw "Python virtual environment not found at $script:VenvPython. Run the Windows install one-liner first."
    }
    New-Item -ItemType Directory -Path $script:RunDir -Force | Out-Null
}

function Get-ServerProcess {
    if (-not (Test-Path $script:PidFile)) {
        return $null
    }

    try {
        $pidValue = (Get-Content $script:PidFile -Raw).Trim()
        if (-not $pidValue) {
            return $null
        }
        $process = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
        if ($process) {
            return $process
        }
    } catch {
    }

    Remove-Item $script:PidFile -Force -ErrorAction SilentlyContinue
    return $null
}

function Start-Server {
    Ensure-Install

    $existing = Get-ServerProcess
    if ($existing) {
        Write-Host "[+] crackserver is already running (PID $($existing.Id))."
        return
    }

    if (Test-Path $script:OutFile) { Remove-Item $script:OutFile -Force -ErrorAction SilentlyContinue }
    if (Test-Path $script:ErrFile) { Remove-Item $script:ErrFile -Force -ErrorAction SilentlyContinue }

    Write-Step "Starting crackserver background web server..."
    $process = Start-Process `
        -FilePath $script:VenvPython `
        -ArgumentList "app\run.py" `
        -WorkingDirectory $script:InstallRoot `
        -RedirectStandardOutput $script:OutFile `
        -RedirectStandardError $script:ErrFile `
        -WindowStyle Hidden `
        -PassThru

    Set-Content -Path $script:PidFile -Value $process.Id
    Start-Sleep -Seconds 2

    $running = Get-ServerProcess
    if (-not $running) {
        throw "The web server failed to stay running. Check 'crackserver logs' for details."
    }

    Merge-Logs
    Write-Host "[+] crackserver started successfully."
    Write-Host "[+] Dashboard URL: http://127.0.0.1:9111"
}

function Stop-Server {
    Ensure-Install

    $process = Get-ServerProcess
    if (-not $process) {
        Write-Host "[+] crackserver is not running."
        return
    }

    Write-Step "Stopping crackserver (PID $($process.Id))..."
    Stop-Process -Id $process.Id -Force
    Remove-Item $script:PidFile -Force -ErrorAction SilentlyContinue
    Merge-Logs
    Write-Host "[+] crackserver stopped."
}

function Restart-Server {
    Stop-Server
    Start-Server
}

function Merge-Logs {
    $combined = @()
    foreach ($path in @($script:OutFile, $script:ErrFile)) {
        if (Test-Path $path) {
            $combined += Get-Content $path
        }
    }
    if ($combined.Count -gt 0) {
        Set-Content -Path $script:LogFile -Value $combined
    }
}

function Show-Status {
    Ensure-Install

    $process = Get-ServerProcess
    if ($process) {
        Write-Host "[+] crackserver is running (PID $($process.Id))."
        Write-Host "[+] Dashboard URL: http://127.0.0.1:9111"
    } else {
        Write-Host "[!] crackserver is not running."
    }
}

function Show-Logs {
    Ensure-Install
    Merge-Logs
    if (-not (Test-Path $script:LogFile)) {
        Write-Host "[!] No logs are available yet."
        return
    }
    Get-Content $script:LogFile -Tail 80
}

function Show-Dashboard {
    $hostname = $env:COMPUTERNAME
    Write-Host "====================================================="
    Write-Host "  Hashcat WPA Server Dashboard"
    Write-Host "====================================================="
    Write-Host "  Local:   http://127.0.0.1:9111"
    if ($hostname) {
        Write-Host "  Host:    http://$hostname`:9111"
    }
    Write-Host "====================================================="
}

function Update-Server {
    Ensure-Install
    $wasRunning = [bool](Get-ServerProcess)
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $script:InstallRoot "windows\update.ps1")
    if ($wasRunning) {
        Restart-Server
    }
}

function Show-Help {
    Write-Host "Usage: crackserver [COMMAND]"
    Write-Host ""
    Write-Host "Commands:"
    Write-Host "  start       - Start the background web server"
    Write-Host "  stop        - Stop the background web server"
    Write-Host "  restart     - Restart the background web server"
    Write-Host "  status      - Show whether the server is running"
    Write-Host "  logs        - Show the recent web server logs"
    Write-Host "  dashboard   - Print the dashboard URLs"
    Write-Host "  update      - Update the Windows install and dependencies"
    Write-Host ""
}

switch ($args[0]) {
    "start" { Start-Server }
    "stop" { Stop-Server }
    "restart" { Restart-Server }
    "status" { Show-Status }
    "logs" { Show-Logs }
    "dashboard" { Show-Dashboard }
    "update" { Update-Server }
    default { Show-Help }
}
