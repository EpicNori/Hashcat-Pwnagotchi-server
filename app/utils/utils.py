import datetime
import subprocess
from functools import lru_cache
from typing import List
from urllib.parse import urlparse, urljoin

from flask import request, Markup

from app.logger import logger


def subprocess_call(args: List[str]):
    """
    :param args: shell args
    """
    args = list(map(str, args))
    logger.debug(">>> {}".format(' '.join(args)))
    if not all(args):
        raise ValueError(f"Empty arg in {args}")
    completed = subprocess.run(args, universal_newlines=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
    hashcat_devices, _ = subprocess_call(['hashcat', '-I', '--force'])
    hashcat_devices = f"<code>$ hashcat -I --force</code>\n<samp>{hashcat_devices}</samp>"
    return Markup(hashcat_devices.replace('\n', '<br>'))

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


def get_nvidia_power_limits():
    """
    Return power limit metadata keyed by 1-based GPU id to match the rest of the app.
    """
    try:
        out = subprocess.check_output(
            [
                'nvidia-smi',
                '--query-gpu=index,power.min_limit,power.max_limit,power.limit,clocks.max.graphics',
                '--format=csv,noheader,nounits'
            ],
            universal_newlines=True
        )
    except Exception:
        return {}

    limits = {}
    for line in out.strip().split('\n'):
        if not line.strip():
            continue
        try:
            index, min_limit, max_limit, current_limit, max_graphics_clock = [part.strip() for part in line.split(',')]
            app_gpu_id = str(int(index) + 1)
            limits[app_gpu_id] = {
                "nvidia_index": index,
                "min_power_limit": float(min_limit),
                "max_power_limit": float(max_limit),
                "current_power_limit": float(current_limit),
                "max_graphics_clock": float(max_graphics_clock) if max_graphics_clock not in ("N/A", "[N/A]") else None,
            }
        except Exception:
            continue
    return limits


def apply_nvidia_power_caps(device_intensities: dict):
    """
    Try to turn percentage targets into steady hardware-side power caps.

    Returns a dict keyed by app GPU id for the GPUs where a cap was successfully
    applied, including the previous power limit so it can be restored later.
    """
    power_limits = get_nvidia_power_limits()
    applied = {}

    for gpu_id, intensity in device_intensities.items():
        info = power_limits.get(str(gpu_id))
        if info is None:
            continue

        intensity = max(0, min(100, int(intensity)))
        if intensity >= 100:
            continue

        min_limit = info["min_power_limit"]
        max_limit = info["max_power_limit"]
        target_limit = min_limit + ((max_limit - min_limit) * (intensity / 100.0))
        target_limit = round(max(min_limit, min(max_limit, target_limit)))

        try:
            result = subprocess.run(
                ['nvidia-smi', '-i', info["nvidia_index"], '-pl', str(target_limit)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=False
            )
            if result.returncode == 0:
                applied[str(gpu_id)] = {
                    "nvidia_index": info["nvidia_index"],
                    "previous_power_limit": info["current_power_limit"],
                    "target_power_limit": target_limit,
                }
        except Exception:
            continue

    return applied


def restore_nvidia_power_caps(applied_caps: dict):
    for gpu_id, cap in (applied_caps or {}).items():
        previous_limit = cap.get("previous_power_limit")
        nvidia_index = cap.get("nvidia_index")
        if previous_limit is None or nvidia_index is None:
            continue
        try:
            subprocess.run(
                ['nvidia-smi', '-i', str(nvidia_index), '-pl', str(round(previous_limit))],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=False
            )
        except Exception:
            continue

def get_hashcat_devices():
    """ Returns a list of detected hashcat devices (GPUs/CPUs). """
    import re
    devices = []
    
    # 1. Primary Method: Hashcat identification
    try:
        # Try -I (info)
        out, _ = subprocess_call(['hashcat', '-I', '--force'])
        
        # Simple line-by-line parsing instead of complex regex
        for line in out.splitlines():
            line = line.strip()
            # Look for lines starting with "Device #X: "
            if line.startswith("Device #"):
                try:
                    # Format: Device #1: NVIDIA GeForce RTX 3080, 10240/10240 MB, 68MCU
                    id_part, rest = line.split(':', 1)
                    dev_id = id_part.replace("Device #", "").strip()
                    
                    # Split name and rest of info
                    info_parts = rest.split(',', 1)
                    name = info_parts[0].strip()
                    memory = info_parts[1].strip() if len(info_parts) > 1 else "Unknown"
                    
                    devices.append({
                        "id": dev_id,
                        "name": name,
                        "memory": memory.split(',')[0].strip(),
                        "is_gpu": any(x in name.lower() for x in ["nvidia", "amd", "radeon", "graphics", "gpu"])
                    })
                except Exception:
                    continue
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

    # 3. Last Resort: CPU
    if not devices:
        import psutil
        devices.append({
            "id": "1",
            "name": "Host CPU (Fallback)",
            "memory": f"{psutil.virtual_memory().total // (1024*1024)} MB",
            "is_gpu": False
        })

    return devices
