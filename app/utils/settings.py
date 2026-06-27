import json
import platform
from app.config import ADMIN_SETTINGS_PATH
from app.domain import Workload
from app.utils.utils import get_hashcat_devices


ARM_HASHCAT_SAFE_FLAGS = [
    "-D", "1",
    "--backend-ignore-cuda",
    "--backend-ignore-hip",
    "--self-test-disable",
    "--backend-vector-width=1",
    "--workload-profile=1",
    "--kernel-accel=1",
    "--kernel-loops=1",
    "--force",
]
ARM_HASHCAT_SAFE_REMOVE_NEXT = {
    "-d",
    "--backend-devices",
    "-D",
    "--opencl-device-types",
    "--backend-vector-width",
    "-w",
}
ARM_HASHCAT_SAFE_REMOVE_EXACT = {
    "--backend-ignore-cuda",
    "--backend-ignore-hip",
    "--backend-ignore-opencl",
    "--backend-ignore-metal",
    "--self-test-disable",
    "--force",
}
ARM_HASHCAT_SAFE_REMOVE_PREFIXES = (
    "--backend-devices=",
    "--opencl-device-types=",
    "--backend-vector-width=",
    "--workload-profile=",
    "--kernel-accel=",
    "--kernel-loops=",
)


def hashcat_tuning_for_intensity(intensity: int):
    """
    Map the UI percentage to steady hashcat tuning knobs.

    Keep the GPU running continuously with a lighter workload profile instead
    of forcing backend-specific kernel parameters. Hashcat's self-test is
    sensitive to hand-picked accel/loop values on some OpenCL/CUDA stacks.
    """
    intensity = max(0, min(100, int(intensity)))
    if intensity <= 35:
        return {"workload_profile": "1"}
    if intensity <= 65:
        return {"workload_profile": "2"}
    if intensity <= 90:
        return {"workload_profile": "3"}
    return {"workload_profile": "4"}


def is_arm_host():
    machine = platform.machine().lower()
    return machine in ("aarch64", "arm64", "armv7l", "armv6l")


def strip_arm_conflicting_hashcat_args(hashcat_args: list):
    filtered_args = []
    skip_next = False
    for arg in hashcat_args:
        if skip_next:
            skip_next = False
            continue
        if arg in ARM_HASHCAT_SAFE_REMOVE_NEXT:
            skip_next = True
            continue
        if arg in ARM_HASHCAT_SAFE_REMOVE_EXACT:
            continue
        if any(arg.startswith(prefix) for prefix in ARM_HASHCAT_SAFE_REMOVE_PREFIXES):
            continue
        filtered_args.append(arg)
    return filtered_args

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
    available_device_ids = [
        str(device.get("id"))
        for device in get_hashcat_devices()
        if str(device.get("id", "")).isdigit() and device.get("hashcat_usable", True)
    ]
    
    # identify enabled devices
    active_devices = [
        device_id
        for device_id in available_device_ids
        if int(device_intensities.get(device_id, 100)) > 0
    ]
    return active_devices


def default_hashcat_device_ids(settings=None, enabled_devices=None):
    settings = settings or read_settings()
    enabled_devices = enabled_devices or enabled_hashcat_device_ids(settings)
    defaults = [str(device_id) for device_id in settings.get("default_devices", [])]
    selected = [device_id for device_id in defaults if device_id in enabled_devices]
    return selected or list(enabled_devices[:1])


def selected_hashcat_devices_include_gpu(device_ids: list):
    selected_ids = {str(device_id) for device_id in device_ids or []}
    if not selected_ids:
        return False
    try:
        return any(
            str(device.get("id")) in selected_ids and device.get("is_gpu")
            for device in get_hashcat_devices()
        )
    except Exception:
        return False


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

    active_devices_include_gpu = selected_hashcat_devices_include_gpu(active_devices)

    if active_devices:
        hashcat_args.append("-d")
        hashcat_args.append(",".join(active_devices))

        # Use the highest enabled device intensity to pick a stable hashcat
        # workload profile. Strip older forced kernel settings because they can
        # trigger hashcat self-test failures on specific driver/backend combos.
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
        hashcat_args = filtered_args

    if is_arm_host() and not active_devices_include_gpu:
        hashcat_args = strip_arm_conflicting_hashcat_args(hashcat_args)
        hashcat_args.extend(ARM_HASHCAT_SAFE_FLAGS)
        
    return hashcat_args
