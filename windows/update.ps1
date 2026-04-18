Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[*] $Message"
}

function Resolve-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @("py", "-3")
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    throw "Python 3 was not found in PATH."
}

$defaultInstallRoot = Join-Path $env:USERPROFILE "Hashcat-Pwnagotchi-server"
$installRoot = if ($env:HASHCAT_WPA_SERVER_DIR) { $env:HASHCAT_WPA_SERVER_DIR } else { $defaultInstallRoot }

if (-not (Test-Path $installRoot)) {
    throw "No Windows install was found at $installRoot. Run the Windows install one-liner first."
}

Set-Location $installRoot

if (-not (Test-Path ".git")) {
    throw "$installRoot exists but is not a git checkout."
}

$gitStatus = (& git status --porcelain).Trim()
if ($gitStatus) {
    throw "Update aborted because the repo has uncommitted changes. Commit or stash them first."
}

Write-Step "Fetching latest changes..."
& git fetch origin main
& git pull --ff-only origin main

$python = Resolve-Python

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Step "Virtual environment not found. Recreating it..."
    & $python[0] $python[1..($python.Length - 1)] -m venv .venv
}

$venvPython = Join-Path $installRoot ".venv\Scripts\python.exe"

Write-Step "Refreshing Python dependencies..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

$userBinDir = Join-Path $env:USERPROFILE ".local\bin"
New-Item -ItemType Directory -Path $userBinDir -Force | Out-Null
Copy-Item (Join-Path $installRoot "windows\crackserver.ps1") (Join-Path $userBinDir "crackserver.ps1") -Force
Copy-Item (Join-Path $installRoot "windows\crackserver.cmd") (Join-Path $userBinDir "crackserver.cmd") -Force

Write-Host ""
Write-Host "=========================================================================="
Write-Host "[+] Windows update complete."
Write-Host "[+] Repository:    $installRoot"
Write-Host "[+] Start command: crackserver start"
Write-Host "[+] Dashboard URL: http://127.0.0.1:9111"
Write-Host "=========================================================================="
