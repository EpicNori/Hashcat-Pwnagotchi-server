import concurrent.futures
import os
import re
import threading
import time
from asyncio import CancelledError
from pathlib import Path
import math

from app import app, db, lock_app
from app.attack.base_attack import BaseAttack
from app.attack.hashcat_cmd import restore_with_status, run_with_status, HashcatCmdCapture
from app.attack.recovery import clear_recovery_state, read_recovery_state
from app.config import BENCHMARK_FILE
from app.domain import Rule, TaskInfoStatus, InvalidFileError, ProgressLock, Workload, WordList
from app.logger import logger
from app.uploader import UploadForm, UploadedTask
from app.utils import read_plain_key, date_formatted, subprocess_call, read_hashcat_brain_password, \
    build_rainbow_wordlist, build_pmk_rainbow_cache, resolve_pmk_rainbow_password, bssid_essid_from_22000, decode_essid_hex
from app.word_magic.wordlist import WordListDefault, iter_user_wordlist_sources, materialize_wordlist_source


class KeyFound(Exception):
    pass


class CapAttack(BaseAttack):
    STAGE_ORDER = (
        "rainbow",
        "digits8",
        "top1k",
        "keyboard_walk",
        "essid",
        "names",
        "main_wordlist",
        "names_with_digits",
        "user_scripts",
        "default_wordlists",
        "exhaustive",
    )

    def __init__(self, file_22000, lock: ProgressLock, wordlist: Path = None, rule: Rule = None,
                 hashcat_args=(), timeout=None, work_mode=Workload.Normal.value):
        super().__init__(file_22000=file_22000,
                         hashcat_args=hashcat_args,
                         verbose=False)
        self.lock = lock
        self.timeout = timeout
        self.deadline = None if timeout is None else time.time() + (timeout * 60)
        self.wordlist = wordlist
        self.rule = rule
        self.work_mode = str(work_mode)
        self._stage = None
        # Use run_with_status for ALL sub-attacks in BaseAttack
        self.runner = self._monitored_runner

    def _monitored_runner(self, cmd: HashcatCmdCapture):
        run_with_status(
            cmd,
            lock=self.lock,
            timeout_minutes=self._remaining_timeout_minutes(),
            recovery_state=self._recovery_state(),
        )

    def _recovery_state(self):
        return {
            "stage": self._stage,
            "file_22000": str(self.file_22000),
            "wordlist": str(self.wordlist) if self.wordlist is not None else "",
            "rule": self.rule.value if self.rule is not None else "",
            "hashcat_args": list(self.hashcat_args),
            "timeout": self.timeout,
            "work_mode": self.work_mode,
        }

    def _run_stage(self, stage: str, status: str, func):
        self._stage = stage
        with self.lock:
            self.lock.set_status(status)
        result = func()
        self.read_key()
        with self.lock:
            if self.lock.found_key:
                raise KeyFound()
        return result

    def _should_run_stage(self, stage: str, start_after: str | None = None):
        if start_after is None:
            return True
        try:
            return self.STAGE_ORDER.index(stage) > self.STAGE_ORDER.index(start_after)
        except ValueError:
            return True

    def _remaining_timeout_minutes(self):
        if self.deadline is None:
            return None
        remaining_seconds = self.deadline - time.time()
        if remaining_seconds <= 0:
            raise TimeoutError("Timed out before starting the next attack step")
        return max(1, math.ceil(remaining_seconds / 60))

    def _arm_safe_mode(self):
        from app.utils.settings import is_arm_host
        return is_arm_host()

    def cancel_if_needed(self):
        with self.lock:
            if self.lock.cancelled:
                raise CancelledError(TaskInfoStatus.CANCELLED)

    def read_key(self):
        key_password = read_plain_key(self.key_file)
        if not key_password:
            key_password = self._read_key_from_hashcat_show()
        if not key_password:
            return
        with self.lock:
            self.lock.found_key = key_password
            self.lock.set_status(TaskInfoStatus.CRACKED)

    def _read_key_from_hashcat_show(self):
        # Use a clean hashcat call for --show to avoid conflicting flags.
        from app.domain import HashcatMode
        cmd = ["hashcat", "-m", HashcatMode.from_suffix(self.file_22000.suffix), 
               "--show", "--outfile", str(self.key_file), str(self.file_22000), "--force"]
        subprocess_call(cmd)
        return read_plain_key(self.key_file)

    def check_not_empty(self):
        """
        Check .hccapx file for hashes.
        """
        if not self.file_22000.exists():
            raise FileNotFoundError(f"Capture file not found: {self.file_22000}")
        file_size = self.file_22000.stat().st_size
        if file_size == 0:
            raise InvalidFileError("No hashes found")

    def run_essid_attack(self):
        """
        Run ESSID + digits_append.txt combinator attack.
        Run ESSID + best64.rule attack.
        """
        self.cancel_if_needed()
        with self.lock:
            self.lock.set_status("Running ESSID attack")
        super().run_essid_attack()

    def run_top1k(self):
        self.cancel_if_needed()
        with self.lock:
            self.lock.set_status("Running top1k with rules")
        super().run_top1k()

    def run_arm_top1k_plain(self):
        self.cancel_if_needed()
        with self.lock:
            self.lock.set_status("Running ARM-safe top1k")
        hashcat_cmd = self.new_cmd()
        hashcat_cmd.add_wordlists(WordList.TOP1K)
        self.runner(hashcat_cmd)

    def run_digits8(self):
        self.cancel_if_needed()
        with self.lock:
            self.lock.set_status("Running digits8")
        super().run_digits8()

    def run_main_wordlist(self):
        """
        Run main attack, specified by the user through the client app.
        """
        self.cancel_if_needed()
        if self.wordlist is None:
            return
        if not self.wordlist.exists():
            with self.lock:
                self.lock.set_status("Downloading the wordlist")
            while not self.wordlist.exists():
                time.sleep(5)
                self.cancel_if_needed()
        with self.lock:
            self.lock.set_status("Running the main wordlist")
        hashcat_cmd = self.new_cmd()
        resolved_wordlist = materialize_wordlist_source(self.wordlist)
        hashcat_cmd.add_wordlists(resolved_wordlist)
        hashcat_cmd.add_rule(self.rule)
        self.runner(hashcat_cmd)

    def run_default_wordlist_chain(self):
        for default_wordlist in WordListDefault.list():
            self.cancel_if_needed()
            if not default_wordlist.path.exists():
                with self.lock:
                    self.lock.set_status(f"Downloading fallback wordlist: {default_wordlist.name}")
                try:
                    default_wordlist.download()
                except Exception as error:
                    logger.warning(f"Skipping fallback wordlist {default_wordlist.name}: {error}")
                    with self.lock:
                        self.lock.set_status(f"Skipping fallback wordlist: {default_wordlist.name}")
                    continue
            with self.lock:
                self.lock.set_status(f"Running fallback wordlist: {default_wordlist.name}")
            hashcat_cmd = self.new_cmd()
            hashcat_cmd.add_wordlists(default_wordlist.path)
            self.runner(hashcat_cmd)

    def run_user_wordlist_chain(self):
        for wordlist_source in iter_user_wordlist_sources():
            self.cancel_if_needed()
            with self.lock:
                self.lock.set_status(f"Running user wordlist: {wordlist_source.name}")
            resolved_wordlist = materialize_wordlist_source(wordlist_source)
            if not resolved_wordlist.exists() or resolved_wordlist.stat().st_size == 0:
                continue
            hashcat_cmd = self.new_cmd()
            hashcat_cmd.add_wordlists(resolved_wordlist)
            self.runner(hashcat_cmd)

    def run_rainbow_attack(self):
        """
        Run an ESSID-specific WPA PMK rainbow cache.
        """
        self.cancel_if_needed()

        try:
            with app.app_context():
                rainbow_wordlist = build_rainbow_wordlist()
        except OSError as error:
            logger.warning(f"Skipping rainbow PMK cache: {error}")
            return

        if rainbow_wordlist is None or not rainbow_wordlist.exists():
            return

        try:
            bssid_essid = next(bssid_essid_from_22000(self.file_22000))
            _, essid_hex = bssid_essid.split(':', 1)
            essid = decode_essid_hex(essid_hex)
        except Exception as error:
            logger.warning(f"Skipping rainbow PMK cache; could not read ESSID: {error}")
            return

        with self.lock:
            self.lock.set_status(f"Building PMK rainbow cache for {essid}")

        pmk_wordlist, pmk_map = build_pmk_rainbow_cache(essid, rainbow_wordlist)
        if pmk_wordlist is None or pmk_map is None:
            return

        hashcat_cmd = self.new_cmd()
        hashcat_cmd.mode = "22001"
        hashcat_cmd.add_wordlists(pmk_wordlist)
        self.runner(hashcat_cmd)

        found_key = read_plain_key(self.key_file)
        password = resolve_pmk_rainbow_password(found_key, pmk_map)
        if password:
            hash_value = str(found_key).split(':', 1)[0]
            self.key_file.write_text(f"{hash_value}:{password}\n", encoding="utf-8")

    def run_all(self, start_after: str | None = None):
        """
        Run all attacks.
        """
        with lock_app:
            with app.app_context():
                task = db.session.get(UploadedTask, self.lock.task_id)
                task.status = TaskInfoStatus.RUNNING
                db.session.commit()

        if self._should_run_stage("rainbow", start_after):
            self._run_stage("rainbow", "Running rainbow reuse list...", self.run_rainbow_attack)

        if self.work_mode == Workload.Rainbow.value:
            self.read_key()
            return

        arm_safe_mode = self._arm_safe_mode()

        if arm_safe_mode:
            if self._should_run_stage("top1k", start_after):
                self._run_stage("top1k", "Running ARM-safe top1k...", self.run_arm_top1k_plain)

            if self._should_run_stage("main_wordlist", start_after):
                self._run_stage("main_wordlist", "Running the main wordlist...", self.run_main_wordlist)

            if self.work_mode == Workload.Normal.value:
                if self._should_run_stage("user_scripts", start_after):
                    self._run_stage("user_scripts", "Running CPU-safe user wordlists...", self.run_user_wordlist_chain)

                if self._should_run_stage("default_wordlists", start_after):
                    self._run_stage("default_wordlists", "Running CPU-safe fallback wordlists...", self.run_default_wordlist_chain)

            with self.lock:
                self.lock.set_status("Completed CPU-safe attack chain")
            return

        if not arm_safe_mode and self._should_run_stage("digits8", start_after):
            self._run_stage("digits8", "Running digits8...", self.run_digits8)
        
        if self._should_run_stage("top1k", start_after):
            self._run_stage("top1k", "Running top1k with rules...", self.run_top1k)
        
        if self._should_run_stage("keyboard_walk", start_after):
            self._run_stage("keyboard_walk", "Running keyboard walk...", self.run_keyboard_walk)
        
        if self._should_run_stage("essid", start_after):
            self._run_stage("essid", "Running ESSID attack...", self.run_essid_attack)
        
        if self._should_run_stage("names", start_after):
            self._run_stage("names", "Running name mutations...", self.run_names)
        
        if self._should_run_stage("main_wordlist", start_after):
            self._run_stage("main_wordlist", "Running the main wordlist...", self.run_main_wordlist)

        if self.work_mode == Workload.Normal.value:
            if self._should_run_stage("names_with_digits", start_after):
                self._run_stage("names_with_digits", "Running name mutations with digits...", self.run_names_with_digits)

            if self._should_run_stage("user_scripts", start_after):
                self._run_stage("user_scripts", "Running user wordlists...", self.run_user_wordlist_chain)

            if self._should_run_stage("default_wordlists", start_after):
                self._run_stage("default_wordlists", "Running extended default wordlists...", self.run_default_wordlist_chain)

            if self._should_run_stage("exhaustive", start_after):
                self._run_stage(
                    "exhaustive",
                    "Running exhaustive WPA brute force (8-63)...",
                    lambda: self.run_exhaustive_bruteforce(min_length=8),
                )


