Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoZipUrl = "https://github.com/EpicNori/Hashcat-Pwnagotchi-server/archive/refs/heads/main.zip"
$InstallRoot = "C:\ProgramData\HashcatWPAServer"
$CurrentRoot = Join-Path $InstallRoot "current"
$VenvRoot = Join-Path $InstallRoot "venv"
$DataRoot = Join-Path $InstallRoot "data"
$LogsRoot = Join-Path $InstallRoot "logs"
$BinRoot = Join-Path $InstallRoot "bin"
$ToolsRoot = Join-Path $InstallRoot "tools"
$BundledToolsRoot = Join-Path $CurrentRoot "windows\tools"
$TaskName = "HashcatWPAServer"
$script:NvidiaDriverStatus = "not-needed"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Step([string]$Message) {
    Write-Host "[*] $Message"
}

function Test-NvidiaGpuPresent {
    try {
        $controllers = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
        if (-not $controllers) {
            Write-Step "No video controllers detected via WMI."
            return $false
        }
        $nvidiaGpus = @($controllers | Where-Object {
            ($_.Name -match "NVIDIA|GeForce") -or ($_.AdapterCompatibility -match "NVIDIA")
        })
        $nvidiaCount = $nvidiaGpus.Count
        if ($nvidiaCount -gt 0) {
            Write-Step "Detected $nvidiaCount NVIDIA GPU(s): $($nvidiaGpus.Name -join ', ')"
            return $true
        }
        Write-Step "No NVIDIA GPU detected."
        $otherGpus = @($controllers | Where-Object { $_.Name -notmatch "NVIDIA|GeForce" })
        if ($otherGpus.Count -gt 0) {
            Write-Step "Other GPU(s) found: $($otherGpus.Name -join ', ')"
            Write-Step "Note: Hashcat on Windows requires NVIDIA GPU with CUDA support for optimal performance."
        }
        return $false
    } catch {
        Write-Step "Warning: Could not query video controllers - $_"
        return $false
    }
}

function Test-NvidiaDriverReady {
    if (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue) {
        return $true
    }

    $defaultNvsmPath = "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
    return Test-Path $defaultNvsmPath
}

function Get-WingetCommand {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        return $winget.Source
    }

    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop
    } catch {
    }

    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        return $winget.Source
    }

    return $null
}

function Ensure-NvidiaDriverSupport {
    if (-not (Test-NvidiaGpuPresent)) {
        Write-Step "Skipping NVIDIA driver installation (no NVIDIA GPU detected)."
        return
    }

    if (Test-NvidiaDriverReady) {
        $script:NvidiaDriverStatus = "already-installed"
        Write-Step "NVIDIA GPU runtime already appears to be available."
        return
    }

    $wingetCmd = Get-WingetCommand
    if (-not $wingetCmd) {
        $script:NvidiaDriverStatus = "manual-required"
        Write-Step "NVIDIA GPU detected, but winget is unavailable for automatic driver helper installation."
        return
    }

    Write-Step "NVIDIA GPU detected. Attempting to install NVIDIA GeForce Experience so drivers can be installed automatically"
    try {
        & $wingetCmd install -e --id Nvidia.GeForceExperience --scope machine --accept-package-agreements --accept-source-agreements --silent --disable-interactivity
        if ($LASTEXITCODE -eq 0) {
            $script:NvidiaDriverStatus = "installed"
            return
        }
    } catch {
    }

    $script:NvidiaDriverStatus = "manual-required"
    Write-Step "Automatic NVIDIA helper installation did not complete successfully."
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
    New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
    if (Test-Path $DestinationRoot) {
        Write-Step "Preserving existing runtime logs while refreshing application files"
    }
    $robocopyArgs = @(
        $SourceRoot,
        $DestinationRoot,
        "/MIR",
        "/XD", ".git", ".github", "__pycache__", ".venv", "venv", "logs"
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

function Expand-ArchiveCrossFormat {
    param(
        [string]$ArchivePath,
        [string]$DestinationPath
    )

    if (Test-Path $DestinationPath) {
        Remove-Item -LiteralPath $DestinationPath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $DestinationPath -Force | Out-Null

    try {
        & tar.exe -xf $ArchivePath -C $DestinationPath
        if ($LASTEXITCODE -eq 0) {
            return
        }
    } catch {
    }

    Expand-Archive -Path $ArchivePath -DestinationPath $DestinationPath -Force
}

function Copy-BundledToolDirectory([string]$SourceDir, [string]$DestinationDir) {
    if (-not (Test-Path $SourceDir)) {
        return $false
    }
    New-Item -ItemType Directory -Path $DestinationDir -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $SourceDir "*") -Destination $DestinationDir -Recurse -Force
    return $true
}

function Require-ToolInPath([string]$ToolName, [string]$BundledSubdir, [string]$MissingMessage) {
    if (Get-Command $ToolName -ErrorAction SilentlyContinue) {
        return
    }

    $bundledDir = Join-Path $BundledToolsRoot $BundledSubdir
    $installedDir = Join-Path $ToolsRoot $BundledSubdir
    $copied = Copy-BundledToolDirectory -SourceDir $bundledDir -DestinationDir $installedDir
    if ($copied) {
        Ensure-MachinePathEntry -PathEntry $installedDir
    }

    if (-not (Get-Command $ToolName -ErrorAction SilentlyContinue)) {
        throw $MissingMessage
    }
}

function Try-InstallWSL {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        return $false
    }

    try {
        & wsl.exe -l -v | Out-Null
        return $true
    } catch {
    }

    try {
        Write-Step "Attempting to install WSL Ubuntu for Linux hcxtools support"
        & wsl.exe --install -d Ubuntu --no-launch
        return $true
    } catch {
        return $false
    }
}

function Install-HashcatToolchain() {
    Ensure-NvidiaDriverSupport
    Require-ToolInPath -ToolName "hashcat.exe" -BundledSubdir "hashcat" -MissingMessage "hashcat.exe is required. Bundle it under windows\\tools\\hashcat or install it system-wide before running the installer."
    $hcxBundled = Copy-BundledToolDirectory -SourceDir (Join-Path $BundledToolsRoot "hcxtools") -DestinationDir (Join-Path $ToolsRoot "hcxtools")
    if ($hcxBundled) {
        Ensure-MachinePathEntry -PathEntry (Join-Path $ToolsRoot "hcxtools")
    }
    if (-not (Get-Command hcxpcapngtool.exe -ErrorAction SilentlyContinue)) {
        Try-InstallWSL | Out-Null
    }
}

function Invoke-PythonCommand([string[]]$PythonCommand, [string[]]$Arguments) {
    if ($PythonCommand.Length -gt 1) {
        & $PythonCommand[0] $PythonCommand[1..($PythonCommand.Length - 1)] @Arguments
    }
    else {
        & $PythonCommand[0] @Arguments
    }
}

function Invoke-CheckedPowerShellFile([string]$ScriptPath, [string[]]$Arguments = @()) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PowerShell helper failed: $ScriptPath"
    }
}

