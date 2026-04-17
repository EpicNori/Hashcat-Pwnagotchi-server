import os
import select
import shlex
import signal
import subprocess
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import List, Union

from app.config import HASHCAT_STATUS_TIMER
from app.domain import HashcatMode, Mask, ProgressLock, Rule, TaskInfoStatus, WordList
from app.logger import logger

HASHCAT_WARNINGS = (
    "nvmlDeviceGetCurrPcieLinkWidth",
    "nvmlDeviceGetClockInfo",
    "nvmlDeviceGetTemperatureThreshold",
    "nvmlDeviceGetUtilizationRates",
    "nvmlDeviceGetPowerManagementLimit",
    "nvmlDeviceGetUtilizationRates",
)

CONTROL_LOOP_INTERVAL = 1.0
GPU_UTIL_HISTORY_SIZE = 6
GPU_UTIL_TOLERANCE = 2
GPU_THROTTLE_COOLDOWN = 1.25
TRANSIENT_STATUS_PREFIXES = (
    "Paused for CPU cooldown",
    "Paused for GPU cooldown",
    "Paused for CPU usage cap",
    "Resumed after limit cooldown",
    "Throttling GPU #",
)


def is_transient_status(status: str) -> bool:
    return any(status.startswith(prefix) for prefix in TRANSIENT_STATUS_PREFIXES)


def split_warnings_errors(stderr: str):

    def is_warning(line: str):
        for warn_pattern in HASHCAT_WARNINGS:
            if warn_pattern in line:
                return True
        return False

    warn = []
    err = []
    for line in stderr.splitlines():
        if line == '':
            continue
        if is_warning(line):
            warn.append(line)
        else:
            err.append(line)
    warn = '\n'.join(warn)
    err = '\n'.join(err)
    return warn, err


class HashcatCmd:
    def __init__(self, outfile: Union[str, Path], mode='22000', hashcat_args=(), session=None):
        self.outfile = str(outfile)
        self.mode = mode
        self.session = session
        self.rules = []
        self.wordlists = []
        self.mask = None
        self.hashcat_args = hashcat_args

    def build(self) -> List[str]:
        command = ["hashcat", f"-m{self.mode}", *self.hashcat_args]
        for rule in self.rules:
            if rule is not None:
                rule_path = str(rule.path)
                command.append("--rules={}".format(shlex.quote(rule_path)))
        command.append("--outfile={}".format(shlex.quote(self.outfile)))
        if self.session is not None:
            command.append("--session={}".format(shlex.quote(self.session)))
        self._populate_class_specific(command)
        if self.mask is not None:
            command.extend(['-a3', self.mask])
        else:
            for word_list in self.wordlists:
                command.append(shlex.quote(word_list))
        command.append("--force")
        return command

    def add_rule(self, rule: Rule):
        self.rules.append(rule)

    def add_wordlists(self, *wordlists: Union[WordList, str, Path], options: List[str] = ()):
        wordlists_new = list(options)
        for wlist in wordlists:
            if isinstance(wlist, WordList):
                wlist = wlist.path
            wordlists_new.append(str(wlist))
        self.wordlists.extend(wordlists_new)

    def set_mask(self, mask: Mask):
        self.mask = str(mask.path)

    def _populate_class_specific(self, command: List[str]):
        pass


class HashcatCmdCapture(HashcatCmd):
    def __init__(self, hcap_file: Union[str, Path], outfile: Union[str, Path], hashcat_args=(), session=None):
        mode = HashcatMode.from_suffix(Path(hcap_file).suffix)
        super().__init__(outfile=outfile, mode=mode, hashcat_args=hashcat_args, session=session)
        self.hcap_file = str(hcap_file)

    def _populate_class_specific(self, command: List[str]):
        if int(os.getenv('POTFILE_DISABLE', 0)):
            command.append("--potfile-disable")
        command.append("--status")
        command.append("--status-timer={}".format(HASHCAT_STATUS_TIMER))
        command.append("--machine-readable")
        command.append(self.hcap_file)


