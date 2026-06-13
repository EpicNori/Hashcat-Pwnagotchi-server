import os
import shlex
import shutil
import subprocess
import sys
from http import HTTPStatus
from pathlib import Path
from threading import Thread

import flask
from flask import request, render_template, redirect, url_for
from flask.json import jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.datastructures import CombinedMultiDict

from app import app, db, limiter
from app.config import APP_UPDATE_PROGRESS_FILE, NVIDIA_INSTALL_PROGRESS_FILE
from app.attack.convert import split_by_essid, convert_to_22000
from app.attack.recovery import read_recovery_state
from app.attack.worker import HashcatWorker
from app.domain import TaskInfoStatus, Rule, InvalidFileError, Workload, HashcatMode
from app.logger import logger
from app.login import LoginForm, RegistrationForm, User, RoleEnum, register_user, create_first_users, Role, \
    roles_required, user_has_roles
from app.uploader import cap_uploads, UploadForm, UploadedTask, PwnagotchiStatus, check_incomplete_tasks, backward_db_compatibility, ensure_upload_queue_position_column
from app.utils.file_io import read_last_benchmark, bssid_essid_from_22000, build_rainbow_wordlist, read_hashcat_brain_password, decode_essid_hex, normalize_stored_capture_filename, resolve_existing_capture_path, extract_passwords_from_found_key
from app.utils.utils import is_safe_url, hashcat_devices_info, date_formatted
from app.word_magic import create_digits_wordlist, estimate_runtime_fmt, create_fast_wordlists
from app.word_magic.wordlist import download_wordlist, find_wordlist_by_name, WordListDefault

hashcat_worker = HashcatWorker(app)


def active_task_filter():
    return (
        UploadedTask.status == TaskInfoStatus.RUNNING
    ) | UploadedTask.status.startswith("Restoring")


def queued_task_filter():
    return (UploadedTask.completed == False) & (
        (UploadedTask.status == TaskInfoStatus.SCHEDULED) |
        UploadedTask.status.startswith("Waiting")
    )


def next_queue_position():
    highest = db.session.query(db.func.max(UploadedTask.queue_position)).scalar()
    return (highest or 0) + 1


def proceed_login(user: User, remember=False):
    login_user(user, remember=remember)
    next_page = request.args.get('next')
    if not is_safe_url(next_page):
        return flask.abort(HTTPStatus.NOT_ACCEPTABLE)
    flask.flash('Successfully logged in.')
    return redirect(next_page or flask.url_for('user_profile'))


@app.route('/')
@app.route('/index')
def index():
    from app.uploader import UploadedTask
    from app.login import User
    from app.utils.settings import read_settings
    from app.utils.utils import get_hashcat_devices
    
    settings = read_settings()
    devices = get_hashcat_devices()
    device_intensities = settings.get("device_intensities", {})
    
    stats = {
        'total_handshakes': UploadedTask.query.count(),
        'total_cracked': UploadedTask.query.filter(UploadedTask.found_key.is_not(None)).count(),
        'total_failed': UploadedTask.query.filter(UploadedTask.completed == True, UploadedTask.found_key.is_(None)).count(),
        'total_active': UploadedTask.query.filter(active_task_filter()).count(),
        'total_users': User.query.count(),
        'cpu_limit': settings.get('cpu_percent', 100)
    }
    
    return render_template('index.html', stats=stats, devices=devices, device_intensities=device_intensities)

@app.route('/learn_more')
def learn_more():
    return render_template('learn_more.html')


def get_version():
    try:
        return (Path(app.root_path).parent / "VERSION").read_text().strip()
    except Exception:
        return "1.0.0"


def get_management_script_path(script_name: str) -> str:
    installed_path = Path("/opt/hashcat-wpa-server/bash") / script_name
    if installed_path.exists():
        return str(installed_path)
    return str(Path(app.root_path).parent / "bash" / script_name)