def _crack_async(worker, attack: CapAttack, raw_hashcat_args, requested_device_ids):
    """
    Called in background process.
    :param attack: hashcat attack to crack uploaded capture
    """
    assigned_devices = worker.claim_devices(requested_device_ids, task_id=attack.lock.task_id)
    attack.hashcat_args = tuple(worker.apply_device_limits(raw_hashcat_args, assigned_devices))
    with attack.lock:
        attack.lock.mark_started()
    attack.check_not_empty()
    try:
        attack.run_all()
        attack.read_key()
    except KeyFound:
        logger.info(f"Cracked key for {attack.file_22000}")
    logger.info(f"Finished cracking {attack.file_22000}")
    for name, timer in attack.timers.items():
        elapsed = timer['elapsed'] / timer['count']
        logger.debug(f"Timer {name}: {elapsed:.2f} sec")


def _attack_from_recovery_state(lock: ProgressLock, state: dict):
    rule = Rule.from_data(state.get("rule") or None)
    wordlist = Path(state["wordlist"]) if state.get("wordlist") else None
    return CapAttack(
        file_22000=state["file_22000"],
        lock=lock,
        wordlist=wordlist,
        rule=rule,
        hashcat_args=tuple(state.get("hashcat_args") or ()),
        timeout=state.get("timeout"),
        work_mode=state.get("work_mode", Workload.Normal.value),
    )


