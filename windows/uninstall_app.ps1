param(
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CurrentRoot = Join-Path $InstallRoot "current"
$RepoWindowsRoot = $PSScriptRoot
$TaskScript = Join-Path $CurrentRoot "windows\autostart_service.ps1"
$CliScript = Join-Path $CurrentRoot "windows\crackserver.ps1"
$FallbackTaskScript = Join-Path $RepoWindowsRoot "autostart_service.ps1"
$FallbackCliScript = Join-Path $RepoWindowsRoot "crackserver.ps1"

function Invoke-PreferredPowerShellScript([string]$Primary, [string]$Fallback, [string[]]$Arguments = @()) {
    foreach ($scriptPath in @($Primary, $Fallback)) {
        if (-not (Test-Path $scriptPath)) {
            continue
        }

        try {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath @Arguments | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return $true
            }
        } catch {
        }
    }

    return $false
}

function Test-PathUnlocked([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return $true
    }

    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $stream.Close()
        return $true
    } catch [System.IO.IOException] {
        return $false
    } catch {
        return $false
    }
}

function Wait-PathUnlocked([string]$Path, [int]$TimeoutSeconds = 20) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-PathUnlocked -Path $Path) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Invoke-WithRetry([scriptblock]$Action, [string]$Description, [int]$Attempts = 24, [int]$DelayMilliseconds = 500) {
    $lastError = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            & $Action
            return $true
        } catch [System.IO.IOException] {
            $lastError = $_
        } catch [System.UnauthorizedAccessException] {
            $lastError = $_
        } catch [System.ArgumentException] {
            $lastError = $_
        }

        if ($attempt -lt $Attempts) {
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }

    if ($lastError) {
        Write-Output "[!] Failed to $Description after retries: $($lastError.Exception.Message)"
    }
    return $false
}

function Stop-InstallProcess([string]$PidFilePath) {
    if (-not (Test-Path $PidFilePath)) {
        return
    }

    $pidValue = Get-Content -LiteralPath $PidFilePath -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not [string]::IsNullOrWhiteSpace($pidValue)) {
        Stop-Process -Id $pidValue.Trim() -Force -ErrorAction SilentlyContinue
    }

    Get-Process -Name hashcat -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PidFilePath -Force -ErrorAction SilentlyContinue
}

function Stop-InstallProcessesByRoot([string]$RootPath) {
    $escapedRoot = [Regex]::Escape($RootPath)
    $selfPid = $PID
    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessId -ne $selfPid -and (
            ($_.ExecutablePath -and $_.ExecutablePath -match "^$escapedRoot") -or
            ($_.CommandLine -and $_.CommandLine -match $escapedRoot)
        )
    }

    foreach ($process in $processes) {
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        } catch {
        }
    }
}

function Remove-InstallRootForcefully([string]$Path) {
    if (-not (Test-Path $Path)) {
        return $true
    }

    $null = Invoke-WithRetry -Description "remove the install root" -Action {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
    }
    if (-not (Test-Path $Path)) {
        return $true
    }

    try {
        & cmd.exe /c "attrib -r -s -h `"$Path`" /s /d >nul 2>&1"
    } catch {
    }

    try {
        & cmd.exe /c "rmdir /s /q `"$Path`""
    } catch {
    }

    return -not (Test-Path $Path)
}

Invoke-PreferredPowerShellScript -Primary $TaskScript -Fallback $FallbackTaskScript -Arguments @("disable", "-InstallRoot", $InstallRoot) | Out-Null
if (-not (Invoke-PreferredPowerShellScript -Primary $CliScript -Fallback $FallbackCliScript -Arguments @("stop", "-InstallRoot", $InstallRoot))) {
    Stop-InstallProcess -PidFilePath (Join-Path $InstallRoot "server.pid")
}
Stop-InstallProcessesByRoot -RootPath $InstallRoot

Start-Sleep -Seconds 2

if (Test-Path $InstallRoot) {
    $logsRoot = Join-Path $InstallRoot "logs"
    if (Test-Path $logsRoot) {
        Wait-PathUnlocked -Path $logsRoot -TimeoutSeconds 20 | Out-Null
    }
    if (-not (Remove-InstallRootForcefully -Path $InstallRoot)) {
        throw "Could not remove $InstallRoot. Try running PowerShell as Administrator and rerun uninstall.ps1."
    }
}

Write-Output "uninstall-started"
