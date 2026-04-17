import json
from app.config import ADMIN_SETTINGS_PATH


def workload_profile_for_intensity(intensity: int) -> str:
    """
    Map the UI intensity slider to the nearest safer hashcat workload profile.

    Hashcat only exposes four coarse workload profiles, so a percentage here
    cannot be enforced as a real-time GPU utilization ceiling.
    """
    intensity = max(0, min(100, int(intensity)))
    if intensity == 0:
        return "1"
    if intensity <= 30:
        return "1"
    if intensity <= 55:
        return "2"
    if intensity <= 80:
        return "3"
    return "4"

def read_settings():
    if not ADMIN_SETTINGS_PATH.exists():
        return {
            "device_intensities": {"1": 100},
            "cpu_percent": 100,
            "gpu_temp_limit": 90,
            "cpu_temp_limit": 90,
            "temp_resume_delta": 5,
            "max_job_time_minutes": None,
            "default_devices": ["1"]
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
            
            return data
    except Exception:
        return {
            "device_intensities": {"1": 100},
            "cpu_percent": 100,
            "gpu_temp_limit": 90,
            "cpu_temp_limit": 90,
            "temp_resume_delta": 5,
            "max_job_time_minutes": None,
            "default_devices": ["1"]
        }

def write_settings(device_intensities: dict, cpu_percent: int, gpu_temp_limit: int = 90, cpu_temp_limit: int = 90,
                   temp_resume_delta: int = 5, max_job_time_minutes: int = None, default_devices: list = None):
    with open(ADMIN_SETTINGS_PATH, "w") as f:
        json.dump({
            "device_intensities": device_intensities, 
            "cpu_percent": cpu_percent,
            "gpu_temp_limit": gpu_temp_limit,
            "cpu_temp_limit": cpu_temp_limit,
            "temp_resume_delta": temp_resume_delta,
            "max_job_time_minutes": max_job_time_minutes,
            "default_devices": default_devices or ["1"]
        }, f)

def apply_hashcat_limits(hashcat_args: list):
    """ Modifies the hashcat args based on configured settings. """
    settings = read_settings()
    device_intensities = {str(k): int(v) for k, v in settings.get("device_intensities", {"1": 100}).items()}
    
    # identify enabled devices
    active_devices = [str(id) for id, val in device_intensities.items() if int(val) > 0]
    
    if active_devices:
        hashcat_args.append("-d")
        hashcat_args.append(",".join(active_devices))

        # Use the highest enabled device intensity as a coarse workload cap.
        max_val = max(device_intensities.values()) if device_intensities else 100
        wp = workload_profile_for_intensity(max_val)
        
        hashcat_args = [arg for arg in hashcat_args if not arg.startswith("--workload-profile=")]
        hashcat_args.append(f"--workload-profile={wp}")
        
    return hashcat_args
