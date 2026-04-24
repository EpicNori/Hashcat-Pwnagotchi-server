import datetime
import os
import re
import subprocess
from shutil import which
from functools import lru_cache
from typing import List
from pathlib import Path
from urllib.parse import urlparse, urljoin

from flask import request, Markup

from app.logger import logger


GPU_NAME_MARKERS = ("nvidia", "amd", "radeon", "geforce", "quadro", "rtx", "gtx", "intel arc", "graphics", "gpu")
CPU_NAME_MARKERS = ("cpu", "core(tm)", "xeon", "ryzen", "epyc", "threadripper", "pentium", "celeron", "athlon")


def infer_device_is_gpu(name: str, device_type: str = "") -> bool:
    normalized_name = (name or "").lower()
    normalized_type = (device_type or "").lower()

    if "cpu" in normalized_type:
        return False
    if "gpu" in normalized_type:
        return True

    if any(marker in normalized_name for marker in CPU_NAME_MARKERS):
        return False
    if any(marker in normalized_name for marker in GPU_NAME_MARKERS):
        return True
    return False


def parse_hashcat_devices_output(output: str):
    devices = []

    # Older hashcat format:
    # Device #1: NVIDIA GeForce RTX 3080, 10240/10240 MB, 68MCU
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("*"):
            line = line.lstrip("*").strip()
        if not re.match(r"^(?:Backend\s+)?Device(?:\s+ID)?\s+#\d+", line):
            continue
        try:
            id_part, rest = line.split(':', 1)
            dev_id = re.sub(r"^(?:Backend\s+)?Device(?:\s+ID)?\s+#", "", id_part).strip()
            info_parts = [part.strip() for part in rest.split(',')]
            name = info_parts[0] if info_parts else f"Device {dev_id}"
            memory = next((part for part in info_parts[1:] if "MB" in part or "GB" in part), "Unknown")
            devices.append({
                "id": dev_id,
                "name": name,
                "memory": memory,
                "is_gpu": infer_device_is_gpu(name)
            })
        except Exception:
            continue

    if devices:
        return devices

    # Newer hashcat formats on Linux often use multi-line blocks:
    # Backend Device ID #1
    #   Name...........: NVIDIA GeForce RTX ...
    #   Device Type....: GPU
    #   Processor(s)...: 68
    #   Memory.Total...: 10240 MB
    lines = output.splitlines()
    current = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        backend_match = re.match(r"^(?:Backend\s+)?Device(?:\s+ID)?\s+#(\d+)", line)
        if backend_match:
            if current:
                devices.append(current)
            dev_id = backend_match.group(1)
            current = {
                "id": dev_id,
                "name": f"Device {dev_id}",
                "memory": "Unknown",
                "is_gpu": False
            }
            continue

        if current is None or ":" not in line:
            continue

        key, value = line.split(":", 1)
        normalized_key = key.replace(".", "").strip().lower()
        value = value.strip()

        if normalized_key in ("name", "device name"):
            current["name"] = value
            current["is_gpu"] = infer_device_is_gpu(value, current.get("device_type", ""))
        elif normalized_key in ("device type", "type"):
            current["device_type"] = value
            current["is_gpu"] = infer_device_is_gpu(current.get("name", ""), value)
        elif normalized_key.startswith("memory total") or normalized_key == "global memory":
            current["memory"] = value

    if current:
        devices.append(current)

    return devices


def get_linux_pci_gpus():
    lspci_bin = "lspci"

    try:
        completed = subprocess.run(
            [lspci_bin],
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False
        )
        if completed.returncode != 0:
            return []

        devices = []
        for line in completed.stdout.splitlines():
            lowered = line.lower()
            if not any(marker in lowered for marker in [" vga ", " 3d ", " display "]):
                continue
            if ":" in line:
                name = line.split(":", 2)[-1].strip()
            else:
                name = line.strip()
            devices.append({
                "id": str(len(devices) + 1),
                "name": name,
                "memory": "Unknown",
                "is_gpu": True
            })
        return devices
    except Exception as error:
        logger.error(f"Linux PCI GPU detection failed: {error}")
        return []


