param(
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CurrentRoot = Join-Path $InstallRoot "current"
$VenvPython = Join-Path $InstallRoot "venv\Scripts\python.exe"
$LogsRoot = Join-Path $InstallRoot "logs"
$PidFile = Join-Path $InstallRoot "server.pid"
$StdoutLog = Join-Path $LogsRoot "server_stdout.log"
$StderrLog = Join-Path $LogsRoot "server_stderr.log"

New-Item -ItemType Directory -Path $LogsRoot, (Join-Path $InstallRoot "data") -Force | Out-Null

if (-not (Test-Path $CurrentRoot)) {
    throw "Installed application root not found: $CurrentRoot"
}
if (-not (Test-Path $VenvPython)) {
    throw "Virtual environment Python not found: $VenvPython"
}

if (Test-Path $PidFile) {
    $existingPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not [string]::IsNullOrWhiteSpace($existingPid) -and (Get-Process -Id $existingPid.Trim() -ErrorAction SilentlyContinue)) {
        Write-Output "already-running"
        exit 0
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

$env:HASHCAT_WPA_SERVER_HOME = Join-Path $InstallRoot "data"
$env:HASHCAT_WPA_INSTALL_ROOT = $InstallRoot
$env:PYTHONUNBUFFERED = "1"
if (-not $env:HASHCAT_ADMIN_USER) {
    $env:HASHCAT_ADMIN_USER = "admin"
}
if (-not $env:HASHCAT_ADMIN_PASSWORD) {
    $env:HASHCAT_ADMIN_PASSWORD = "changeme"
}

function Start-ServerLauncher {
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
for key in ("SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP"):
    value = os.environ.get(key)
    if value:
        env[key] = value
env["PATH"] = _clean_path()
env["HASHCAT_WPA_SERVER_HOME"] = __DATA_ROOT__
env["HASHCAT_WPA_INSTALL_ROOT"] = __INSTALL_ROOT__
env["PYTHONUNBUFFERED"] = "1"
env["HASHCAT_ADMIN_USER"] = os.environ.get("HASHCAT_ADMIN_USER", "admin")
env["HASHCAT_ADMIN_PASSWORD"] = os.environ.get("HASHCAT_ADMIN_PASSWORD", "changeme")

stdout_log = open(__STDOUT_LOG__, "ab", buffering=0)
stderr_log = open(__STDERR_LOG__, "ab", buffering=0)
process = subprocess.Popen(
    [__VENV_PYTHON__, "-m", "waitress", "--host=0.0.0.0", "--port=9111", "app:app"],
    cwd=__CURRENT_ROOT__,
    stdout=stdout_log,
    stderr=stderr_log,
    env=env,
)
sys.stdout.write(str(process.pid))
sys.stdout.flush()
'@

    $launcher = $launcher.Replace("__DATA_ROOT__", (ConvertTo-Json -Compress (Join-Path $InstallRoot "data")))
    $launcher = $launcher.Replace("__INSTALL_ROOT__", (ConvertTo-Json -Compress $InstallRoot))
    $launcher = $launcher.Replace("__STDOUT_LOG__", (ConvertTo-Json -Compress $StdoutLog))
    $launcher = $launcher.Replace("__STDERR_LOG__", (ConvertTo-Json -Compress $StderrLog))
    $launcher = $launcher.Replace("__VENV_PYTHON__", (ConvertTo-Json -Compress $VenvPython))
    $launcher = $launcher.Replace("__CURRENT_ROOT__", (ConvertTo-Json -Compress $CurrentRoot))

    $tempLauncher = Join-Path ([IO.Path]::GetTempPath()) ("hashcat-wpa-launch-" + [guid]::NewGuid().ToString("N") + ".py")
    Set-Content -LiteralPath $tempLauncher -Value $launcher -Encoding UTF8
    try {
        $pidText = & $VenvPython $tempLauncher
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to start the dashboard launcher."
        }
    } finally {
        Remove-Item -LiteralPath $tempLauncher -Force -ErrorAction SilentlyContinue
    }

    $pidValue = ($pidText | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($pidValue)) {
        throw "The dashboard launcher did not return a process id."
    }

    Set-Content -LiteralPath $PidFile -Value $pidValue
    Start-Sleep -Seconds 3

    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if (-not $process) {
        $stderr = if (Test-Path $StderrLog) { Get-Content -LiteralPath $StderrLog -Tail 20 | Out-String } else { "" }
        throw "The dashboard process exited immediately. $stderr"
    }
}

Start-ServerLauncher

Write-Output "started"
