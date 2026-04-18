Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoZipUrl = "https://github.com/EpicNori/Hashcat-Pwnagotchi-server/archive/refs/heads/main.zip"
$InstallRoot = "C:\ProgramData\HashcatWPAServer"
$CurrentRoot = Join-Path $InstallRoot "current"
$VenvRoot = Join-Path $InstallRoot "venv"
$DataRoot = Join-Path $InstallRoot "data"
$LogsRoot = Join-Path $InstallRoot "logs"
$BinRoot = Join-Path $InstallRoot "bin"
$TaskName = "HashcatWPAServer"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Step([string]$Message) {
    Write-Host "[*] $Message"
}

function Get-LocalSourceRoot {
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "app")) -and (Test-Path (Join-Path $PSScriptRoot "requirements.txt"))) {
        return $PSScriptRoot
    }
    return $null
}

function Get-SourceRoot {
    $localSource = Get-LocalSourceRoot
    if ($localSource) {
        Write-Step "Using local repository contents from $localSource"
        return @{ Root = $localSource; Temp = $null }
    }

    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("hashcat-wpa-win-" + [guid]::NewGuid().ToString("N"))
    $zipPath = Join-Path $tempRoot "repo.zip"
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    Write-Step "Downloading latest repository archive from GitHub"
    Invoke-WebRequest -Uri $RepoZipUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $tempRoot -Force
    $sourceRoot = Get-ChildItem -Path $tempRoot -Directory | Where-Object { $_.Name -like "Hashcat-Pwnagotchi-server-*" } | Select-Object -First 1
    if (-not $sourceRoot) {
        throw "Could not locate extracted repository contents."
    }
    return @{ Root = $sourceRoot.FullName; Temp = $tempRoot }
}

function Copy-RepoTree([string]$SourceRoot, [string]$DestinationRoot) {
    if (Test-Path $DestinationRoot) {
        Remove-Item -LiteralPath $DestinationRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
    $robocopyArgs = @(
        $SourceRoot,
        $DestinationRoot,
        "/MIR",
        "/XD", ".git", ".github", "__pycache__", ".venv", "venv"
    )
    & robocopy @robocopyArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
}

function Get-PythonCommand {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        return @($py.Source, "-3")
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }

    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3 was not found and winget is unavailable. Install Python 3.11+ and rerun the installer."
    }

    Write-Step "Installing Python 3.11 with winget"
    & $winget.Source install -e --id Python.Python.3.11 --scope machine --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw "winget failed to install Python."
    }

    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        return @($py.Source, "-3")
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }
    throw "Python installation completed, but python.exe/py.exe is still not available in PATH."
}

function Ensure-MachinePathEntry([string]$PathEntry) {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $parts = @()
    if ($machinePath) {
        $parts = $machinePath.Split(';', [System.StringSplitOptions]::RemoveEmptyEntries)
    }
    if ($parts -contains $PathEntry) {
        return
    }
    $newPath = if ($machinePath) { "$machinePath;$PathEntry" } else { $PathEntry }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "Machine")
    $env:Path = "$env:Path;$PathEntry"
}

function Invoke-PythonCommand([string[]]$PythonCommand, [string[]]$Arguments) {
    if ($PythonCommand.Length -gt 1) {
        & $PythonCommand[0] $PythonCommand[1..($PythonCommand.Length - 1)] @Arguments
    }
    else {
        & $PythonCommand[0] @Arguments
    }
}

if (-not (Test-IsAdministrator)) {
    throw "Please run this installation script from an elevated PowerShell session."
}

Write-Step "Preparing Windows installation directories"
New-Item -ItemType Directory -Path $InstallRoot, $DataRoot, $LogsRoot, $BinRoot -Force | Out-Null

$source = Get-SourceRoot
try {
    Write-Step "Installing application files into $CurrentRoot"
    Copy-RepoTree -SourceRoot $source.Root -DestinationRoot $CurrentRoot

    Write-Step "Creating Python virtual environment"
    $pythonCmd = Get-PythonCommand
    Invoke-PythonCommand -PythonCommand $pythonCmd -Arguments @("-m", "venv", $VenvRoot)

    $venvPython = Join-Path $VenvRoot "Scripts\python.exe"
    Write-Step "Installing Python dependencies"
    & $venvPython -m pip install --upgrade pip wheel
    & $venvPython -m pip install -r (Join-Path $CurrentRoot "requirements.txt")

    Write-Step "Installing crackserver command wrapper"
    Copy-Item -LiteralPath (Join-Path $CurrentRoot "windows\crackserver.ps1") -Destination (Join-Path $BinRoot "crackserver.ps1") -Force
    Copy-Item -LiteralPath (Join-Path $CurrentRoot "windows\crackserver.cmd") -Destination (Join-Path $BinRoot "crackserver.cmd") -Force
    Ensure-MachinePathEntry -PathEntry $BinRoot

    Write-Step "Configuring Windows autostart task"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $CurrentRoot "windows\autostart_service.ps1") enable

    Write-Step "Starting the dashboard service"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $CurrentRoot "windows\run_server.ps1") -InstallRoot $InstallRoot

    Write-Step "Opening local firewall port 9111"
    try {
        $ruleName = "HashcatWPAServer 9111"
        if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 9111 | Out-Null
        }
    } catch {
    }
}
finally {
    if ($source.Temp -and (Test-Path $source.Temp)) {
        Remove-Item -LiteralPath $source.Temp -Recurse -Force
    }
}

$ipAddresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "169.254.*" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -ExpandProperty IPAddress -Unique
$networkIp = if ($ipAddresses) { $ipAddresses[0] } else { "YOUR_SERVER_IP" }
$toolWarnings = @()
if (-not (Get-Command hashcat.exe -ErrorAction SilentlyContinue)) {
    $toolWarnings += "hashcat.exe was not found in PATH. Install Hashcat to run cracking jobs."
}
if (-not (Get-Command hcxpcapngtool.exe -ErrorAction SilentlyContinue)) {
    $toolWarnings += "hcxpcapngtool.exe was not found in PATH. Upload .22000 files directly or install hcxtools for raw capture conversion."
}
if (-not (Get-Command hcxhashtool.exe -ErrorAction SilentlyContinue)) {
    $toolWarnings += "hcxhashtool.exe was not found in PATH. ESSID splitting from .22000 files requires hcxtools."
}

Write-Host ""
Write-Host "=========================================================================="
Write-Host "[+] SUCCESS! hashcat-wpa-server has been installed and is now running on Windows."
Write-Host "[+] It will also start automatically on boot via Scheduled Tasks."
Write-Host "[+]"
Write-Host "[+] Web Interface URL:   http://127.0.0.1:9111"
Write-Host "[+] Network Access:      http://$networkIp`:9111"
Write-Host "[+] Global CLI:          crackserver"
Write-Host "[+]"
Write-Host "[+] Default Login User:  admin"
Write-Host "[+] Default Password:    changeme"
foreach ($warning in $toolWarnings) {
    Write-Host "[!] $warning"
}
Write-Host "=========================================================================="
