Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:InstallerScriptPath = $PSCommandPath
$script:BootstrapScriptPath = $null

function Get-InstallerScriptPath {
    if (-not [string]::IsNullOrWhiteSpace($script:InstallerScriptPath)) {
        return $script:InstallerScriptPath
    }

    $bootstrapSource = $env:HASHCAT_WPA_INSTALL_SOURCE
    if ([string]::IsNullOrWhiteSpace($bootstrapSource)) {
        $bootstrapSource = "https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/install.ps1"
    }

    $bootstrapTarget = Join-Path ([IO.Path]::GetTempPath()) ("hashcat-wpa-install-" + [guid]::NewGuid().ToString("N") + ".ps1")
    if ($bootstrapSource -match '^[A-Za-z]:\\' -or $bootstrapSource -match '^\\\\') {
        Copy-Item -LiteralPath $bootstrapSource -Destination $bootstrapTarget -Force
    } else {
        Invoke-WebRequest -Uri $bootstrapSource -OutFile $bootstrapTarget -UseBasicParsing
    }

    $script:InstallerScriptPath = $bootstrapTarget
    $script:BootstrapScriptPath = $bootstrapTarget
    return $bootstrapTarget
}

function Ensure-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        return $true
    }

    $scriptPath = Get-InstallerScriptPath
    $launchArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $scriptPath
    )
    try {
        $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $launchArgs -Wait -PassThru
        if ($process.ExitCode -ne 0) {
            try {
                $probe = Invoke-WebRequest -Uri "http://127.0.0.1:9111" -UseBasicParsing -TimeoutSec 5
                if ($probe.StatusCode -eq 200) {
                    return $false
                }
            } catch {
            }
        }
        if ($process.ExitCode -ne 0) {
            throw "Elevated installer failed with exit code $($process.ExitCode)."
        }
        return $false
    } finally {
        if ($script:BootstrapScriptPath -and (Test-Path -LiteralPath $script:BootstrapScriptPath)) {
            Remove-Item -LiteralPath $script:BootstrapScriptPath -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not (Ensure-Administrator)) {
    return
}

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
$ProgressFile = Join-Path $LogsRoot "app_update.progress"
$NvidiaProgressFile = Join-Path $LogsRoot "nvidia_install.progress"
$script:NvidiaDriverStatus = "not-needed"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Step([string]$Message) {
    Write-Host "[*] $Message"
}

function Write-ProgressState([string]$State, [int]$Percent, [string]$Message) {
    New-Item -ItemType Directory -Path $LogsRoot -Force | Out-Null
    Set-Content -LiteralPath $ProgressFile -Value "$State|$Percent|$Message"
}

function Write-NvidiaProgressState([string]$State, [int]$Percent, [string]$Message) {
    New-Item -ItemType Directory -Path $LogsRoot -Force | Out-Null
    Set-Content -LiteralPath $NvidiaProgressFile -Value "$State|$Percent|$Message"
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
    $nvidiaSmi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    $nvidiaSmiPath = if ($nvidiaSmi) { $nvidiaSmi.Source } else { "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe" }
    if (-not (Test-Path $nvidiaSmiPath)) {
        return $false
    }

    try {
        & $nvidiaSmiPath -L *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Ensure-NvidiaDriverSupport {
    if (-not (Test-NvidiaGpuPresent)) {
        Write-Step "Skipping NVIDIA driver installation (no NVIDIA GPU detected)."
        Write-NvidiaProgressState "not-applicable" 100 "No NVIDIA GPU detected"
        return
    }

    if (Test-NvidiaDriverReady) {
        $script:NvidiaDriverStatus = "already-installed"
        Write-Step "NVIDIA GPU runtime already appears to be available."
        Write-NvidiaProgressState "success" 100 "NVIDIA drivers are already installed"
        return
    }

    $helperScript = Join-Path $CurrentRoot "windows\install_nvidia_drivers.ps1"
    if (-not (Test-Path $helperScript)) {
        $script:NvidiaDriverStatus = "manual-required"
        Write-Step "NVIDIA GPU detected, but the NVIDIA driver helper script is missing."
        return
    }

    Write-Step "NVIDIA GPU detected. Attempting automatic driver installation and validation"
    Write-NvidiaProgressState "running" 15 "Checking NVIDIA driver support"
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helperScript check
        if (Test-NvidiaDriverReady) {
            $script:NvidiaDriverStatus = "installed"
            Write-NvidiaProgressState "success" 100 "NVIDIA drivers are ready"
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
        Write-ProgressState "running" 20 "Using the local repository contents"
        return @{ Root = $localSource; Temp = $null }
    }

    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("hashcat-wpa-win-" + [guid]::NewGuid().ToString("N"))
    $zipPath = Join-Path $tempRoot "repo.zip"
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    Write-Step "Downloading latest repository archive from GitHub"
    Write-ProgressState "running" 20 "Downloading the latest application source"
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
    Write-ProgressState "running" 35 "Refreshing application files"
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
    Copy-Item -Path (Join-Path $SourceDir "*") -Destination $DestinationDir -Recurse -Force
    return $true
}

function Ensure-7ZipExe {
    $candidate = Get-Command 7z.exe -ErrorAction SilentlyContinue
    if ($candidate) {
        return $candidate.Source
    }

    $sevenZipDir = Join-Path $ToolsRoot "7zip"
    $sevenZipExe = Join-Path $sevenZipDir "7zr.exe"
    if (Test-Path $sevenZipExe) {
        return $sevenZipExe
    }

    New-Item -ItemType Directory -Path $sevenZipDir -Force | Out-Null
    $sevenZipUrl = "https://www.7-zip.org/a/7zr.exe"
    Write-Step "Downloading portable 7-Zip extractor for the official Hashcat archive"
    Write-ProgressState "running" 72 "Downloading the Hashcat extractor"
    Invoke-WebRequest -Uri $sevenZipUrl -OutFile $sevenZipExe -UseBasicParsing
    if (-not (Test-Path $sevenZipExe)) {
        throw "Could not download 7zr.exe."
    }
    return $sevenZipExe
}

function Install-HashcatRelease {
    $hashcatVersion = "7.1.2"
    $archiveName = "hashcat-$hashcatVersion.7z"
    $downloadUris = @(
        "https://hashcat.net/files/$archiveName",
        "https://github.com/hashcat/hashcat/releases/download/v$hashcatVersion/$archiveName"
    )
    $archivePath = Join-Path ([IO.Path]::GetTempPath()) ("hashcat-" + [guid]::NewGuid().ToString("N") + ".7z")
    $extractRoot = Join-Path ([IO.Path]::GetTempPath()) ("hashcat-extract-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
    $sevenZipExe = Ensure-7ZipExe

    try {
        $downloaded = $false
        foreach ($uri in $downloadUris) {
            try {
                Write-Step "Downloading official Hashcat release from $uri"
                Write-ProgressState "running" 78 "Downloading the official Hashcat release"
                Invoke-WebRequest -Uri $uri -OutFile $archivePath -UseBasicParsing
                $downloaded = $true
                break
            } catch {
                Write-Step "Warning: download attempt failed for $uri"
            }
        }

        if (-not $downloaded) {
            throw "Could not download the Hashcat release archive."
        }

        & $sevenZipExe x "-o$extractRoot" -y $archivePath | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to extract the Hashcat release archive."
        }
        Write-ProgressState "running" 86 "Extracting the Hashcat release"

        $sourceDir = Get-ChildItem -LiteralPath $extractRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "hashcat-*" } |
            Select-Object -First 1
        if (-not $sourceDir) {
            throw "Could not locate extracted Hashcat files."
        }

        $destinationRoot = Join-Path $ToolsRoot "hashcat"
        if (Test-Path $destinationRoot) {
            Remove-Item -LiteralPath $destinationRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null
        Copy-Item -Path (Join-Path $sourceDir.FullName "*") -Destination $destinationRoot -Recurse -Force
        Ensure-MachinePathEntry -PathEntry $destinationRoot
        Write-ProgressState "running" 92 "Installing the Hashcat binaries"
    } finally {
        if (Test-Path $archivePath) {
            Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path $extractRoot) {
            Remove-Item -LiteralPath $extractRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Ensure-HashcatTool([string]$PythonExe) {
    if (Get-Command hashcat.exe -ErrorAction SilentlyContinue) {
        return $true
    }

    $bundledDir = Join-Path $BundledToolsRoot "hashcat"
    $installedDir = Join-Path $ToolsRoot "hashcat"
    $copied = Copy-BundledToolDirectory -SourceDir $bundledDir -DestinationDir $installedDir
    if ($copied) {
        Ensure-MachinePathEntry -PathEntry $installedDir
    } elseif (-not (Get-Command hashcat.exe -ErrorAction SilentlyContinue)) {
        try {
            Install-HashcatRelease
        } catch {
            Write-Step "Warning: automatic Hashcat download failed - $($_.Exception.Message)"
        }
    }

    if (-not (Get-Command hashcat.exe -ErrorAction SilentlyContinue)) {
        Write-Step "Warning: hashcat.exe was not found. Bundle it under windows\\tools\\hashcat or let the installer download the official release."
        return $false
    }

    return $true
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
    $venvPython = Join-Path $VenvRoot "Scripts\python.exe"
    $null = Ensure-HashcatTool -PythonExe $venvPython
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
$InstallDebugLog = Join-Path $LogsRoot "install_debug.log"
try {
    Start-Transcript -Path $InstallDebugLog -Append | Out-Null
} catch {
}

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
    Write-ProgressState "running" 30 "Installing the application files"
    Copy-RepoTree -SourceRoot $source.Root -DestinationRoot $CurrentRoot

    Write-Step "Creating Python virtual environment"
    Write-ProgressState "running" 40 "Creating the Python virtual environment"
    $pythonCmd = Get-PythonCommand
    Invoke-PythonCommand -PythonCommand $pythonCmd -Arguments @("-m", "venv", $VenvRoot)

    $venvPython = Join-Path $VenvRoot "Scripts\python.exe"
    Write-Step "Installing Python dependencies"
    Write-ProgressState "running" 50 "Installing Python dependencies"
    & $venvPython -m pip install --upgrade pip wheel
    & $venvPython -m pip install -r (Join-Path $CurrentRoot "requirements.txt")

    Write-Step "Installing crackserver command wrapper"
    Write-ProgressState "running" 60 "Installing the command wrapper"
    Copy-Item -LiteralPath (Join-Path $CurrentRoot "windows\crackserver.ps1") -Destination (Join-Path $BinRoot "crackserver.ps1") -Force
    Copy-Item -LiteralPath (Join-Path $CurrentRoot "windows\crackserver.cmd") -Destination (Join-Path $BinRoot "crackserver.cmd") -Force
    Ensure-MachinePathEntry -PathEntry $BinRoot
    Write-ProgressState "running" 70 "Downloading and installing Hashcat"
    Install-HashcatToolchain

    Write-Step "Configuring Windows autostart task"
    Write-ProgressState "running" 85 "Configuring autostart"
    Invoke-CheckedPowerShellFile -ScriptPath (Join-Path $CurrentRoot "windows\autostart_service.ps1") -Arguments @("enable")

    Write-Step "Starting the dashboard service"
    Write-ProgressState "running" 95 "Starting the dashboard service"
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
    try {
        Stop-Transcript | Out-Null
    } catch {
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
Write-ProgressState "success" 100 "Windows installation completed successfully"
Write-Host "[!] If 'crackserver' is not recognized immediately, open a NEW PowerShell window or run:"
Write-Host "[!] C:\ProgramData\HashcatWPAServer\bin\crackserver.cmd status"
Write-Host "=========================================================================="
