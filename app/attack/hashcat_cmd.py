import os
import queue
import signal
import subprocess
import threading
import time
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
TRANSIENT_STATUS_PREFIXES = (
    "Paused for CPU cooldown",
    "Paused for GPU cooldown",
    "Paused for CPU usage cap",
    "Resumed after limit cooldown",
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
                command.append(f"--rules={rule.path}")
        command.append(f"--outfile={self.outfile}")
        if self.session is not None:
            command.append(f"--session={self.session}")
        self._populate_class_specific(command)
        if self.mask is not None:
            command.extend(['-a3', self.mask])
        else:
            for word_list in self.wordlists:
                command.append(word_list)
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


def _stream_reader(pipe, output_queue: queue.Queue, stream_name: str):
    try:
        for line in iter(pipe.readline, ''):
            output_queue.put((stream_name, line))
    finally:
        try:
            pipe.close()
        except Exception:
            pass
        output_queue.put((stream_name, None))


def run_with_status(hashcat_cmd: HashcatCmdCapture, lock: ProgressLock, timeout_minutes=None):
    if timeout_minutes is None:
        timeout_minutes = float('inf')
    timeout_seconds = timeout_minutes * 60
    start = time.time()
    from app.utils.settings import read_settings
    from app.utils.utils import get_live_usage
    hashcat_cmd_list = hashcat_cmd.build()
    process = subprocess.Popen(
        hashcat_cmd_list,
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1
    )
    output_queue = queue.Queue()
    stdout_done = False
    stderr_done = False
    stderr_lines = []
    stdout_thread = threading.Thread(
        target=_stream_reader,
        args=(process.stdout, output_queue, "stdout"),
        daemon=True
    )
    stderr_thread = threading.Thread(
        target=_stream_reader,
        args=(process.stderr, output_queue, "stderr"),
        daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    last_temp_check = 0
    is_paused_for_temp = False
    paused_status_context = None
    base_status_context = TaskInfoStatus.RUNNING
    supports_suspend = os.name != "nt" and hasattr(signal, "SIGSTOP") and hasattr(signal, "SIGCONT")

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
            usage = get_live_usage()

            cpu_over_limit = usage['cpu_temp'] > cpu_limit
            hottest_gpu = None
            for gpu in usage.get('gpus', []):
                gpu_temp = int(gpu.get('temp', 0))
                if gpu_temp > gpu_limit and (hottest_gpu is None or gpu_temp > int(hottest_gpu.get('temp', 0))):
                    hottest_gpu = gpu

            cpu_usage_over_limit = usage.get('cpu_usage', 0) > cpu_usage_limit
            pause_message = None
            if cpu_over_limit:
                pause_message = f"Paused for CPU cooldown: {usage['cpu_temp']} C / {cpu_limit} C"
            elif hottest_gpu is not None:
                pause_message = f"Paused for GPU cooldown: GPU #{hottest_gpu.get('id')} {hottest_gpu.get('temp')} C / {gpu_limit} C"
            elif cpu_usage_over_limit:
                pause_message = f"Paused for CPU usage cap: {usage.get('cpu_usage', 0)}% / {cpu_usage_limit}%"

            cpu_resume_limit = max(0, cpu_limit - temp_resume_delta)
            gpu_resume_limit = max(0, gpu_limit - temp_resume_delta)
            cpu_usage_resume_limit = max(0, cpu_usage_limit - 5)
            cpu_temp_safe = usage['cpu_temp'] <= cpu_resume_limit
            gpu_temp_safe = all(int(gpu.get('temp', 0)) <= gpu_resume_limit for gpu in usage.get('gpus', []))
            cpu_usage_safe = usage.get('cpu_usage', 0) <= cpu_usage_resume_limit

            if pause_message is not None:
                if not supports_suspend:
                    process.terminate()
                    raise RuntimeError(
                        f"{pause_message}. Automatic pause/resume is unavailable on Windows, so the job was stopped to protect hardware."
                    )
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

        try:
            stream_name, line = output_queue.get(timeout=CONTROL_LOOP_INTERVAL)
        except queue.Empty:
            if process.poll() is not None and stdout_done and stderr_done:
                break
            continue

        if line is None:
            if stream_name == "stdout":
                stdout_done = True
            else:
                stderr_done = True
            if process.poll() is not None and stdout_done and stderr_done:
                break
            continue

        if stream_name == "stderr":
            stderr_lines.append(line)
            continue

        if not line.startswith("STATUS"):
            continue

        parts = line.split()
        try:
            progress_index = parts.index("PROGRESS")
            tried_keys = int(parts[progress_index + 1])
            total_keys = int(parts[progress_index + 2])
            if total_keys > 0:
                progress = 100. * tried_keys / total_keys
            else:
                progress = 0.0

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
        except (ValueError, IndexError, ZeroDivisionError):
            pass

    process.wait()
    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    stderr = ''.join(stderr_lines)
    if stderr:
        warn, err = split_warnings_errors(stderr)
        if err.strip():
            logger.error(f"Hashcat error detected: {err.strip()}")
            if time.time() - start < 2:
                raise RuntimeError(f"Hashcat failed to start: {err.splitlines()[0]}")

    if process.returncode != 0:
        if process.returncode not in (0, 1):
            raise RuntimeError(f"Hashcat exited with code {process.returncode}")
