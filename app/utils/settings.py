import json
from app.config import ADMIN_SETTINGS_PATH

def read_settings():
    if not ADMIN_SETTINGS_PATH.exists():
        return {"device_intensities": {"1": 100}, "cpu_percent": 100, "gpu_temp_limit": 90, "cpu_temp_limit": 90, "default_devices": ["1"]}
    try:
        with open(ADMIN_SETTINGS_PATH, "r") as f:
            data = json.load(f)
            # Migration logic
            if "gpu1_percent" in data:
                data["device_intensities"] = {"1": data.pop("gpu1_percent"), "2": data.pop("gpu2_percent", 100)}
            elif "gpu_percent" in data:
                 data["device_intensities"] = {"1": data.pop("gpu_percent")}
            
            # Defaults for new fields
            if "gpu_temp_limit" not in data: data["gpu_temp_limit"] = 90
            if "cpu_temp_limit" not in data: data["cpu_temp_limit"] = 90
            if "default_devices" not in data: data["default_devices"] = ["1"]
            
            return data
    except Exception:
        return {"device_intensities": {"1": 100}, "cpu_percent": 100, "gpu_temp_limit": 90, "cpu_temp_limit": 90, "default_devices": ["1"]}

def write_settings(device_intensities: dict, cpu_percent: int, gpu_temp_limit: int = 90, cpu_temp_limit: int = 90, default_devices: list = None):
    with open(ADMIN_SETTINGS_PATH, "w") as f:
        json.dump({
            "device_intensities": device_intensities, 
            "cpu_percent": cpu_percent,
            "gpu_temp_limit": gpu_temp_limit,
            "cpu_temp_limit": cpu_temp_limit,
            "default_devices": default_devices or ["1"]
        }, f)

def apply_hashcat_limits(hashcat_args: list):
    """ Modifies the hashcat args based on configured settings. """
    settings = read_settings()
    device_intensities = settings.get("device_intensities", {"1": 100})
    
    # identify enabled devices
    active_devices = [str(id) for id, val in device_intensities.items() if int(val) > 0]
    
    if active_devices:
        hashcat_args.append("-d")
        hashcat_args.append(",".join(active_devices))

        # Map intensities to workload profiles (1-4)
        # ... (rest of the logic)
        max_val = max(device_intensities.values()) if device_intensities else 100
        if max_val <= 25: wp = "1"
        elif max_val <= 50: wp = "2"
        elif max_val <= 75: wp = "3"
        else: wp = "4"
        
        hashcat_args = [arg for arg in hashcat_args if not arg.startswith("--workload-profile=")]
        hashcat_args.append(f"--workload-profile={wp}")
        
        # Throttling Logic for all devices
        avg_intensity = sum(device_intensities.values()) / len(device_intensities)
        if avg_intensity < 100:
            throttle = int(Math.pow(100 - avg_intensity, 2) * 5) 
            hashcat_args.append(f"--backend-throttle={throttle}")

    # Safety: Hard temperature kill-switch
    gpu_limit = settings.get("gpu_temp_limit", 90)
    hashcat_args.append(f"--gpu-temp-abort={gpu_limit}")
        
    return hashcat_args

import math as Math
