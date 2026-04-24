param(
    [ValidateSet("run", "status")]
    [string]$Action = "run",
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$LogsRoot = Join-Path $InstallRoot "logs"
$PidFile = Join-Path $InstallRoot "updater.pid"
$LogFile = Join-Path $LogsRoot "updater.log"
$ErrorLogFile = Join-Path $LogsRoot "updater_stderr.log"
$ProgressFile = Join-Path $LogsRoot "app_update.progress"
$NvidiaProgressFile = Join-Path $LogsRoot "nvidia_install.progress"
$UpdateScript = Join-Path $InstallRoot "current\update.ps1"

New-Item -ItemType Directory -Path $LogsRoot -Force | Out-Null

if ($Action -eq "status") {
    if (Test-Path $PidFile) {
        $pidValue = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not [string]::IsNullOrWhiteSpace($pidValue) -and (Get-Process -Id $pidValue.Trim() -ErrorAction SilentlyContinue)) {
            Write-Output "running"
            exit 0
        }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
    Write-Output "idle"
    exit 0
}

function Start-UpdateLauncher {
    $launcher = @'
import os
import subprocess
import sys

def _clean_path():
    parts = []
    for raw_path in (os.environ.get("Path"), os.environ.get("PATH")):
        if not raw_path:
            continue
        for entry in raw_path.split(os.pathsep):
            entry = entry.strip()
            if entry and entry not in parts:
                parts.append(entry)
    return os.pathsep.join(parts)

env = {}
for key in ("SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA"):
    value = os.environ.get(key)
    if value:
        env[key] = value
env["PATH"] = _clean_path()
env["HASHCAT_WPA_PROGRESS_FILE"] = __PROGRESS_FILE__
env["HASHCAT_WPA_NVIDIA_PROGRESS_FILE"] = __NVIDIA_PROGRESS_FILE__

stdout_log = open(__LOG_FILE__, "ab", buffering=0)
stderr_log = open(__ERROR_LOG_FILE__, "ab", buffering=0)
process = subprocess.Popen(
    [__POWERSHELL_EXE__, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", __UPDATE_SCRIPT__],
    cwd=__UPDATE_ROOT__,
    stdout=stdout_log,
    stderr=stderr_log,
    env=env,
)
sys.stdout.write(str(process.pid))
sys.stdout.flush()
'@

    $launcher = $launcher.Replace("__LOG_FILE__", (ConvertTo-Json -Compress $LogFile))
    $launcher = $launcher.Replace("__ERROR_LOG_FILE__", (ConvertTo-Json -Compress $ErrorLogFile))
    $launcher = $launcher.Replace("__POWERSHELL_EXE__", (ConvertTo-Json -Compress (Get-Command powershell.exe).Source))
    $launcher = $launcher.Replace("__UPDATE_SCRIPT__", (ConvertTo-Json -Compress $UpdateScript))
    $launcher = $launcher.Replace("__UPDATE_ROOT__", (ConvertTo-Json -Compress (Split-Path $UpdateScript -Parent)))
    $launcher = $launcher.Replace("__PROGRESS_FILE__", (ConvertTo-Json -Compress $ProgressFile))
    $launcher = $launcher.Replace("__NVIDIA_PROGRESS_FILE__", (ConvertTo-Json -Compress $NvidiaProgressFile))

    $tempLauncher = Join-Path ([IO.Path]::GetTempPath()) ("hashcat-wpa-update-launch-" + [guid]::NewGuid().ToString("N") + ".py")
    Set-Content -LiteralPath $tempLauncher -Value $launcher -Encoding UTF8
    try {
        $pidText = & (Get-Command python.exe).Source $tempLauncher
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to start the update launcher."
        }
    } finally {
        Remove-Item -LiteralPath $tempLauncher -Force -ErrorAction SilentlyContinue
    }

    $pidValue = ($pidText | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($pidValue)) {
        throw "The update launcher did not return a process id."
    }

    Set-Content -LiteralPath $PidFile -Value $pidValue
    Write-Output "started"
}

Start-UpdateLauncher