def _recover_async(worker, lock: ProgressLock, state: dict):
    """
    Restore the hashcat session that was running during a server crash, then continue
    the attack chain from the next stage. If hashcat has no restore data yet, rerun
    the interrupted stage from scratch.
    """
    attack = _attack_from_recovery_state(lock, state)
    assigned_devices = worker.claim_devices(worker.requested_devices_from_args(attack.hashcat_args), task_id=lock.task_id)
    attack.hashcat_args = tuple(worker.apply_device_limits(list(attack.hashcat_args), assigned_devices))
    restored_stage = state.get("stage")
    session = state.get("session")
    completed_ok = False
    with lock:
        lock.mark_started()
    with lock_app:
        with app.app_context():
            task = db.session.get(UploadedTask, lock.task_id)
            if task is not None:
                task.status = "Restoring interrupted job"
                task.completed = False
                db.session.commit()
    try:
        if session:
            with lock:
                lock.set_status(f"Restoring interrupted hashcat session: {restored_stage or session}")
            restore_with_status(session, lock=lock, timeout_minutes=attack._remaining_timeout_minutes())
            attack.read_key()
            if lock.found_key:
                logger.info(f"Recovered cracked key for {attack.file_22000}")
                completed_ok = True
                return
        try:
            attack.run_all(start_after=restored_stage)
            attack.read_key()
        except KeyFound:
            logger.info(f"Recovered cracked key for {attack.file_22000}")
        completed_ok = True
    except RuntimeError as error:
        message = str(error).lower()
        if "restore" not in message:
            raise
        logger.warning(f"Hashcat restore was unavailable for task {lock.task_id}; retrying stage {restored_stage}: {error}")
        start_after = None
        if restored_stage in CapAttack.STAGE_ORDER:
            stage_index = CapAttack.STAGE_ORDER.index(restored_stage)
            start_after = CapAttack.STAGE_ORDER[stage_index - 1] if stage_index > 0 else None
        try:
            attack.run_all(start_after=start_after)
            attack.read_key()
        except KeyFound:
            logger.info(f"Recovered cracked key for {attack.file_22000}")
        completed_ok = True
    finally:
        if completed_ok:
            clear_recovery_state(lock.task_id)


