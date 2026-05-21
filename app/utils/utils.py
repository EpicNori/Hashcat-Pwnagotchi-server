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

    if any(marker in normalized_name for marker in CPU_NAME_MARKERS):
        return False
    if "cpu" in normalized_type:
        return False
    if "gpu" in normalized_type:
        return True

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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
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
                "is_gpu": True,
                "hashcat_usable": False,
            })
        return devices
    except Exception as error:
        logger.error(f"Linux PCI GPU detection failed: {error}")
        return []


def subprocess_call(args: List[str], cwd=None):
    """
    :param args: shell args
    """
    args = list(map(str, args))
    process_cwd = cwd
    if args:
        executable = Path(args[0]).name.lower()
        if executable == "hashcat":
            resolved_hashcat = resolve_hashcat_executable()
            if resolved_hashcat:
                args[0] = resolved_hashcat
                process_cwd = str(Path(resolved_hashcat).parent)
    logger.debug(">>> {}".format(' '.join(args)))
    if not all(args):
        raise ValueError(f"Empty arg in {args}")
    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=process_cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
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
        candidates = [
            install_root_path / "tools" / "hashcat" / "hashcat",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

    env_override = os.environ.get("HASHCAT_EXECUTABLE")
    if env_override:
        override_path = Path(env_override)
        if override_path.exists():
            return str(override_path)

    resolved = which("hashcat")
    if resolved:
        return resolved

    return None


def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


def date_formatted() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def hashcat_devices_info():
    try:
        from markupsafe import escape
        hashcat_devices, _ = subprocess_call(['hashcat', '-I', '--force'])
        # escape() prevents XSS from unexpected chars in device names / hashcat output
        hashcat_devices = f"<code>$ hashcat -I --force</code>\n<samp>{escape(hashcat_devices)}</samp>"
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
                                      text=True, encoding="utf-8", errors="replace")
        for line in out.strip().split('\n'):
            # Use maxsplit=1 to handle GPU names that contain commas
            parts = line.split(',', 1)
            if len(parts) != 2:
                continue
            util, temp = parts
            try:
                stats["gpus"].append({
                    "id": str(i + 1),
                    "util": util.strip(),
                    "temp": int(temp.strip())
                })
            except ValueError:
                logger.warning("Could not parse nvidia-smi temp on line: %r", line)
                continue
    except Exception:
        pass
        
    return stats

@lru_cache(maxsize=1)
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
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in out.splitlines():
            parts = [part.strip() for part in line.split(',', 2)]
            if len(parts) != 3:
                continue
            index, name, mem = parts
            # Only use numeric device IDs to prevent command injection
            if not str(index).strip().isdigit():
                logger.warning("Skipping non-numeric nvidia-smi device index: %r", index)
                continue
            upsert_device({
                "id": index,
                "name": name,
                "memory": mem,
                "is_gpu": True
            })
    except Exception:
        pass

    # 3. Linux fallback: enumerate PCI display adapters if no GPU was discovered.
    if not any(device.get("is_gpu") for device in devices):
        for device in get_linux_pci_gpus():
            upsert_device(device)

    # 4. Last Resort: CPU
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

    normalized_devices = []
    for device in devices:
        name = str(device.get("name", "")).strip()
        device_type = str(device.get("device_type", ""))
        device["is_gpu"] = infer_device_is_gpu(name, device_type)
        device.setdefault("hashcat_usable", True)
        normalized_devices.append(device)

    gpu_devices = [device for device in normalized_devices if device.get("is_gpu")]
    cpu_devices = [device for device in normalized_devices if not device.get("is_gpu")]

    if len(cpu_devices) > 1:
        preferred_cpu = next(
            (
                device
                for device in cpu_devices
                if "host cpu" in str(device.get("name", "")).lower()
                or "cpu" in str(device.get("name", "")).lower()
            ),
            cpu_devices[0],
        )
        cpu_devices = [preferred_cpu]

    return gpu_devices + cpu_devices
