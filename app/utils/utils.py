import datetime
import re
import subprocess
from functools import lru_cache
from typing import List
from urllib.parse import urlparse, urljoin

from flask import request, Markup

from app.logger import logger


def parse_hashcat_devices_output(output: str):
    devices = []

    # Older hashcat format:
    # Device #1: NVIDIA GeForce RTX 3080, 10240/10240 MB, 68MCU
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("Device #"):
            continue
        try:
            id_part, rest = line.split(':', 1)
            dev_id = id_part.replace("Device #", "").strip()
            info_parts = [part.strip() for part in rest.split(',')]
            name = info_parts[0] if info_parts else f"Device {dev_id}"
            memory = next((part for part in info_parts[1:] if "MB" in part or "GB" in part), "Unknown")
            devices.append({
                "id": dev_id,
                "name": name,
                "memory": memory,
                "is_gpu": any(x in name.lower() for x in ["nvidia", "amd", "radeon", "graphics", "gpu", "intel arc"])
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
            if any(x in value.lower() for x in ["nvidia", "amd", "radeon", "graphics", "gpu", "intel arc"]):
                current["is_gpu"] = True
        elif normalized_key in ("device type", "type"):
            if "gpu" in value.lower():
                current["is_gpu"] = True
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


def subprocess_call(args: List[str]):
    """
    :param args: shell args
    """
    args = list(map(str, args))
    logger.debug(">>> {}".format(' '.join(args)))
    if not all(args):
        raise ValueError(f"Empty arg in {args}")
    try:
        completed = subprocess.run(args, universal_newlines=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as e:
        executable = args[0] if args else "unknown"
        raise FileNotFoundError(f"Tool not found: '{executable}'. Please ensure it is installed and in your PATH.") from e
        
    if completed.stderr or completed.returncode != 0:
        logger.debug(completed.stdout)
        logger.error(completed.stderr)
    return completed.stdout, completed.stderr


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

    # 2. Augmentation: If no devices found, try to at least get NVIDIA GPUs via nvidia-smi
    if not devices:
        try:
            out = subprocess.check_output(['nvidia-smi', '--query-gpu=gpu_name,memory.total', '--format=csv,noheader'], 
                                          universal_newlines=True)
            for i, line in enumerate(out.strip().split('\n')):
                name, mem = line.split(',')
                devices.append({
                    "id": str(i + 1),
                    "name": name.strip(),
                    "memory": mem.strip(),
                    "is_gpu": True
                })
        except Exception:
            pass

    # 3. Linux fallback: enumerate PCI display adapters even if hashcat parsing fails
    if not devices:
        devices = get_linux_pci_gpus()

    # 4. Last Resort: CPU
    if not devices:
        import psutil
        devices.append({
            "id": "1",
            "name": "Host CPU (Fallback)",
            "memory": f"{psutil.virtual_memory().total // (1024*1024)} MB",
            "is_gpu": False
        })

    return devices
