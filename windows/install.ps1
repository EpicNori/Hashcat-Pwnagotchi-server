Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[*] $Message"
}

function Ensure-Command {
    param(
        [string]$CommandName,
        [string]$WingetId = ""
    )

    if (Get-Command $CommandName -ErrorAction SilentlyContinue) {
        return
    }

    if (-not $WingetId) {
        throw "Required command '$CommandName' was not found in PATH."
    }

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Required command '$CommandName' was not found and winget is unavailable to install it automatically."
    }

    Write-Step "Installing missing dependency '$CommandName' with winget..."
    & winget install --id $WingetId --exact --accept-package-agreements --accept-source-agreements
    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $env:Path = "$machinePath;$userPath"
    }
    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "Installed '$CommandName', but it is still not available in PATH. Open a new PowerShell window and rerun the installer."
    }
}

function Resolve-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @("py", "-3")
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    throw "Python 3 was not found after installation."
}

function Ensure-PathContains {
    param([string]$DirectoryPath)

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathParts = @()
    if ($userPath) {
        $pathParts = $userPath.Split(';') | Where-Object { $_ }
    }
    if ($pathParts -contains $DirectoryPath) {
        return
    }

    $newPath = if ($userPath) { "$userPath;$DirectoryPath" } else { $DirectoryPath }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    $env:Path = "$env:Path;$DirectoryPath"
}

$defaultInstallRoot = Join-Path $env:USERPROFILE "Hashcat-Pwnagotchi-server"
$installRoot = if ($env:HASHCAT_WPA_SERVER_DIR) { $env:HASHCAT_WPA_SERVER_DIR } else { $defaultInstallRoot }
$userBinDir = Join-Path $env:USERPROFILE ".local\bin"

Write-Step "Preparing Windows local install..."
Ensure-Command -CommandName "git" -WingetId "Git.Git"
Ensure-Command -CommandName "py" -WingetId "Python.Python.3.11"

$python = Resolve-Python

if (-not (Test-Path $installRoot)) {
    Write-Step "Cloning repository into $installRoot"
    & git clone https://github.com/EpicNori/Hashcat-Pwnagotchi-server.git $installRoot
} else {
    Write-Step "Repository already exists at $installRoot"
}

Set-Location $installRoot

if (-not (Test-Path ".git")) {
    throw "$installRoot exists but is not a git checkout."
}

Write-Step "Creating Python virtual environment..."
& $python[0] $python[1..($python.Length - 1)] -m venv .venv

$venvPython = Join-Path $installRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment setup failed. Missing $venvPython"
}

Write-Step "Upgrading pip and installing Python dependencies..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

Write-Step "Installing crackserver command..."
New-Item -ItemType Directory -Path $userBinDir -Force | Out-Null
Copy-Item (Join-Path $installRoot "windows\crackserver.ps1") (Join-Path $userBinDir "crackserver.ps1") -Force
Copy-Item (Join-Path $installRoot "windows\crackserver.cmd") (Join-Path $userBinDir "crackserver.cmd") -Force
Ensure-PathContains -DirectoryPath $userBinDir

Write-Step "Starting web server..."
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $installRoot "windows\crackserver.ps1") start

$hashcatDetected = [bool](Get-Command hashcat -ErrorAction SilentlyContinue)
$hcxpcapngtoolDetected = [bool](Get-Command hcxpcapngtool -ErrorAction SilentlyContinue)
$hcxhashtoolDetected = [bool](Get-Command hcxhashtool -ErrorAction SilentlyContinue)

Write-Host ""
Write-Host "=========================================================================="
Write-Host "[+] Windows local install complete."
Write-Host "[+] Repository:        $installRoot"
Write-Host "[+] Virtual env:       $installRoot\.venv"
Write-Host "[+] crackserver cmd:   $userBinDir\crackserver.cmd"
Write-Host "[+] Start command:     crackserver start"
Write-Host "[+] Dashboard URL:     http://127.0.0.1:9111"
Write-Host "[+] Default login:     admin / changeme"
Write-Host "[+]"
if (-not $hashcatDetected) {
    Write-Host "[!] hashcat was not detected in PATH. Cracking jobs require a Windows hashcat install."
}
if (-not $hcxpcapngtoolDetected -or -not $hcxhashtoolDetected) {
    Write-Host "[!] hcx tools were not detected in PATH. Raw capture conversion and ESSID splitting require hcxpcapngtool and hcxhashtool."
}
Write-Host "[+] To update later:   irm https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/windows/update.ps1 | iex"
Write-Host "[+] Server status:     crackserver status"
Write-Host "=========================================================================="
