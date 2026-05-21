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
            "default_api_workload": Workload.Normal.value,
            "use_spare_devices_for_queue": False
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
            if "use_spare_devices_for_queue" not in data: data["use_spare_devices_for_queue"] = False
            data["default_api_workload"] = Workload.normalize(data.get("default_api_workload", Workload.Normal.value))
            
            return data
    except Exception as exc:
        from app.logger import logger
        logger.error("Failed to read settings (%s); using defaults.", exc)
        return {
            "device_intensities": {"1": 100},
            "cpu_percent": 100,
            "gpu_temp_limit": 90,
            "cpu_temp_limit": 90,
            "temp_resume_delta": 5,
            "max_job_time_minutes": None,
            "default_devices": ["1"],
            "default_api_workload": Workload.Normal.value,
            "use_spare_devices_for_queue": False
        }

def write_settings(device_intensities: dict, cpu_percent: int, gpu_temp_limit: int = 90, cpu_temp_limit: int = 90,
                   temp_resume_delta: int = 5, max_job_time_minutes: int = None, default_devices: list = None,
                   default_api_workload: str = Workload.Normal.value, use_spare_devices_for_queue: bool = False):
    existing = read_settings()
    existing.update({
        "device_intensities": device_intensities,
        "cpu_percent": cpu_percent,
        "gpu_temp_limit": gpu_temp_limit,
        "cpu_temp_limit": cpu_temp_limit,
        "temp_resume_delta": temp_resume_delta,
        "max_job_time_minutes": max_job_time_minutes,
        "default_devices": default_devices or ["1"],
        "default_api_workload": Workload.normalize(default_api_workload),
        "use_spare_devices_for_queue": bool(use_spare_devices_for_queue)
    })
    with open(ADMIN_SETTINGS_PATH, "w") as f:
        json.dump(existing, f)


def update_admin_setting(**values):
    existing = read_settings()
    existing.update(values)
    with open(ADMIN_SETTINGS_PATH, "w") as f:
        json.dump(existing, f)

def split_hashcat_device_args(hashcat_args: list):
    filtered_input_args = []
    requested_devices = []
    skip_next = False
    for arg in hashcat_args:
        if skip_next:
            requested_devices.extend(str(arg).split(","))
            skip_next = False
            continue
        if arg == "-d" or arg == "--backend-devices":
            skip_next = True
            continue
        if arg.startswith("--backend-devices="):
            requested_devices.extend(arg.split("=", 1)[1].split(","))
            continue
        filtered_input_args.append(arg)
    requested_devices = [device.strip() for device in requested_devices if device.strip().isdigit()]
    return filtered_input_args, requested_devices


def enabled_hashcat_device_ids(settings=None):
    settings = settings or read_settings()
    device_intensities = {str(k): int(v) for k, v in settings.get("device_intensities", {"1": 100}).items()}
    available_device_ids = {
        str(device.get("id"))
        for device in get_hashcat_devices()
        if str(device.get("id", "")).isdigit() and device.get("hashcat_usable", True)
    }
    
    # identify enabled devices
    active_devices = [
        str(device_id)
        for device_id, val in device_intensities.items()
        if int(val) > 0 and str(device_id) in available_device_ids
    ]
    return active_devices


def default_hashcat_device_ids(settings=None, enabled_devices=None):
    settings = settings or read_settings()
    enabled_devices = enabled_devices or enabled_hashcat_device_ids(settings)
    defaults = [str(device_id) for device_id in settings.get("default_devices", [])]
    selected = [device_id for device_id in defaults if device_id in enabled_devices]
    return selected or list(enabled_devices[:1])


def apply_hashcat_limits(hashcat_args: list, device_ids: list = None):
    """Modifies hashcat args based on configured settings and selected devices."""
    settings = read_settings()
    hashcat_args, requested_devices = split_hashcat_device_args(hashcat_args)
    enabled_devices = enabled_hashcat_device_ids(settings)
    if device_ids is not None:
        active_devices = [str(device_id) for device_id in device_ids if str(device_id) in enabled_devices]
    elif requested_devices:
        active_devices = [device_id for device_id in requested_devices if device_id in enabled_devices]
    else:
        active_devices = enabled_devices

    if active_devices:
        hashcat_args.append("-d")
        hashcat_args.append(",".join(active_devices))

        # Use the highest enabled device intensity to pick a stable hashcat
        # tuning profile rather than pause/resume throttling.
        device_intensities = {str(k): int(v) for k, v in settings.get("device_intensities", {"1": 100}).items()}
        max_val = max((device_intensities.get(device_id, 100) for device_id in active_devices), default=100)
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
