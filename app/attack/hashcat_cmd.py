import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Union, List

from app.config import HASHCAT_STATUS_TIMER
from app.domain import Rule, WordList, ProgressLock, TaskInfoStatus, Mask, HashcatMode

HASHCAT_WARNINGS = (
    "nvmlDeviceGetCurrPcieLinkWidth",
    "nvmlDeviceGetClockInfo",
    "nvmlDeviceGetTemperatureThreshold",
    "nvmlDeviceGetUtilizationRates",
    "nvmlDeviceGetPowerManagementLimit",
    "nvmlDeviceGetUtilizationRates",
)


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
            # masks are not compatible with wordlists
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
            # localhost debug mode
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
    hashcat_cmd_list = hashcat_cmd.build()
    process = subprocess.Popen(hashcat_cmd_list,
                               universal_newlines=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    last_temp_check = 0
    from app.utils.settings import read_settings
    from app.utils.utils import get_live_usage
    
    stderr_buffer = []
    
    # Read stdout for progress updates
    for line in iter(process.stdout.readline, ''):
        current_time = time.time()
        # Temperature & Cancellation Check
        if current_time - last_temp_check > 5:
            last_temp_check = current_time
            settings = read_settings()
            cpu_limit = settings.get("cpu_temp_limit", 90)
            usage = get_live_usage()
            if usage['cpu_temp'] > cpu_limit:
                process.terminate()
                raise RuntimeError(f"CPU Overheat: {usage['cpu_temp']}°C (Limit: {cpu_limit}°C)")

        with lock:
            if lock.cancelled:
                process.terminate()
                raise InterruptedError(TaskInfoStatus.CANCELLED)
        time_spent = current_time - start
        if time_spent > timeout_seconds:
            process.terminate()
            raise TimeoutError(f"Timed out after {timeout_minutes} minutes")
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
    
    # Process has finished, wait and check stderr
    _, stderr = process.communicate()
    if stderr:
        # Filter out common warnings we don't care about
        warn, err = split_warnings_errors(stderr)
        if err.strip():
            logger.error(f"Hashcat error detected: {err.strip()}")
            # If it failed immediately, the status should reflect the error
            if time.time() - start < 2:
                raise RuntimeError(f"Hashcat failed to start: {err.splitlines()[0]}")

    if process.returncode != 0:
        # If it's not a success and it's not 'already cracked' (3 or 4)
        if process.returncode not in (0, 1):
             raise RuntimeError(f"Hashcat exited with code {process.returncode}")