def get_windows_video_adapters():
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | Format-List",
            ],
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return []

        devices = []
        current = {}
        for raw_line in completed.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                if current.get("name"):
                    devices.append(current)
                current = {}
                continue
            if ":" not in line:
                continue
            key, value = [part.strip() for part in line.split(":", 1)]
            key = key.lower()
            if key == "name":
                current["name"] = value
            elif key == "adapternram":
                try:
                    current["memory"] = f"{int(value) // (1024 * 1024)} MB"
                except Exception:
                    current["memory"] = "Unknown"
        if current.get("name"):
            devices.append(current)

        normalized = []
        for index, device in enumerate(devices, start=1):
            normalized.append({
                "id": str(index),
                "name": device.get("name", f"Video Adapter {index}"),
                "memory": device.get("memory", "Unknown"),
                "is_gpu": infer_device_is_gpu(device.get("name", "")),
            })
        return normalized
    except Exception as error:
        logger.debug(f"Windows video adapter detection failed: {error}")
        return []


def subprocess_call(args: List[str]):
    """
    :param args: shell args
    """
    args = list(map(str, args))
    cwd = None
    if args:
        executable = Path(args[0]).name.lower()
        if executable in {"hashcat", "hashcat.exe"}:
            resolved_hashcat = resolve_hashcat_executable()
            if resolved_hashcat:
                args[0] = resolved_hashcat
                cwd = str(Path(resolved_hashcat).parent)
    logger.debug(">>> {}".format(' '.join(args)))
    if not all(args):
        raise ValueError(f"Empty arg in {args}")
    try:
        completed = subprocess.run(args, universal_newlines=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    except FileNotFoundError as e:
        executable = args[0] if args else "unknown"
        raise FileNotFoundError(f"Tool not found: '{executable}'. Please ensure it is installed and in your PATH.") from e
        
    if completed.stderr or completed.returncode != 0:
        logger.debug(completed.stdout)
        logger.error(completed.stderr)
    return completed.stdout, completed.stderr


def resolve_hashcat_executable():
    install_root = os.environ.get("HASHCAT_WPA_INSTALL_ROOT")
    if install_root:
        install_root_path = Path(install_root)
        current_root = Path(__file__).resolve().parents[2]
        candidates = [
            install_root_path / "tools" / "hashcat" / "hashcat.exe",
            install_root_path / "tools" / "hashcat.exe",
            install_root_path / "tools" / "hashcat" / "hashcat",
            install_root_path / "current" / "windows" / "tools" / "hashcat" / "hashcat.exe",
            install_root_path / "current" / "windows" / "tools" / "hashcat.exe",
            current_root / "windows" / "tools" / "hashcat" / "hashcat.exe",
            current_root / "windows" / "tools" / "hashcat.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

    env_override = os.environ.get("HASHCAT_EXECUTABLE")
    if env_override:
        override_path = Path(env_override)
        if override_path.exists():
            return str(override_path)

    for command_name in ("hashcat.exe", "hashcat"):
        resolved = which(command_name)
        if resolved:
            return resolved

    return None


def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


def date_formatted() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


@lru_cache()
def hashcat_devices_info():
    try:
        hashcat_devices, _ = subprocess_call(['hashcat', '-I', '--force'])
        hashcat_devices = f"<code>$ hashcat -I --force</code>\n<samp>{hashcat_devices}</samp>"
        return Markup(hashcat_devices.replace('\n', '<br>'))
    except Exception:
        return Markup("Hashcat device information is unavailable. Install hashcat or add it to PATH.")

def get_live_usage():
    """ Returns real-time system usage (CPU, RAM, and GPU if possible) """
    import psutil
    import subprocess
    
    stats = {
        "cpu_usage": psutil.cpu_percent(),
        "ram_usage": psutil.virtual_memory().percent,
        "cpu_temp": 0,
        "gpus": []
    }
    
    # Try to get CPU temperature
    try:
        temps = psutil.sensors_temperatures()
        if 'coretemp' in temps:
            stats["cpu_temp"] = temps['coretemp'][0].current
        elif 'cpu_thermal' in temps:
            stats["cpu_temp"] = temps['cpu_thermal'][0].current
        elif 'package id 0' in temps:
             stats["cpu_temp"] = temps['package id 0'][0].current
        else:
            # Fallback for systems without named entries
            for name, entries in temps.items():
                if entries:
                    stats["cpu_temp"] = entries[0].current
                    break
    except Exception:
        # If we can't get temp, just use 0 (don't fail)
        stats["cpu_temp"] = 0
    
    # Try to get NVIDIA GPU stats
    try:
        out = subprocess.check_output(['nvidia-smi', '--query-gpu=utilization.gpu,temperature.gpu', '--format=csv,noheader,nounits'], 
                                      universal_newlines=True)
        for i, line in enumerate(out.strip().split('\n')):
            util, temp = line.split(',')
            stats["gpus"].append({
                "id": str(i + 1),
                "util": util.strip(),
                "temp": int(temp.strip())
            })
    except Exception:
        pass
        
    return stats

def get_hashcat_devices():
    """ Returns a list of detected hashcat devices (GPUs/CPUs). """
    devices = []
    
    # 1. Primary Method: Hashcat identification
    try:
        # Try -I (info)
        out, _ = subprocess_call(['hashcat', '-I', '--force'])
        devices = parse_hashcat_devices_output(out)
    except Exception as e:
        logger.error(f"Hashcat device detection failed: {e}")

    def upsert_device(new_device):
        new_id = str(new_device.get("id"))
        new_name = str(new_device.get("name", "")).strip()
        for existing in devices:
            if str(existing.get("id")) == new_id:
                if new_device.get("is_gpu") and not existing.get("is_gpu"):
                    existing["is_gpu"] = True
                if existing.get("memory", "Unknown") in ("Unknown", "", None) and new_device.get("memory"):
                    existing["memory"] = new_device["memory"]
                if existing.get("name", "").startswith("Device ") and new_name:
                    existing["name"] = new_name
                return
        devices.append(new_device)

    # 2. Augmentation: always merge in NVIDIA GPUs when available.
    try:
        out = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=index,name,memory.total', '--format=csv,noheader'],
            universal_newlines=True,
        )
        for line in out.splitlines():
            parts = [part.strip() for part in line.split(',', 2)]
            if len(parts) != 3:
                continue
            index, name, mem = parts
            upsert_device({
                "id": index or name,
                "name": name,
                "memory": mem,
                "is_gpu": True
            })
    except Exception:
        pass

    # 3. Windows fallback: query adapters directly so the UI can still show the
    # real GPU name when hashcat device discovery is incomplete.
    if os.name == "nt" and not any(device.get("is_gpu") for device in devices):
        for device in get_windows_video_adapters():
            upsert_device(device)

    # 4. Linux fallback: enumerate PCI display adapters if no GPU was discovered.
    if not any(device.get("is_gpu") for device in devices):
        for device in get_linux_pci_gpus():
            upsert_device(device)

    # 5. Last Resort: CPU
    if not devices:
        import psutil
        devices.append({
            "id": "cpu",
            "name": "Host CPU (Fallback)",
            "memory": f"{psutil.virtual_memory().total // (1024*1024)} MB",
            "is_gpu": False
        })
    elif not any(not device.get("is_gpu") for device in devices):
        import psutil
        devices.append({
            "id": "cpu",
            "name": "Host CPU",
            "memory": f"{psutil.virtual_memory().total // (1024*1024)} MB",
            "is_gpu": False
        })

    return devices
