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