if (-not (Test-IsAdministrator)) {
    throw "Please run this installation script from an elevated PowerShell session."
}

Write-Step "Preparing Windows installation directories"
New-Item -ItemType Directory -Path $InstallRoot, $DataRoot, $LogsRoot, $BinRoot, $ToolsRoot -Force | Out-Null

if (Test-Path $CurrentRoot) {
    $existingTask = Join-Path $CurrentRoot "windows\autostart_service.ps1"
    $existingCli = Join-Path $CurrentRoot "windows\crackserver.ps1"
    if (Test-Path $existingTask) {
        try {
            Invoke-CheckedPowerShellFile -ScriptPath $existingTask -Arguments @("disable", "-InstallRoot", $InstallRoot)
        } catch {
        }
    }
    if (Test-Path $existingCli) {
        try {
            Invoke-CheckedPowerShellFile -ScriptPath $existingCli -Arguments @("stop", "-InstallRoot", $InstallRoot)
        } catch {
        }
    }
    Get-Process -Name hashcat -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

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
    Install-HashcatToolchain

    Write-Step "Configuring Windows autostart task"
    Invoke-CheckedPowerShellFile -ScriptPath (Join-Path $CurrentRoot "windows\autostart_service.ps1") -Arguments @("enable")

    Write-Step "Starting the dashboard service"
    Invoke-CheckedPowerShellFile -ScriptPath (Join-Path $CurrentRoot "windows\run_server.ps1") -Arguments @("-InstallRoot", $InstallRoot)

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
    $toolWarnings += "hashcat.exe was not found in PATH even after installation."
}
if (-not (Get-Command hcxpcapngtool.exe -ErrorAction SilentlyContinue)) {
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        $toolWarnings += "hcxpcapngtool.exe was not found natively. Raw .cap/.pcap/.pcapng uploads can use WSL hcxtools if Ubuntu and hcxtools are installed there."
    } else {
        $toolWarnings += "hcxpcapngtool.exe was not found natively. Direct .22000 uploads work, but raw .cap/.pcap/.pcapng conversion still needs bundled hcxtools or WSL."
    }
}
if (-not (Get-Command hcxhashtool.exe -ErrorAction SilentlyContinue)) {
    $toolWarnings += "hcxhashtool.exe was not found natively. Direct .22000 uploads still work because Windows can fall back to built-in splitting."
}
switch ($script:NvidiaDriverStatus) {
    "installed" {
        $toolWarnings += "NVIDIA GPU detected. GeForce Experience was installed automatically so NVIDIA drivers can be provisioned. A reboot or first-time NVIDIA setup may still be required before Hashcat can use the GPU."
    }
    "already-installed" {
        $toolWarnings += "NVIDIA GPU and drivers detected. Hashcat should be able to use GPU acceleration."
    }
    "manual-required" {
        $toolWarnings += "NVIDIA GPU detected, but automatic NVIDIA driver/helper installation did not complete. Install the NVIDIA driver manually before expecting GPU cracking to work."
    }
    "not-needed" {
        $toolWarnings += "No NVIDIA GPU detected. Hashcat will run in CPU-only mode. For GPU acceleration, install an NVIDIA GPU with CUDA-capable drivers."
    }
}

Write-Host ""
Write-Host "=========================================================================="
Write-Host "[+] SUCCESS! hashcat-wpa-server has been installed and is now running on Windows."
Write-Host "[+] It will also start automatically on boot via Scheduled Tasks."
Write-Host "[+]"
Write-Host "[+] Web Interface URL:   http://127.0.0.1:9111"
Write-Host "[+] Network Access:      http://$networkIp`:9111"
Write-Host "[+] Global CLI:          crackserver"
Write-Host "[+] Direct CLI Path:     C:\ProgramData\HashcatWPAServer\bin\crackserver.cmd"
Write-Host "[+]"
Write-Host "[+] Default Login User:  admin"
Write-Host "[+] Default Password:    changeme"
foreach ($warning in $toolWarnings) {
    Write-Host "[!] $warning"
}
Write-Host "[!] If 'crackserver' is not recognized immediately, open a NEW PowerShell window or run:"
Write-Host "[!] C:\ProgramData\HashcatWPAServer\bin\crackserver.cmd status"
Write-Host "=========================================================================="
