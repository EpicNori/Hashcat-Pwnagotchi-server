[CmdletBinding()]
param(
    [ValidateSet("check", "status")]
    [string]$Action = "check"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-NvidiaGpuPresent {
    try {
        $controllers = Get-CimInstance Win32_VideoController -ErrorAction Stop
        return @($controllers | Where-Object {
            ($_.Name -match "NVIDIA") -or ($_.AdapterCompatibility -match "NVIDIA")
        }).Count -gt 0
    } catch {
        return $false
    }
}

function Get-NvidiaSmiPath {
    $command = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $defaultPath = "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
    if (Test-Path $defaultPath) {
        return $defaultPath
    }

    return $null
}

function Test-NvidiaDriverReady {
    $nvidiaSmi = Get-NvidiaSmiPath
    if (-not $nvidiaSmi) {
        return $false
    }

    try {
        & $nvidiaSmi -L *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
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

function Invoke-WindowsUpdateNvidiaDriverInstall {
    try {
        $session = New-Object -ComObject Microsoft.Update.Session
        $searcher = $session.CreateUpdateSearcher()
        $searchResult = $searcher.Search("IsInstalled=0 and IsHidden=0")
    } catch {
        Write-Output "Windows Update search for NVIDIA drivers failed: $_"
        return $false
    }

    $updates = New-Object -ComObject Microsoft.Update.UpdateColl
    foreach ($update in @($searchResult.Updates)) {
        $title = [string]$update.Title
        if ($title -match "NVIDIA|GeForce") {
            [void]$updates.Add($update)
        }
    }

    if ($updates.Count -eq 0) {
        Write-Output "No pending NVIDIA driver updates were offered by Windows Update."
        return $false
    }

    Write-Output "Windows Update offered $($updates.Count) NVIDIA-related update(s). Downloading..."
    $downloader = $session.CreateUpdateDownloader()
    $downloader.Updates = $updates
    $downloadResult = $downloader.Download()
    if ($downloadResult.ResultCode -notin 2, 3) {
        Write-Output "Windows Update could not download the NVIDIA driver packages."
        return $false
    }

    Write-Output "Installing NVIDIA driver update(s) from Windows Update..."
    $installer = $session.CreateUpdateInstaller()
    $installer.Updates = $updates
    $installResult = $installer.Install()
    if ($installResult.ResultCode -notin 2, 3) {
        Write-Output "Windows Update did not complete NVIDIA driver installation successfully."
        return $false
    }

    if ($installResult.RebootRequired) {
        Write-Output "NVIDIA driver installation completed, but Windows reported that a reboot is required."
    }
    return $true
}

function Install-NvidiaHelperPackage {
    $wingetCmd = Get-WingetCommand
    if (-not $wingetCmd) {
        Write-Output "winget is unavailable, so NVIDIA helper installation cannot continue."
        return $false
    }

    try {
        & $wingetCmd install -e --id Nvidia.GeForceExperience --scope machine --accept-package-agreements --accept-source-agreements --silent --disable-interactivity
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Wait-NvidiaDriverReady([int]$TimeoutSeconds = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-NvidiaDriverReady) {
            return $true
        }
        try {
            & pnputil.exe /scan-devices *> $null
        } catch {
        }
        Start-Sleep -Seconds 5
    }
    return (Test-NvidiaDriverReady)
}

switch ($Action) {
    "status" {
        if (-not (Test-NvidiaGpuPresent)) {
            Write-Output "visible:no-nvidia-gpu driver:not-applicable"
        } elseif (Test-NvidiaDriverReady) {
            Write-Output "visible:nvidia-gpu driver:installed"
        } else {
            Write-Output "visible:nvidia-gpu driver:missing"
        }
    }
    "check" {
        if (-not (Test-NvidiaGpuPresent)) {
            Write-Output "No NVIDIA GPU was detected on this system."
            exit 0
        }

        if (Test-NvidiaDriverReady) {
            Write-Output "NVIDIA drivers already appear to be installed."
            exit 0
        }

        if (Invoke-WindowsUpdateNvidiaDriverInstall -and (Wait-NvidiaDriverReady)) {
            Write-Output "NVIDIA driver installation completed through Windows Update."
            exit 0
        }

        Write-Output "Windows Update did not fully provision an NVIDIA driver. Falling back to NVIDIA helper installation."
        if (-not (Install-NvidiaHelperPackage)) {
            throw "Automatic NVIDIA driver/helper installation failed."
        }

        if (Wait-NvidiaDriverReady) {
            Write-Output "NVIDIA helper installation completed and a working driver is now available."
            exit 0
        }

        throw "Automatic NVIDIA installation completed, but a working NVIDIA driver is still not available. A reboot or manual driver install is required."
    }
}