class HashcatCmdStdout(HashcatCmd):
    def _populate_class_specific(self, command: List[str]):
        command.append('--stdout')


def run_with_status(hashcat_cmd: HashcatCmdCapture, lock: ProgressLock, timeout_minutes=None):
    if timeout_minutes is None:
        timeout_minutes = float('inf')
    timeout_seconds = timeout_minutes * 60
    start = time.time()
    from app.utils.settings import read_settings
    from app.utils.utils import get_live_usage, apply_nvidia_power_caps, restore_nvidia_power_caps
    settings = read_settings()
    device_intensities = {str(k): int(v) for k, v in settings.get("device_intensities", {"1": 100}).items()}
    hardware_power_caps = apply_nvidia_power_caps(device_intensities)
    hashcat_cmd_list = hashcat_cmd.build()
    process = subprocess.Popen(
        hashcat_cmd_list,
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    last_temp_check = 0
    is_paused_for_temp = False
    paused_status_context = None
    base_status_context = TaskInfoStatus.RUNNING
    gpu_util_history = defaultdict(lambda: deque(maxlen=GPU_UTIL_HISTORY_SIZE))
    last_throttle_at = 0.0

    try:
        while True:
            current_time = time.time()

            with lock:
                current_status = lock.status
            if current_status and not is_transient_status(current_status):
                base_status_context = current_status

            if current_time - last_temp_check >= CONTROL_LOOP_INTERVAL:
                last_temp_check = current_time
                settings = read_settings()
                cpu_limit = settings.get("cpu_temp_limit", 90)
                gpu_limit = settings.get("gpu_temp_limit", 90)
                temp_resume_delta = settings.get("temp_resume_delta", 5)
                cpu_usage_limit = settings.get("cpu_percent", 100)
                device_intensities = {str(k): int(v) for k, v in settings.get("device_intensities", {"1": 100}).items()}
                usage = get_live_usage()

                cpu_over_limit = usage['cpu_temp'] > cpu_limit
                hottest_gpu = None
                for gpu in usage.get('gpus', []):
                    gpu_temp = int(gpu.get('temp', 0))
                    if gpu_temp > gpu_limit and (hottest_gpu is None or gpu_temp > int(hottest_gpu.get('temp', 0))):
                        hottest_gpu = gpu

                cpu_usage_over_limit = usage.get('cpu_usage', 0) > cpu_usage_limit
                pause_message = None
                throttle_gpu = None
                throttle_duration = 0.0
                if cpu_over_limit:
                    pause_message = f"Paused for CPU cooldown: {usage['cpu_temp']} C / {cpu_limit} C"
                elif hottest_gpu is not None:
                    pause_message = f"Paused for GPU cooldown: GPU #{hottest_gpu.get('id')} {hottest_gpu.get('temp')} C / {gpu_limit} C"
                elif cpu_usage_over_limit:
                    pause_message = f"Paused for CPU usage cap: {usage.get('cpu_usage', 0)}% / {cpu_usage_limit}%"
                else:
                    for gpu in usage.get('gpus', []):
                        gpu_id = str(gpu.get('id'))
                        gpu_limit_target = device_intensities.get(gpu_id, 100)
                        if gpu_limit_target >= 100 or gpu_id in hardware_power_caps:
                            continue

                        gpu_util = int(gpu.get('util', 0))
                        history = gpu_util_history[gpu_id]
                        history.append(gpu_util)
                        avg_util = sum(history) / len(history)
                        excess = avg_util - gpu_limit_target
                        if excess <= GPU_UTIL_TOLERANCE:
                            continue

                        candidate = dict(gpu)
                        candidate["avg_util"] = avg_util
                        candidate["target_util"] = gpu_limit_target
                        candidate["excess"] = excess
                        if throttle_gpu is None or candidate["excess"] > throttle_gpu["excess"]:
                            throttle_gpu = candidate

                    if throttle_gpu is not None and current_time - last_throttle_at >= GPU_THROTTLE_COOLDOWN:
                        # Keep software throttling only as a fallback for GPUs where
                        # we could not apply a hardware power cap.
                        throttle_duration = min(0.9, max(0.08, throttle_gpu["excess"] / 40.0))

                cpu_resume_limit = max(0, cpu_limit - temp_resume_delta)
                gpu_resume_limit = max(0, gpu_limit - temp_resume_delta)
                cpu_usage_resume_limit = max(0, cpu_usage_limit - 5)
                cpu_temp_safe = usage['cpu_temp'] <= cpu_resume_limit
                gpu_temp_safe = all(int(gpu.get('temp', 0)) <= gpu_resume_limit for gpu in usage.get('gpus', []))
                cpu_usage_safe = usage.get('cpu_usage', 0) <= cpu_usage_resume_limit

                if pause_message is not None:
                    if not is_paused_for_temp:
                        paused_status_context = base_status_context
                        process.send_signal(signal.SIGSTOP)
                        is_paused_for_temp = True
                    with lock:
                        lock.set_status(pause_message)
                elif is_paused_for_temp and cpu_temp_safe and gpu_temp_safe and cpu_usage_safe:
                    process.send_signal(signal.SIGCONT)
                    is_paused_for_temp = False
                    with lock:
                        lock.set_status(paused_status_context or base_status_context or TaskInfoStatus.RUNNING)
                    paused_status_context = None
                elif throttle_duration > 0 and throttle_gpu is not None:
                    throttle_context = base_status_context
                    with lock:
                        lock.set_status(
                            f"Throttling GPU #{throttle_gpu['id']} to stay near {throttle_gpu['target_util']}% "
                            f"(avg {throttle_gpu['avg_util']:.0f}%)"
                        )
                    process.send_signal(signal.SIGSTOP)
                    time.sleep(throttle_duration)
                    process.send_signal(signal.SIGCONT)
                    last_throttle_at = time.time()
                    with lock:
                        lock.set_status(throttle_context or TaskInfoStatus.RUNNING)

            with lock:
                if lock.cancelled:
                    if is_paused_for_temp:
                        process.send_signal(signal.SIGCONT)
                    process.terminate()
                    raise InterruptedError(TaskInfoStatus.CANCELLED)

            time_spent = current_time - start
            if time_spent > timeout_seconds:
                if is_paused_for_temp:
                    process.send_signal(signal.SIGCONT)
                process.terminate()
                raise TimeoutError(f"Timed out after {timeout_minutes} minutes")

            if is_paused_for_temp:
                time.sleep(0.25)
                continue

            ready, _, _ = select.select([process.stdout], [], [], CONTROL_LOOP_INTERVAL)
            if not ready:
                if process.poll() is not None:
                    break
                continue

            line = process.stdout.readline()
            if line == '':
                if process.poll() is not None:
                    break
                break
            if line.startswith("STATUS"):
                parts = line.split()
                try:
                    progress_index = parts.index("PROGRESS")
                    tried_keys = parts[progress_index + 1]
                    total_keys = parts[progress_index + 2]
                    progress = 100. * int(tried_keys) / int(total_keys)

                    speed_str = "0 H/s"
                    if "SPEED" in parts:
                        speed_index = parts.index("SPEED")
                        speed_val = int(parts[speed_index + 1])
                        if speed_val >= 1000000:
                            speed_str = f"{speed_val / 1000000:.1f} MH/s"
                        elif speed_val >= 1000:
                            speed_str = f"{speed_val / 1000:.1f} kH/s"
                        else:
                            speed_str = f"{speed_val} H/s"

                    with lock:
                        lock.progress = progress
                        lock.speed = speed_str
                except (ValueError, IndexError):
                    pass

        _, stderr = process.communicate()
        if stderr:
            warn, err = split_warnings_errors(stderr)
            if err.strip():
                logger.error(f"Hashcat error detected: {err.strip()}")
                if time.time() - start < 2:
                    raise RuntimeError(f"Hashcat failed to start: {err.splitlines()[0]}")

        if process.returncode != 0:
            if process.returncode not in (0, 1):
                raise RuntimeError(f"Hashcat exited with code {process.returncode}")
    finally:
        restore_nvidia_power_caps(hardware_power_caps)