def _hashcat_benchmark_async():
    """
    Called in background process.
    """
    out, err = subprocess_call(['hashcat', '-m2500', "-b", "--machine-readable", "--quiet", "--force"])
    pattern = re.compile(r"\d+:2500:.*:.*:\d+\.\d+:\d+")
    total_speed = 0
    for line in filter(pattern.fullmatch, out.splitlines()):
        device_speed = int(line.split(':')[-1])
        total_speed += device_speed
    if total_speed > 0:
        snapshot = "{date},{speed}\n".format(date=date_formatted(), speed=total_speed)
        with lock_app, open(BENCHMARK_FILE, 'a') as f:
            f.write(snapshot)


class HashcatWorker:
    def __init__(self, app):
        """
        Called in main process.
        :param app: flask app
        """
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        self.app = app
        self.locks = {}
        self.locks_onetime = []
        self._device_condition = threading.Condition()
        self._devices_by_task = {}
        if not BENCHMARK_FILE.exists():
            self.benchmark()

    def requested_devices_from_args(self, hashcat_args):
        from app.utils.settings import split_hashcat_device_args
        _, requested_devices = split_hashcat_device_args(list(hashcat_args))
        return requested_devices

    def apply_device_limits(self, hashcat_args, device_ids):
        from app.utils.settings import apply_hashcat_limits
        return apply_hashcat_limits(list(hashcat_args), device_ids=device_ids)

    def _claimable_devices(self, requested_device_ids):
        from app.utils.settings import default_hashcat_device_ids, enabled_hashcat_device_ids, read_settings
        settings = read_settings()
        enabled_devices = enabled_hashcat_device_ids(settings)
        requested = [str(device_id) for device_id in requested_device_ids if str(device_id) in enabled_devices]
        allowed_devices = requested or enabled_devices
        default_devices = [device_id for device_id in default_hashcat_device_ids(settings, enabled_devices) if device_id in allowed_devices]
        if not default_devices and allowed_devices:
            default_devices = [allowed_devices[0]]
        spare_devices = [device_id for device_id in allowed_devices if device_id not in default_devices]
        return settings, allowed_devices, default_devices, spare_devices

    def _is_first_waiting_task(self, task_id):
        with app.app_context():
            waiting = UploadedTask.query.filter(
                UploadedTask.completed == False,
                (UploadedTask.status == TaskInfoStatus.SCHEDULED) |
                UploadedTask.status.startswith("Waiting"),
            ).order_by(
                UploadedTask.queue_position.asc(),
                UploadedTask.uploaded_time.asc(),
                UploadedTask.id.asc(),
            ).first()
            return waiting is None or waiting.id == task_id

    def claim_devices(self, requested_device_ids, task_id=None):
        requested_device_ids = [str(device_id) for device_id in requested_device_ids or []]
        task_id = task_id or threading.get_ident()
        with self._device_condition:
            while True:
                settings, allowed_devices, default_devices, spare_devices = self._claimable_devices(requested_device_ids)
                if not allowed_devices:
                    return []
                assigned = set().union(*self._devices_by_task.values()) if self._devices_by_task else set()
                if not self._is_first_waiting_task(task_id):
                    self._set_task_status(task_id, "Waiting for queue position")
                    self._device_condition.wait(timeout=2)
                    continue
                if not settings.get("use_spare_devices_for_queue", False):
                    desired = list(allowed_devices)
                    if all(device_id not in assigned for device_id in desired):
                        self._devices_by_task[task_id] = set(desired)
                        return desired
                else:
                    if default_devices and all(device_id not in assigned for device_id in default_devices):
                        self._devices_by_task[task_id] = set(default_devices)
                        return list(default_devices)
                    for device_id in spare_devices:
                        if device_id not in assigned:
                            self._devices_by_task[task_id] = {device_id}
                            return [device_id]
                self._set_task_status(task_id, "Waiting for device")
                self._device_condition.wait(timeout=2)

    def notify_queue_changed(self):
        with self._device_condition:
            self._device_condition.notify_all()

    def _set_task_status(self, task_id, status):
        with app.app_context():
            task = db.session.get(UploadedTask, task_id)
            if task is not None and not task.completed and task.status != status:
                task.status = status
                db.session.commit()

    def release_devices(self, task_id):
        with self._device_condition:
            self._devices_by_task.pop(task_id, None)
            self._device_condition.notify_all()

    def callback_attack(self, future: concurrent.futures.Future):
        # called when the future is done or cancelled
        try:
            exception = future.exception()
        except concurrent.futures.CancelledError as cancelled_error:
            exception = None
        if exception is not None:
            logger.exception(repr(exception), exc_info=False)
        job_id = id(future)
        lock = self.locks.pop(job_id, None)
        if lock is None:
            logger.error("Could not find lock for job {}".format(job_id))
            return
        self.release_devices(lock.task_id)
        with lock:
            if future.cancelled():
                lock.set_status(TaskInfoStatus.CANCELLED)
            elif lock.found_key:
                lock.set_status(TaskInfoStatus.CRACKED)
            else:
                lock.set_status(TaskInfoStatus.COMPLETED)
            if exception is not None:
                if isinstance(exception, CancelledError):
                    lock.set_status(TaskInfoStatus.CANCELLED)
                else:
                    lock.set_status(str(exception) or repr(exception))
            lock.finish()
            update_dict = lock.update_dict()
            task_id = lock.task_id
        with app.app_context():
            UploadedTask.query.filter_by(id=task_id).update(update_dict)
            db.session.commit()
        self.locks_onetime.append(lock)

    def submit_recovery(self, task: UploadedTask, state: dict):
        lock = ProgressLock(task_id=task.id)
        future = self.executor.submit(_recover_async, worker=self, lock=lock, state=state)
        future.add_done_callback(self.callback_attack)
        with lock:
            lock.future = future
        self.locks[id(future)] = lock
        task.status = TaskInfoStatus.SCHEDULED
        task.completed = False

    def submit_capture(self, file_22000, uploaded_form: UploadForm, task: UploadedTask):
        """
        Called in main process.
        Starts cracking .cap file in parallel process.
        :param uploaded_task: uploaded .cap file task
        :param timeout: brute force timeout in minutes
        """
        file_22000 = Path(file_22000)
        if not file_22000.exists():
            raise FileNotFoundError(f"Capture file not found: {file_22000}")
        lock = ProgressLock(task_id=task.id)
        from app.utils.settings import apply_hashcat_limits, read_settings
        raw_hashcat_args = uploaded_form.hashcat_args(secret=True)
        requested_device_ids = self.requested_devices_from_args(raw_hashcat_args)
        hashcat_args = apply_hashcat_limits(raw_hashcat_args)
        settings = read_settings()
        configured_max_time = settings.get("max_job_time_minutes")
        requested_timeout = uploaded_form.timeout.data
        effective_timeout = requested_timeout
        work_mode = str(uploaded_form.workload.data)
        if work_mode == Workload.Normal.value:
            effective_timeout = None
        elif configured_max_time:
            effective_timeout = configured_max_time if requested_timeout is None else min(requested_timeout, configured_max_time)
        wordlist_path = uploaded_form.get_wordlist_path()
        rule = uploaded_form.get_rule()
        attack = CapAttack(file_22000=file_22000,
                           lock=lock,
                           wordlist=wordlist_path,
                           rule=rule,
                           hashcat_args=hashcat_args,
                           timeout=effective_timeout,
                           work_mode=work_mode)
        future = self.executor.submit(
            _crack_async,
            worker=self,
            attack=attack,
            raw_hashcat_args=raw_hashcat_args,
            requested_device_ids=requested_device_ids,
        )
        future.add_done_callback(self.callback_attack)
        with lock:
            lock.future = future
        self.locks[id(future)] = lock

    def benchmark(self):
        """
        Run hashcat WPA benchmark.
        """
        self.executor.submit(_hashcat_benchmark_async)

    def terminate(self):
        for lock in tuple(self.locks.values()):
            with lock:
                lock.cancel()
                self.release_devices(lock.task_id)
        subprocess_call(["pkill", "hashcat"])
        self.locks.clear()

    def cancel(self, task_id: int):
        for job_id, lock in tuple(self.locks.items()):
            with lock:
                if lock.task_id == task_id:
                    return lock.cancel()
        return False

    def __del__(self):
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=False)
