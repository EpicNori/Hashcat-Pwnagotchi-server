import json
from app.config import ADMIN_SETTINGS_PATH
from app.domain import Workload
from app.utils.utils import get_hashcat_devices


def hashcat_tuning_for_intensity(intensity: int):
    """
    Map the UI percentage to steady hashcat tuning knobs.

    This keeps the GPU running continuously with a lighter kernel configuration
    instead of pulse-throttling the whole process on and off.
    """
    intensity = max(0, min(100, int(intensity)))
    if intensity == 0:
        return {"workload_profile": "1", "kernel_accel": 8, "kernel_loops": 64}
    if intensity <= 20:
        return {"workload_profile": "1", "kernel_accel": 8, "kernel_loops": 64}
    if intensity <= 35:
        return {"workload_profile": "1", "kernel_accel": 16, "kernel_loops": 128}
    if intensity <= 50:
        return {"workload_profile": "2", "kernel_accel": 24, "kernel_loops": 128}
    if intensity <= 65:
        return {"workload_profile": "2", "kernel_accel": 32, "kernel_loops": 256}
    if intensity <= 80:
        return {"workload_profile": "3", "kernel_accel": 48, "kernel_loops": 256}
    if intensity <= 90:
        return {"workload_profile": "3", "kernel_accel": 64, "kernel_loops": 512}
    return {"workload_profile": "4", "kernel_accel": 96, "kernel_loops": 1024}

def read_settings():
    if not ADMIN_SETTINGS_PATH.exists():
        return {
            "device_intensities": {"1": 100},
            "cpu_percent": 100,
            "gpu_temp_limit": 90,
            "cpu_temp_limit": 90,
            "temp_resume_delta": 5,
            "max_job_time_minutes": None,
            "default_devices": ["1"],
            "default_api_workload": Workload.Normal.value
        }
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
            if "temp_resume_delta" not in data: data["temp_resume_delta"] = 5
            if "max_job_time_minutes" not in data: data["max_job_time_minutes"] = None
            if "default_devices" not in data: data["default_devices"] = ["1"]
            data["default_api_workload"] = Workload.normalize(data.get("default_api_workload", Workload.Normal.value))
            
            return data
    except Exception:
        return {
            "device_intensities": {"1": 100},
            "cpu_percent": 100,
            "gpu_temp_limit": 90,
            "cpu_temp_limit": 90,
            "temp_resume_delta": 5,
            "max_job_time_minutes": None,
            "default_devices": ["1"],
            "default_api_workload": Workload.Normal.value
        }

def write_settings(device_intensities: dict, cpu_percent: int, gpu_temp_limit: int = 90, cpu_temp_limit: int = 90,
                   temp_resume_delta: int = 5, max_job_time_minutes: int = None, default_devices: list = None,
                   default_api_workload: str = Workload.Normal.value):
    with open(ADMIN_SETTINGS_PATH, "w") as f:
        json.dump({
            "device_intensities": device_intensities, 
            "cpu_percent": cpu_percent,
            "gpu_temp_limit": gpu_temp_limit,
            "cpu_temp_limit": cpu_temp_limit,
            "temp_resume_delta": temp_resume_delta,
            "max_job_time_minutes": max_job_time_minutes,
            "default_devices": default_devices or ["1"],
            "default_api_workload": Workload.normalize(default_api_workload)
        }, f)

def apply_hashcat_limits(hashcat_args: list):
    """ Modifies the hashcat args based on configured settings. """
    settings = read_settings()
    device_intensities = {str(k): int(v) for k, v in settings.get("device_intensities", {"1": 100}).items()}
    available_device_ids = {
        str(device.get("id"))
        for device in get_hashcat_devices()
        if str(device.get("id", "")).isdigit()
    }
    
    # identify enabled devices
    active_devices = [
        str(device_id)
        for device_id, val in device_intensities.items()
        if int(val) > 0 and (not available_device_ids or str(device_id) in available_device_ids)
    ]
    
    if active_devices:
        hashcat_args.append("-d")
        hashcat_args.append(",".join(active_devices))

        # Use the highest enabled device intensity to pick a stable hashcat
        # tuning profile rather than pause/resume throttling.
        max_val = max(device_intensities.values()) if device_intensities else 100
        tuning = hashcat_tuning_for_intensity(max_val)

        filtered_args = []
        skip_next = False
        for arg in hashcat_args:
            if skip_next:
                skip_next = False
                continue
            if arg in ("-n", "-u"):
                skip_next = True
                continue
            if arg.startswith("--workload-profile=") or arg.startswith("--kernel-accel=") or arg.startswith("--kernel-loops="):
                continue
            filtered_args.append(arg)

        filtered_args.append(f"--workload-profile={tuning['workload_profile']}")
        filtered_args.append(f"--kernel-accel={tuning['kernel_accel']}")
        filtered_args.append(f"--kernel-loops={tuning['kernel_loops']}")
        hashcat_args = filtered_args
        
    return hashcat_args