def get_autostart_status():
    try:
        result = subprocess.run(
            ["sudo", get_management_script_path("autostart_service.sh"), "status"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False
        )
        status = (result.stdout or result.stderr or "").strip()
        return status or "unknown"
    except Exception:
        return "unknown"


def get_update_status():
    update_log = Path("/var/log/hashcat-wpa-server/updater.log")
    status = "idle"
    summary = "No update log available yet."

    try:
        result = subprocess.run(
            ["systemctl", "is-active", "hashcat-server-updater.service"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False
        )
        service_state = (result.stdout or "").strip()
        if service_state == "active":
            status = "running"
            summary = "Update is currently running in the background."
    except Exception:
        pass

    if update_log.exists():
        try:
            lines = update_log.read_text(errors="ignore").splitlines()
            tail_lines = lines[-12:]
            log_excerpt = "\n".join(tail_lines) if tail_lines else "Log file is empty."
            joined = "\n".join(lines[-25:])
            if "failed to start after update" in joined.lower() or "[!]" in joined:
                status = "failed"
                summary = "The last update reported an error."
            elif "[+] hashcat-wpa-server.service is active." in joined or "[*] Update complete." in joined:
                status = "success"
                summary = "The last update finished and the service reported active."
            elif status != "running":
                summary = "Last update log found, but completion could not be confirmed."
            return status, summary, log_excerpt
        except Exception as e:
            return "unknown", f"Could not read update log: {e}", "Log read failed."

    return status, summary, "No update log available yet."


def get_tailscale_snapshot():
    if not shutil.which("tailscale"):
        return {
            "status": "Not installed",
            "running": False,
            "ip": "",
            "plugin_url": "",
        }

    ip = ""
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        ip = (result.stdout or "").strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""
    except Exception:
        ip = ""

    if ip:
        return {
            "status": f"Running ({ip})",
            "running": True,
            "ip": ip,
            "plugin_url": f"http://{ip}:9111",
        }

    try:
        result = subprocess.run(
            ["tailscale", "status", "--self"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        detail = (result.stdout or result.stderr or "").strip()
    except Exception as error:
        detail = str(error)

    return {
        "status": "Installed, not connected" if not detail else f"Installed, not connected: {detail.splitlines()[0]}",
        "running": False,
        "ip": "",
        "plugin_url": "",
    }


def get_cloudflare_snapshot():
    settings = read_settings()
    plugin_url = settings.get("public_plugin_url", "")
    installed = bool(shutil.which("cloudflared"))
    running = False
    detail = "Not installed"

    try:
        result = subprocess.run(
            ["systemctl", "is-active", "cloudflared"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        state = (result.stdout or "").strip()
        if state:
            running = state == "active"
            detail = f"Service {state}"
        elif installed:
            detail = "Installed, service state unknown"
    except Exception as error:
        detail = str(error)

    return {
        "status": detail,
        "installed": installed,
        "running": running,
        "plugin_url": plugin_url,
    }


def get_runtime_logs_dir() -> Path:
    return Path("/var/log/hashcat-wpa-server")


def get_progress_file(kind: str) -> Path:
    filename = APP_UPDATE_PROGRESS_FILE if kind == "update" else NVIDIA_INSTALL_PROGRESS_FILE
    return get_runtime_logs_dir() / filename


def read_progress_snapshot(path: Path, default_message: str) -> dict:
    snapshot = {
        "state": "idle",
        "progress": 0,
        "message": default_message,
    }

    try:
        if not path.exists():
            return snapshot

        raw_value = path.read_text(errors="ignore").strip()
        if not raw_value:
            return snapshot

        parts = raw_value.split("|", 2)
        state = (parts[0] or "idle").strip().lower() if parts else "idle"
        try:
            progress = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            progress = 0
        progress = max(0, min(100, progress))
        message = parts[2].strip() if len(parts) > 2 and parts[2].strip() else default_message
        snapshot.update({
            "state": state,
            "progress": progress,
            "message": message,
        })
        return snapshot
    except Exception as error:
        snapshot.update({
            "state": "unknown",
            "message": f"Could not read progress: {error}",
        })
        return snapshot


def write_progress_snapshot(kind: str, state: str, progress: int, message: str):
    path = get_progress_file(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    progress = max(0, min(100, int(progress)))
    path.write_text(f"{state}|{progress}|{message}\n", encoding="utf-8")


def get_install_progress() -> dict:
    return {
        "update": read_progress_snapshot(
            get_progress_file("update"),
            "Waiting for the app update to start.",
        ),
        "nvidia": read_progress_snapshot(
            get_progress_file("nvidia"),
            "Waiting for the NVIDIA install to start.",
        ),
    }

@app.context_processor
def inject_version():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        network_ip = s.getsockname()[0]
        s.close()
    except Exception:
        network_ip = "YOUR_SERVER_IP"
    return dict(version=get_version(), network_ip=network_ip)


def split_hashcat_args(hashcat_args_text: str):
    if not hashcat_args_text:
        return []
    return shlex.split(hashcat_args_text)


def form_for_stored_task(task: UploadedTask):
    from types import SimpleNamespace

    wordlist_info = find_wordlist_by_name(task.wordlist)
    wordlist_path = wordlist_info.path if wordlist_info is not None else None
    rule = Rule.from_data(task.rule)

    base_hashcat_args = split_hashcat_args(task.hashcat_args)
    filtered_hashcat_args = []
    skip_next = False
    for arg in base_hashcat_args:
        if skip_next:
            skip_next = False
            continue
        if arg == "-d":
            skip_next = True
            continue
        if arg.startswith("--brain-password="):
            continue
        filtered_hashcat_args.append(arg)

    if "--brain-client" in filtered_hashcat_args and not any(arg.startswith("--brain-password=") for arg in filtered_hashcat_args):
        filtered_hashcat_args.append(f"--brain-password={read_hashcat_brain_password()}")

    return SimpleNamespace(
        timeout=SimpleNamespace(data=None),
        workload=SimpleNamespace(data=Workload.Normal.value),
        get_wordlist_path=lambda: wordlist_path,
        get_rule=lambda: rule,
        hashcat_args=lambda secret=False: list(filtered_hashcat_args),
    )


def resolve_task_attack_file(task: UploadedTask):
    capture_path = resolve_capture_path(task.filename)
    if not capture_path.exists():
        raise FileNotFoundError(f"Original capture file not found: {capture_path}")

    file_22000 = convert_to_22000(capture_path)
    folder_split_by_essid = split_by_essid(file_22000)

    for file_essid in iter_split_capture_files(folder_split_by_essid):
        bssid, essid = decode_task_essid(file_essid)
        if bssid == task.bssid and essid == task.essid:
            return file_essid

    raise InvalidFileError("Could not match the original ESSID/BSSID pair in the capture file.")


def decode_task_essid(file_22000: Path):
    bssid_essid = next(bssid_essid_from_22000(file_22000))
    bssid, essid_hex = bssid_essid.split(':')
    essid = decode_essid_hex(essid_hex)
    return bssid, essid


def normalize_task_filename(saved_filename: str) -> str:
    return normalize_stored_capture_filename(saved_filename)


def resolve_capture_path(saved_filename: str) -> Path:
    return resolve_existing_capture_path(saved_filename)


def iter_split_capture_files(split_folder: Path):
    valid_suffixes = set(HashcatMode.valid_modes())
    for candidate in sorted(split_folder.iterdir()):
        if candidate.is_file() and candidate.suffix.lstrip(".") in valid_suffixes:
            yield candidate


def save_capture_for_user(file_storage, username: str) -> tuple[str, Path]:
    saved_filename = cap_uploads.save(file_storage, folder=username)
    filename = normalize_task_filename(saved_filename)
    cap_path = resolve_capture_path(filename)
    if cap_path.suffix.lstrip('.').lower() not in set(HashcatMode.valid_upload_suffixes()):
        cap_path.unlink(missing_ok=True)
        import flask
        from http import HTTPStatus
        flask.abort(HTTPStatus.BAD_REQUEST, description="Invalid file type after save")
    return filename, cap_path


def non_empty_uploads(field_name: str = 'capture'):
    return [upload for upload in request.files.getlist(field_name) if upload and upload.filename]


def submit_uploaded_capture(file_storage, user: User, form: UploadForm, wordlist_started: bool = False):
    filename, cap_path = save_capture_for_user(file_storage, user.username)
    file_22000 = convert_to_22000(cap_path)
    folder_split_by_essid = split_by_essid(file_22000)

    if not wordlist_started:
        Thread(target=download_wordlist, args=(form.get_wordlist_path(),)).start()

    tasks = {}
    queue_position = next_queue_position()
    hashcat_args = ' '.join(form.hashcat_args())
    for file_essid in iter_split_capture_files(folder_split_by_essid):
        bssid_essid = next(bssid_essid_from_22000(file_essid))
        bssid, essid = bssid_essid.split(':')
        essid = decode_essid_hex(essid)
        new_task = UploadedTask(user_id=user.id, filename=filename, wordlist=form.get_wordlist_name(),
                                rule=form.rule.data, bssid=bssid, essid=essid, hashcat_args=hashcat_args,
                                queue_position=queue_position)
        tasks[file_essid] = new_task
        queue_position += 1

    if not tasks:
        raise InvalidFileError(f"No crackable WPA data was found in {file_storage.filename}.")

    db.session.add_all(tasks.values())
    db.session.commit()
    for file_essid, task in tasks.items():
        hashcat_worker.submit_capture(file_essid, uploaded_form=form, task=task)
    upsert_pwnagotchi_status(
        username=user.username,
        event="upload",
        message=f"Uploaded {filename} and scheduled {len(tasks)} task(s).",
        upload_filename=filename,
    )

    return {
        "filename": filename,
        "tasks": len(tasks),
    }


def upsert_pwnagotchi_status(*, username: str, event: str, message: str | None = None,
                             hostname: str | None = None, plugin_version: str | None = None,
                             upload_filename: str | None = None):
    status = PwnagotchiStatus.query.filter_by(username=username).first()
    if status is None:
        status = PwnagotchiStatus(username=username)
        db.session.add(status)
    status.mark_seen(
        event=event,
        message=message,
        hostname=hostname,
        plugin_version=plugin_version,
        upload_filename=upload_filename,
    )
    db.session.commit()
    return status


def get_pwnagotchi_status_snapshot():
    statuses = PwnagotchiStatus.query.order_by(PwnagotchiStatus.last_seen.desc().nullslast()).all()
    return [{
        "username": status.username,
        "hostname": status.hostname or status.username,
        "plugin_version": status.plugin_version or "unknown",
        "last_event": status.last_event or "unknown",
        "last_message": status.last_message or "",
        "last_seen": status.last_seen,
        "last_upload_at": status.last_upload_at,
        "last_upload_filename": status.last_upload_filename or "",
        "upload_count": status.upload_count or 0,
        "online": status.is_online,
    } for status in statuses]


@app.route('/pwnagotchi')
def pwnagotchi():
    statuses = get_pwnagotchi_status_snapshot()
    latest_status = statuses[0] if statuses else None
    return render_template('pwnagotchi.html', title='Pwnagotchi Integration',
                           pwnagotchi_statuses=statuses, latest_pwnagotchi_status=latest_status,
                           api_upload_url=url_for('api_upload'), api_heartbeat_url=url_for('api_pwnagotchi_heartbeat'),
                           tailscale_snapshot=get_tailscale_snapshot())


@app.shell_context_processor
def make_shell_context():
    return dict(db=db, User=User, Role=Role, UploadedTask=UploadedTask, version=get_version())


@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    form = UploadForm()
    if form.validate_on_submit():
        if not user_has_roles(current_user, RoleEnum.USER):
            return flask.abort(HTTPStatus.FORBIDDEN, description="You do not have the permission to start jobs.")
        uploads = non_empty_uploads()
        uploaded = []
        failed = []
        wordlist_started = False
        for file_storage in uploads:
            try:
                result = submit_uploaded_capture(file_storage, current_user, form, wordlist_started=wordlist_started)
                wordlist_started = True
                uploaded.append(result)
            except (FileNotFoundError, InvalidFileError, ValueError) as error:
                logger.exception(error)
                failed.append((file_storage.filename, str(error)))

        if uploaded:
            task_count = sum(item["tasks"] for item in uploaded)
            flask.flash(f"Uploaded {len(uploaded)} capture(s) and scheduled {task_count} task(s).", category="success")
        for filename, error in failed:
            flask.flash(f"Skipped {filename}: {error}", category="error")
        if not uploaded and failed:
            return flask.abort(HTTPStatus.BAD_REQUEST, description="No captures could be uploaded.")
        return redirect(url_for('user_profile'))
    missing_default_wordlists = [wlist for wlist in WordListDefault.list() if not wlist.path.exists()]
    return render_template('upload.html', title='Upload', form=form, missing_default_wordlists=missing_default_wordlists)


@app.route('/install_default_wordlist/<wordlist_name>', methods=['POST'])
@login_required
def install_default_wordlist(wordlist_name):
    target = None
    for wlist in WordListDefault.list():
        if wordlist_name == wlist.path.name:
            target = wlist
            break

    if target is None:
        flask.flash("Unknown default wordlist.", category="error")
        return redirect(url_for('upload'))

    if target.path.exists():
        flask.flash(f"{target.name} is already installed.", category="info")
        return redirect(url_for('upload'))

    try:
        download_wordlist(target.path)
        if target.path.exists():
            flask.flash(f"Installed {target.name}.", category="success")
        else:
            flask.flash(f"Could not install {target.name}.", category="error")
    except Exception as error:
        logger.exception(error)
        flask.flash(f"Failed to install {target.name}: {error}", category="error")

    return redirect(url_for('upload'))


@app.route('/api/admin/install_progress')
@login_required
@roles_required(RoleEnum.ADMIN)
def api_admin_install_progress():
    return jsonify(get_install_progress())


@app.route('/update_wait')
@login_required
@roles_required(RoleEnum.ADMIN)
def update_wait():
    return render_template(
        'update_wait.html',
        title='Updating',
        update_progress=get_install_progress()["update"],
    )

@app.route('/api/upload', methods=['POST'])
def api_upload():
    auth = request.authorization
    if not auth or not auth.username or not auth.password:
        return flask.abort(HTTPStatus.UNAUTHORIZED, description="Missing basic authentication")
    user = User.query.filter_by(username=auth.username).first()
    if not user or not user.verify_password(auth.password):
        return flask.abort(HTTPStatus.UNAUTHORIZED, description="Invalid credentials")
    if not user_has_roles(user, RoleEnum.USER):
        return flask.abort(HTTPStatus.FORBIDDEN, description="Insufficient permissions")
    
    uploads = non_empty_uploads()
    if not uploads:
        return flask.abort(HTTPStatus.BAD_REQUEST, description="Missing capture file")
        
    # Disable CSRF for this API endpoint
    from app.utils.settings import read_settings
    settings = read_settings()
    form = UploadForm(
        formdata=CombinedMultiDict((request.files, request.form)),
        meta={'csrf': False},
        data={'workload': Workload.normalize(settings.get("default_api_workload", Workload.Normal.value))}
    )
    if not form.validate():
        return flask.abort(HTTPStatus.BAD_REQUEST, description=str(form.errors))
    uploaded = []
    failed = []
    wordlist_started = False
    for file_storage in uploads:
        try:
            result = submit_uploaded_capture(file_storage, user, form, wordlist_started=wordlist_started)
            wordlist_started = True
            uploaded.append(result)
        except (FileNotFoundError, InvalidFileError, ValueError) as error:
            logger.exception(error)
            failed.append({"filename": file_storage.filename, "error": str(error)})

    if not uploaded:
        return flask.abort(HTTPStatus.BAD_REQUEST, description=f"Could not upload any captures: {failed}")

    return jsonify({
        "status": "success" if not failed else "partial_success",
        "message": f"Uploaded {len(uploaded)} capture(s) with {sum(item['tasks'] for item in uploaded)} task(s) scheduled.",
        "uploaded": uploaded,
        "failed": failed,
    })


@app.route('/api/pwnagotchi/heartbeat', methods=['POST'])
def api_pwnagotchi_heartbeat():
    auth = request.authorization
    if not auth or not auth.username or not auth.password:
        return flask.abort(HTTPStatus.UNAUTHORIZED, description="Missing basic authentication")
    user = User.query.filter_by(username=auth.username).first()
    if not user or not user.verify_password(auth.password):
        return flask.abort(HTTPStatus.UNAUTHORIZED, description="Invalid credentials")
    if not user_has_roles(user, RoleEnum.USER):
        return flask.abort(HTTPStatus.FORBIDDEN, description="Insufficient permissions")

    payload = request.get_json(silent=True) or request.form or {}
    status = upsert_pwnagotchi_status(
        username=user.username,
        event=payload.get("event", "heartbeat"),
        message=payload.get("message"),
        hostname=payload.get("hostname"),
        plugin_version=payload.get("plugin_version"),
    )
    return jsonify({
        "status": "success",
        "online": status.is_online,
        "last_seen": status.last_seen.isoformat() if status.last_seen else None,
    })



@app.route('/estimate_runtime', methods=['POST'])
@login_required
def estimate_runtime():
    wordlist = request.form.get('wordlist')
    rule = Rule.from_data(request.form.get('rule'))
    runtime = estimate_runtime_fmt(wordlist_path=wordlist, rule=rule)
    return jsonify(runtime)


@app.route('/user_profile')
@login_required
def user_profile():
    from app.uploader import UploadedTask
    if user_has_roles(current_user, RoleEnum.ADMIN):
        tasks = UploadedTask.query.order_by(
            UploadedTask.completed.asc(),
            UploadedTask.queue_position.asc(),
            UploadedTask.uploaded_time.desc(),
        ).all()
    else:
        tasks = UploadedTask.query.filter_by(user_id=current_user.id).order_by(
            UploadedTask.completed.asc(),
            UploadedTask.queue_position.asc(),
            UploadedTask.uploaded_time.desc(),
        ).all()
    retryable_count = sum(1 for task in tasks if task.completed and not task.found_key)
    return render_template('user_profile.html', title='Home', tasks=tasks,
                           can_reorder_queue=user_has_roles(current_user, RoleEnum.ADMIN),
                           retryable_count=retryable_count,
                           benchmark=read_last_benchmark(), devices=hashcat_devices_info(), progress=progress())


@app.route('/progress')
@limiter.exempt
@login_required
def progress():
    tasks_progress = []
    is_admin = user_has_roles(current_user, RoleEnum.ADMIN)
    user_tasks_id = set(task.id for task in current_user.uploads)
    locks = set(hashcat_worker.locks.values())
    locks.update(hashcat_worker.locks_onetime)
    hashcat_worker.locks_onetime.clear()
    for lock in locks:
        with lock:
            task_id = lock.task_id
            if is_admin or task_id in user_tasks_id:
                task_progress = dict(task_id=task_id,
                                     progress=f"{lock.progress:.2f}",
                                     speed=lock.speed,
                                     status=lock.status,
                                     duration=str(lock.duration),
                                     found_key=lock.found_key)
                tasks_progress.append(task_progress)
    return jsonify(tasks_progress)


@app.route('/download/<int:task_id>/<string:file_type>')
@login_required
def download(task_id, file_type):
    from app.uploader import UploadedTask
    task = UploadedTask.query.get_or_404(task_id)
    
    # Permission check: Admin can download everything, Users only their own
    if not user_has_roles(current_user, RoleEnum.ADMIN) and task.user_id != current_user.id:
        return flask.abort(HTTPStatus.FORBIDDEN, description="You do not have permission to download this file.")
    
    # Base path logic
    # Note: task.filename usually includes user folder, e.g. "admin/Handshake.pcap"
    base_file = resolve_capture_path(task.filename)
    
    if file_type == 'capture':
        p = base_file
        download_name = Path(task.filename).name
    elif file_type == 'result':
        p = base_file.with_suffix('.key')
        download_name = Path(task.filename).with_suffix('.key').name
    else:
        return flask.abort(HTTPStatus.BAD_REQUEST)
        
    if not p.exists():
        return flask.abort(HTTPStatus.NOT_FOUND, description=f"The requested {file_type} file could not be found.")
        
    return flask.send_file(str(p), as_attachment=True, download_name=download_name)


@app.route('/download_all_results')
@login_required
def download_all_results():
    from app.uploader import UploadedTask
    import io
    
    if user_has_roles(current_user, RoleEnum.ADMIN):
        tasks = UploadedTask.query.filter(UploadedTask.found_key.is_not(None)).all()
    else:
        tasks = UploadedTask.query.filter_by(user_id=current_user.id).filter(UploadedTask.found_key.is_not(None)).all()
        
    if not tasks:
        flask.flash("No cracked passwords found to download.", category="info")
        return redirect(url_for('user_profile'))
        
    # Create the text file in memory
    output = io.StringIO()
    output.write(f"# Hashcat WPA Server - Cracked Passwords Export ({date_formatted()})\n")
    output.write("# Format: ESSID | BSSID | Password\n")
    output.write("-" * 50 + "\n")
    
    for task in tasks:
        passwords = extract_passwords_from_found_key(task.found_key)
        if not passwords:
            output.write(f"{task.essid} | {task.bssid} | \n")
            continue
        for password in passwords:
            output.write(f"{task.essid} | {task.bssid} | {password}\n")
        
    # Seek to beginning to read
    output.seek(0)
    
    return flask.Response(
        output.getvalue(),
        mimetype="text/plain",
        headers={"Content-disposition": f"attachment; filename=cracked_passwords_{current_user.username}.txt"}
    )


@app.route('/download_rainbow_wordlist')
@login_required
def download_rainbow_wordlist():
    if not user_has_roles(current_user, RoleEnum.ADMIN):
        return flask.abort(HTTPStatus.FORBIDDEN, description="You do not have permission to download the rainbow wordlist.")

    rainbow_wordlist = build_rainbow_wordlist()
    if rainbow_wordlist is None or not rainbow_wordlist.exists():
        flask.flash("No rainbow wordlist is available yet.", category="info")
        return redirect(url_for('admin_rainbow'))

    return flask.send_file(
        str(rainbow_wordlist),
        as_attachment=True,
        download_name=rainbow_wordlist.name
    )


@app.route('/admin/rainbow')
@login_required
@roles_required(RoleEnum.ADMIN)
def admin_rainbow():
    from datetime import datetime

    rainbow_wordlist = build_rainbow_wordlist()
    rainbow_entries = []
    rainbow_size = 0
    rainbow_updated = None

    if rainbow_wordlist is not None and rainbow_wordlist.exists():
        rainbow_updated = datetime.fromtimestamp(rainbow_wordlist.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        try:
            rainbow_entries = rainbow_wordlist.read_text(errors="ignore").splitlines()
        except OSError:
            rainbow_entries = []
        rainbow_size = len(rainbow_entries)

    return render_template(
        'admin_rainbow.html',
        title='Rainbow Wordlist',
        rainbow_wordlist=rainbow_wordlist,
        rainbow_entries=rainbow_entries[:50],
        rainbow_size=rainbow_size,
        rainbow_updated=rainbow_updated,
    )


@app.route('/download_test_capture')
@login_required
def download_test_capture():
    sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
    if not sample_capture.exists():
        return flask.abort(HTTPStatus.NOT_FOUND, description="The bundled test capture could not be found.")
    return flask.send_file(
        str(sample_capture),
        as_attachment=True,
        download_name=sample_capture.name
    )


@app.route('/download_test_capture_pcap')
@login_required
def download_test_capture_pcap():
    sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.pcap"
    if not sample_capture.exists():
        return flask.abort(HTTPStatus.NOT_FOUND, description="The bundled test PCAP could not be found.")
    return flask.send_file(
        str(sample_capture),
        as_attachment=True,
        download_name=sample_capture.name
    )


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.verify_password(form.password.data):
            flask.flash('Invalid username or password', category='error')
            return redirect(url_for('login'))
        return proceed_login(user, remember=form.remember_me.data)
    return render_template('login.html', title='Login', form=form)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/register', methods=['GET', 'POST'])
@login_required
@roles_required(RoleEnum.ADMIN)
def register():
    # register User by Admin
    form = RegistrationForm()
    if form.validate_on_submit():
        user = register_user(user=form.username.data, password=form.password.data, roles=RoleEnum.USER)
        flask.flash("You have successfully registered the new {role} '{name}'.".format(role=RoleEnum.USER.value,
                                                                                       name=user.username))
        return redirect(url_for('index'))
    return render_template('register.html', title='Admin register', form=form)


@app.route("/benchmark")
@login_required
def benchmark():
    hashcat_worker.benchmark()
    return jsonify("Started benchmark.")


@app.route("/cancel/<int:task_id>")
@login_required
def cancel(task_id):
    task = db.session.get(UploadedTask, task_id)
    if task is None:
        return flask.Response(status=HTTPStatus.BAD_REQUEST)
    if task.user_id != current_user.id:
        return flask.Response(status=HTTPStatus.FORBIDDEN)
    if hashcat_worker.cancel(task.id):
        return jsonify(TaskInfoStatus.CANCELLED)
    else:
        return jsonify("Cancelling...")


@app.route("/queue/<int:task_id>/<string:direction>")
@login_required
@roles_required(RoleEnum.ADMIN)
def move_queue_task(task_id, direction):
    if direction not in {"up", "down"}:
        return flask.abort(HTTPStatus.BAD_REQUEST, description="Queue direction must be up or down.")

    task = db.session.get(UploadedTask, task_id)
    if task is None:
        return flask.abort(HTTPStatus.NOT_FOUND)
    if task.completed or task.status not in {TaskInfoStatus.SCHEDULED, "Waiting for queue position", "Waiting for device"}:
        flask.flash("Only scheduled handshakes can be moved in the queue.", category="info")
        return redirect(url_for('user_profile'))

    queued_tasks = UploadedTask.query.filter(queued_task_filter()).order_by(
        UploadedTask.queue_position.asc(),
        UploadedTask.uploaded_time.asc(),
        UploadedTask.id.asc(),
    ).all()
    index = next((idx for idx, queued in enumerate(queued_tasks) if queued.id == task.id), None)
    if index is None:
        flask.flash("That handshake is not currently waiting in the queue.", category="info")
        return redirect(url_for('user_profile'))

    swap_index = index - 1 if direction == "up" else index + 1
    if swap_index < 0 or swap_index >= len(queued_tasks):
        flask.flash("That handshake is already at the edge of the queue.", category="info")
        return redirect(url_for('user_profile'))

    other = queued_tasks[swap_index]
    task.queue_position, other.queue_position = other.queue_position, task.queue_position
    db.session.commit()
    hashcat_worker.notify_queue_changed()
    flask.flash(f"Moved task #{task.id} {direction} in the queue.", category="success")
    return redirect(url_for('user_profile'))


@app.route("/requeue/<int:task_id>")
@login_required
def requeue(task_id):
    task = db.session.get(UploadedTask, task_id)
    if task is None:
        return flask.abort(HTTPStatus.NOT_FOUND)
    if not user_has_roles(current_user, RoleEnum.ADMIN) and task.user_id != current_user.id:
        return flask.abort(HTTPStatus.FORBIDDEN, description="You do not have permission to re-queue this task.")

    if not task.completed:
        flask.flash("This task is still running. Cancel it first if you want to restart it.", category="info")
        return redirect(url_for('user_profile'))

    try:
        new_task = requeue_completed_task(task)
        flask.flash(f"Task #{task.id} was re-queued as task #{new_task.id}.", category="success")
    except (FileNotFoundError, InvalidFileError, ValueError) as error:
        logger.exception(error)
        flask.flash(f"Failed to re-queue task #{task.id}: {error}", category="error")

    return redirect(url_for('user_profile'))


@app.route("/requeue_all", methods=["POST"])
@login_required
def requeue_all():
    active_or_queued = UploadedTask.query.filter(UploadedTask.completed == False).count()
    if active_or_queued:
        flask.flash(
            f"Cannot retry all while {active_or_queued} task(s) are already queued or running. "
            "Cancel or finish them first.",
            category="info"
        )
        return redirect(url_for('user_profile'))

    tasks_query = UploadedTask.query.filter(
        UploadedTask.completed == True,
        UploadedTask.found_key.is_(None),
    )
    if not user_has_roles(current_user, RoleEnum.ADMIN):
        tasks_query = tasks_query.filter_by(user_id=current_user.id)

    tasks = tasks_query.order_by(
        UploadedTask.uploaded_time.asc(),
        UploadedTask.id.asc(),
    ).all()

    if not tasks:
        flask.flash("There are no failed completed tasks to retry.", category="info")
        return redirect(url_for('user_profile'))

    requeued = 0
    failed = []
    for task in tasks:
        try:
            requeue_completed_task(task)
            requeued += 1
        except (FileNotFoundError, InvalidFileError, ValueError) as error:
            logger.exception("Failed to re-queue task %s: %s", task.id, error)
            failed.append(f"#{task.id}: {error}")

    if requeued:
        flask.flash(f"Re-queued {requeued} failed task(s).", category="success")
    if failed:
        flask.flash(f"Could not re-queue {len(failed)} task(s): {'; '.join(failed[:3])}", category="error")

    return redirect(url_for('user_profile'))


def requeue_completed_task(task: UploadedTask):
    attack_file = resolve_task_attack_file(task)
    uploaded_form = form_for_stored_task(task)
    new_task = UploadedTask(
        user_id=task.user_id,
        filename=task.filename,
        wordlist=task.wordlist,
        rule=task.rule,
        bssid=task.bssid,
        essid=task.essid,
        hashcat_args=' '.join(split_hashcat_args(task.hashcat_args)),
        queue_position=next_queue_position()
    )
    db.session.add(new_task)
    db.session.commit()

    hashcat_worker.submit_capture(attack_file, uploaded_form=uploaded_form, task=new_task)
    return new_task


@app.route('/terminate')
@login_required
@roles_required(RoleEnum.ADMIN)
def terminate():
    cancelled_count = hashcat_worker.terminate()
    return jsonify(f"Terminated all jobs and cancelled {cancelled_count} queued/running task(s).")


@app.route('/hashcat.potfile')
@login_required
@roles_required(RoleEnum.ADMIN)
def hashcat_potfile():
    hashcat_potfile = Path.home() / ".hashcat" / "hashcat.potfile"
    if hashcat_potfile.exists():
        return hashcat_potfile.read_text()
    return jsonify("Empty hashcat.potfile")

from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SubmitField, PasswordField, StringField, RadioField
from wtforms.validators import DataRequired, NumberRange, EqualTo, Optional, Regexp
from app.utils.settings import read_settings, write_settings, update_admin_setting

from wtforms import StringField

from wtforms import SelectMultipleField, widgets

class MultiCheckboxField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()

class SettingsForm(FlaskForm):
    cpu_percent = IntegerField('Global CPU Thread Limit (%)', validators=[DataRequired(), NumberRange(min=1, max=100)], description="Limit total CPU threads for host operations.")
    gpu_temp_limit = IntegerField('GPU Max Temp (°C)', validators=[DataRequired(), NumberRange(min=50, max=100)], default=90, description="Hashcat will abort if GPU exceeds this temperature.")
    cpu_temp_limit = IntegerField('CPU Max Temp (°C)', validators=[DataRequired(), NumberRange(min=50, max=100)], default=90, description="Server will pause jobs if CPU exceeds this temperature.")
    temp_resume_delta = IntegerField('Resume Margin (C)', validators=[DataRequired(), NumberRange(min=1, max=30)], default=5, description="Jobs resume after temperatures cool down by this many degrees below the limit.")
    max_job_time_minutes = IntegerField('Max Job Time (minutes, optional)', validators=[Optional(), NumberRange(min=1)], description="Stop any cracking job that runs longer than this limit.")
    default_devices = MultiCheckboxField('Default Devices (for Pwnagotchi/API)', choices=[])
    default_api_workload = RadioField('Default Work Mode (for Pwnagotchi/API)', choices=Workload.to_form(), default=Workload.Normal.value)
    use_spare_devices_for_queue = BooleanField(
        'Use spare devices for queued handshakes',
        description='Run the first active handshake on the default devices, then use other enabled devices for additional queued handshakes.'
    )
    submit = SubmitField('Save Performance Settings')

class TailscaleForm(FlaskForm):
    auth_key = StringField('Tailscale Auth Key (optional)', validators=[Optional()])
    submit_tailscale = SubmitField('Install / Connect Tailscale')

class PublicWebsiteForm(FlaskForm):
    public_hostname = StringField(
        'Public hostname',
        validators=[
            DataRequired(),
            Regexp(r'^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$', message='Use a hostname like upload.example.com.')
        ],
        description='The hostname you configured in Cloudflare Zero Trust.'
    )
    tunnel_token = PasswordField('Cloudflare Tunnel token', validators=[DataRequired()])
    submit_public_website = SubmitField('Install / Start Public Website')

class NvidiaDriversForm(FlaskForm):
    submit_check_nvidia = SubmitField('Check NVIDIA Drivers')

class UpdateAppForm(FlaskForm):
    submit_update = SubmitField('Update App & Restart')

class UninstallAppForm(FlaskForm):
    submit_uninstall = SubmitField('Permanently Uninstall Server')

class AutostartForm(FlaskForm):
    submit_enable_autostart = SubmitField('Enable Autostart')
    submit_disable_autostart = SubmitField('Disable Autostart')

class AccountSettingsForm(FlaskForm):
    new_username = StringField('Update Username', validators=[DataRequired()])
    new_password = PasswordField('New Password (leave blank to keep current)', render_kw={"autocomplete": "new-password"})
    confirm_password = PasswordField('Confirm New Password', validators=[EqualTo('new_password', message='Passwords must match')], render_kw={"autocomplete": "new-password"})
    submit_account = SubmitField('Update Account')


class EditUserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    new_password = PasswordField('New Password (leave blank to keep current)', render_kw={"autocomplete": "new-password"})
    confirm_password = PasswordField('Confirm New Password', validators=[EqualTo('new_password', message='Passwords must match')], render_kw={"autocomplete": "new-password"})
    roles = MultiCheckboxField('Roles', choices=[])
    submit_user = SubmitField('Save User Changes')

@app.route('/settings', methods=['GET', 'POST'])
@login_required
@roles_required(RoleEnum.ADMIN)
def admin_settings():
    form = SettingsForm()
    ts_form = TailscaleForm()
    public_form = PublicWebsiteForm()
    nvidia_form = NvidiaDriversForm()
    update_form = UpdateAppForm()
    uninstall_form = UninstallAppForm()
    autostart_form = AutostartForm()
    account_form = AccountSettingsForm()
    
    from app.utils.utils import get_hashcat_devices
    devices = get_hashcat_devices()
    
    if account_form.submit_account.data and account_form.validate():
        existing_user = User.query.filter_by(username=account_form.new_username.data).first()
        if existing_user and existing_user.id != current_user.id:
            flask.flash('Username already exists.', category='error')
        else:
            current_user.username = account_form.new_username.data
            if account_form.new_password.data:
                current_user.set_password(account_form.new_password.data)
            db.session.commit()
            flask.flash('Account settings updated!', category='success')
            return redirect(url_for('admin_settings'))

    settings = read_settings()
    device_intensities = settings.get("device_intensities", {})
    gpu_visible = any(device.get("is_gpu") for device in devices)
    gpu_ready = any(
        device.get("is_gpu") and device.get("hashcat_usable", True)
        for device in devices
    )
    
    # Populate device choices
    form.default_devices.choices = [(d['id'], f"{d['name']} ({d['memory']})") for d in devices]
    
    if form.validate_on_submit():
        new_intensities = {}
        for device in devices:
            val = request.form.get(f"device_{device['id']}", 100)
            new_intensities[str(device['id'])] = int(val)
        
        write_settings(
            device_intensities=new_intensities,
            cpu_percent=form.cpu_percent.data,
            gpu_temp_limit=form.gpu_temp_limit.data,
            cpu_temp_limit=form.cpu_temp_limit.data,
            temp_resume_delta=form.temp_resume_delta.data,
            max_job_time_minutes=form.max_job_time_minutes.data,
            default_devices=form.default_devices.data,
            default_api_workload=form.default_api_workload.data,
            use_spare_devices_for_queue=form.use_spare_devices_for_queue.data
        )
        flask.flash('Performance settings updated successfully!')
        return redirect(url_for('admin_settings'))
    
    # Populate form from current settings
    form.cpu_percent.data = settings.get("cpu_percent", 100)
    form.gpu_temp_limit.data = settings.get("gpu_temp_limit", 90)
    form.cpu_temp_limit.data = settings.get("cpu_temp_limit", 90)
    form.temp_resume_delta.data = settings.get("temp_resume_delta", 5)
    form.max_job_time_minutes.data = settings.get("max_job_time_minutes")
    form.default_devices.data = settings.get("default_devices", ["1"])
    form.default_api_workload.data = Workload.normalize(settings.get("default_api_workload", Workload.Normal.value))
    form.use_spare_devices_for_queue.data = bool(settings.get("use_spare_devices_for_queue", False))
        
    if ts_form.submit_tailscale.data and ts_form.validate():
        try:
            proc = subprocess.Popen(["sudo", get_management_script_path("install_tailscale.sh")], stdin=subprocess.PIPE)
            proc.communicate(input=ts_form.auth_key.data.encode())
            flask.flash('Tailscale connection initiated in the background! Check your Tailscale admin console.', category='success')
        except Exception as e:
            flask.flash(f'Failed to run Tailscale securely: {e}', category='error')
        return redirect(url_for('admin_settings'))

    if public_form.submit_public_website.data and public_form.validate():
        public_url = f"https://{public_form.public_hostname.data.strip().lower()}"
        token = (public_form.tunnel_token.data or "").strip()
        try:
            proc = subprocess.Popen(
                ["sudo", get_management_script_path("install_cloudflare_tunnel.sh"), public_form.public_hostname.data.strip().lower()],
                stdin=subprocess.PIPE,
                text=True,
            )
            proc.communicate(input=token, timeout=20)
            update_admin_setting(public_plugin_url=public_url)
            flask.flash('Public website connector started. Use the HTTPS plugin URL shown below after Cloudflare reports the tunnel healthy.', category='success')
        except subprocess.TimeoutExpired:
            update_admin_setting(public_plugin_url=public_url)
            flask.flash('Public website connector is still starting in the background. Use the HTTPS plugin URL once Cloudflare shows the tunnel healthy.', category='info')
        except Exception as e:
            flask.flash(f'Failed to start public website connector: {e}', category='error')
        return redirect(url_for('admin_settings'))

    if nvidia_form.submit_check_nvidia.data and nvidia_form.validate():
        if gpu_ready:
            flask.flash('A GPU is already usable by Hashcat, so NVIDIA driver installation was skipped.', category='info')
        else:
            try:
                subprocess.Popen(["sudo", get_management_script_path("install_nvidia_drivers.sh")])
                flask.flash('NVIDIA driver check started in the background. If drivers are missing, the installer will try to add them. A reboot may still be required before the GPU appears.', category='success')
            except Exception as e:
                flask.flash(f'Failed to start NVIDIA driver check: {e}', category='error')
        return redirect(url_for('admin_settings'))

    if update_form.submit_update.data and update_form.validate():
        try:
            write_progress_snapshot("update", "running", 1, "Starting the application update")
            subprocess.Popen(["sudo", get_management_script_path("update_app.sh")])
            return redirect(url_for('update_wait'))
        except Exception as e:
            try:
                write_progress_snapshot("update", "failed", 0, f"Failed to start update script: {e}")
            except Exception:
                pass
            flask.flash(f'Failed to start update script: {e}', category='error')
        return redirect(url_for('admin_settings'))

    if uninstall_form.submit_uninstall.data and uninstall_form.validate():
        try:
            subprocess.Popen(["sudo", get_management_script_path("uninstall_app.sh")])
            flask.flash('App uninstallation process started! The web server will be permanently deleted and go offline in 5 seconds.', category='danger')
        except Exception as e:
            flask.flash(f'Failed to start uninstall script: {e}', category='error')
        return redirect(url_for('admin_settings'))

    if autostart_form.submit_enable_autostart.data and autostart_form.validate():
        try:
            command = ["sudo", get_management_script_path("autostart_service.sh"), "enable"]
            subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
            flask.flash('Autostart enabled. The server will now start automatically on boot.', category='success')
        except subprocess.CalledProcessError as e:
            message = (e.stderr or e.stdout or str(e)).strip()
            flask.flash(f'Failed to enable autostart: {message}', category='error')
        except Exception as e:
            flask.flash(f'Failed to enable autostart: {e}', category='error')
        return redirect(url_for('admin_settings'))

    if autostart_form.submit_disable_autostart.data and autostart_form.validate():
        try:
            command = ["sudo", get_management_script_path("autostart_service.sh"), "disable"]
            subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
            flask.flash('Autostart disabled. The server will no longer start automatically on boot.', category='success')
        except subprocess.CalledProcessError as e:
            message = (e.stderr or e.stdout or str(e)).strip()
            flask.flash(f'Failed to disable autostart: {message}', category='error')
        except Exception as e:
            flask.flash(f'Failed to disable autostart: {e}', category='error')
        return redirect(url_for('admin_settings'))
    
    # Ensure we always have a valid dictionary even if keys are ints
    settings = read_settings()
    raw_intensities = settings.get("device_intensities", {})
    # Normalize keys to strings for Jinja2 consistency
    device_intensities = {str(k): v for k, v in raw_intensities.items()}

    if request.method == 'GET':
        form.cpu_percent.data = settings.get('cpu_percent', 100)
        form.temp_resume_delta.data = settings.get('temp_resume_delta', 5)
        form.max_job_time_minutes.data = settings.get('max_job_time_minutes')
        form.use_spare_devices_for_queue.data = bool(settings.get('use_spare_devices_for_queue', False))
        account_form.new_username.data = current_user.username

    autostart_status = get_autostart_status()
    update_status, update_summary, update_log_excerpt = get_update_status()
    install_progress = get_install_progress()
    tailscale_snapshot = get_tailscale_snapshot()
    cloudflare_snapshot = get_cloudflare_snapshot()
        
    return render_template('settings.html', title='Admin Settings', form=form, ts_form=ts_form, 
                           public_form=public_form,
                           update_form=update_form, uninstall_form=uninstall_form,
                           devices=devices, device_intensities=device_intensities,
                           account_form=account_form, autostart_form=autostart_form,
                           nvidia_form=nvidia_form, gpu_visible=gpu_visible, gpu_ready=gpu_ready,
                           autostart_status=autostart_status, update_status=update_status,
                           update_summary=update_summary, update_log_excerpt=update_log_excerpt,
                           install_progress=install_progress, tailscale_snapshot=tailscale_snapshot,
                           cloudflare_snapshot=cloudflare_snapshot)


@app.route('/tailscale_status')
@login_required
@roles_required(RoleEnum.ADMIN)
def tailscale_status():
    return jsonify(get_tailscale_snapshot())


@app.route('/cloudflare_status')
@login_required
@roles_required(RoleEnum.ADMIN)
def cloudflare_status():
    return jsonify(get_cloudflare_snapshot())


@app.route('/api/stats')
@login_required
def api_stats():
    from app.uploader import UploadedTask
    from app.login import User
    from app.utils.settings import read_settings
    from app.utils.utils import get_hashcat_devices, get_live_usage
    
    settings = read_settings()
    devices = get_hashcat_devices()
    device_intensities = settings.get("device_intensities", {})
    live_usage = get_live_usage()
    
    stats = {
        'total_handshakes': UploadedTask.query.count(),
        'total_cracked': UploadedTask.query.filter(UploadedTask.found_key.is_not(None)).count(),
        'total_failed': UploadedTask.query.filter(UploadedTask.completed == True, UploadedTask.found_key.is_(None)).count(),
        'total_active': UploadedTask.query.filter(active_task_filter()).count(),
        'total_users': User.query.count(),
        'cpu_limit': settings.get('cpu_percent', 100),
        'devices': devices,
        'device_intensities': device_intensities,
        'live_usage': live_usage
    }
    return jsonify(stats)

@app.route('/admin/users')
@login_required
@roles_required(RoleEnum.ADMIN)
def admin_users():
    users = User.query.all()
    return render_template('admin_users.html', title='User Management', users=users)


@app.route('/admin/edit_user/<int:user_id>', methods=['GET', 'POST'])
@login_required
@roles_required(RoleEnum.ADMIN)
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    if user_id == current_user.id:
        flask.flash('Use the settings page to edit your own administrator account.', category='info')
        return redirect(url_for('admin_settings'))
    if user.username == 'guest':
        flask.flash('The guest account is protected and cannot be edited here.', category='error')
        return redirect(url_for('admin_users'))

    form = EditUserForm()
    form.roles.choices = [(role.name.value, role.name.value) for role in Role.query.order_by(Role.id).all()]

    if form.validate_on_submit():
        existing_user = User.query.filter_by(username=form.username.data).first()
        if existing_user and existing_user.id != user.id:
            flask.flash('Username already exists.', category='error')
        elif not form.roles.data:
            flask.flash('Please select at least one role.', category='error')
        else:
            user.username = form.username.data
            if form.new_password.data:
                user.set_password(form.new_password.data)
            user.roles = [Role.by_enum(RoleEnum(role_name)) for role_name in form.roles.data]
            db.session.commit()
            flask.flash(f"User '{user.username}' updated successfully.", category='success')
            return redirect(url_for('admin_users'))

    if request.method == 'GET':
        form.username.data = user.username
        form.roles.data = [role.name.value for role in user.roles]

    return render_template('admin_edit_user.html', title='Edit User', form=form, managed_user=user)

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@login_required
@roles_required(RoleEnum.ADMIN)
def delete_user(user_id):
    from app.uploader import UploadedTask

    if user_id == current_user.id:
        flask.flash('You cannot delete your own account!', category='error')
        return redirect(url_for('admin_users'))
    
    user = User.query.get_or_404(user_id)
    if user.username == 'guest':
        flask.flash('The guest account is protected.', category='error')
        return redirect(url_for('admin_users'))

    UploadedTask.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    flask.flash(f'User {user.username} has been deleted.', category='success')
    return redirect(url_for('admin_users'))

def should_run_startup_maintenance():
    if os.environ.get("HASHCAT_WPA_SKIP_STARTUP_MAINTENANCE") == "1":
        return False
    executable = Path(sys.argv[0]).name.lower()
    argv = " ".join(sys.argv).lower()
    return executable in {"gunicorn", "flask"} or "flask" in argv


def restore_interrupted_jobs():
    interrupted = UploadedTask.query.filter_by(completed=False).order_by(UploadedTask.uploaded_time.asc()).all()
    if not interrupted:
        return

    # A crashed/restarted web worker can leave hashcat running without an in-memory
    # ProgressLock. Stop those orphaned processes first so restore/restart owns the
    # task state again instead of duplicating work.
    hashcat_worker.terminate()

    restored = 0
    restarted = 0
    for task in interrupted:
        state = read_recovery_state(task.id)
        if state:
            try:
                hashcat_worker.submit_recovery(task, state)
                restored += 1
                continue
            except Exception as error:
                logger.exception("Could not restore task %s: %s", task.id, error)
                task.status = TaskInfoStatus.ABORTED
                task.completed = True
                continue

        try:
            hashcat_worker.submit_capture(resolve_task_attack_file(task), uploaded_form=form_for_stored_task(task), task=task)
            task.status = TaskInfoStatus.SCHEDULED
            task.completed = False
            restarted += 1
        except Exception as error:
            logger.exception("Could not restart queued task %s: %s", task.id, error)
            task.status = TaskInfoStatus.ABORTED
            task.completed = True

    if restored or restarted:
        db.session.commit()
        logger.info("Queued %s interrupted hashcat job(s) for restore and %s queued job(s) for restart.", restored, restarted)


if should_run_startup_maintenance():
    with app.app_context():
        create_first_users()
        ensure_upload_queue_position_column()
        check_incomplete_tasks()
        backward_db_compatibility()
        restore_interrupted_jobs()
