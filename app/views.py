import shlex
import subprocess
from http import HTTPStatus
from pathlib import Path
from threading import Thread

import flask
from flask import request, render_template, redirect, url_for
from flask.json import jsonify
from flask_login import login_user, logout_user, login_required, current_user

from app import app, db
from app.attack.convert import split_by_essid, convert_to_22000
from app.attack.worker import HashcatWorker
from app.domain import TaskInfoStatus, Rule, InvalidFileError
from app.logger import logger
from app.login import LoginForm, RegistrationForm, User, RoleEnum, register_user, create_first_users, Role, \
    roles_required, user_has_roles
from app.uploader import cap_uploads, UploadForm, UploadedTask, check_incomplete_tasks, backward_db_compatibility
from app.utils.file_io import read_last_benchmark, bssid_essid_from_22000
from app.utils.utils import is_safe_url, hashcat_devices_info
from app.word_magic import create_digits_wordlist, estimate_runtime_fmt, create_fast_wordlists
from app.word_magic.wordlist import download_wordlist, find_wordlist_by_name

hashcat_worker = HashcatWorker(app)


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
        'total_cracked': UploadedTask.query.filter_by(status='cracked').count(),
        'total_failed': UploadedTask.query.filter(UploadedTask.completed == True, UploadedTask.found_key.is_(None)).count(),
        'total_active': UploadedTask.query.filter(UploadedTask.status.in_(['Running', 'Scheduled'])).count(),
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


def decode_task_essid(file_22000: Path):
    bssid_essid = next(bssid_essid_from_22000(file_22000))
    bssid, essid_hex = bssid_essid.split(':')
    essid = bytes.fromhex(essid_hex).decode('utf-8')
    return bssid, essid

@app.route('/pwnagotchi')
def pwnagotchi():
    return render_template('pwnagotchi.html', title='Pwnagotchi Integration')

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
        # flask-uploads already uses werkzeug.secure_filename()
        filename = cap_uploads.save(request.files['capture'], folder=current_user.username)
        cap_path = Path(app.config['CAPTURES_DIR']) / filename
        cap_path = Path(shlex.quote(str(cap_path)))
        try:
            file_22000 = convert_to_22000(cap_path)
        except (FileNotFoundError, InvalidFileError) as error:
            logger.exception(error)
            return flask.abort(HTTPStatus.BAD_REQUEST, description=str(error))
        Thread(target=download_wordlist, args=(form.get_wordlist_path(),)).start()
        folder_split_by_essid = split_by_essid(file_22000)
        tasks = {}
        hashcat_args = ' '.join(form.hashcat_args())
        for file_essid in folder_split_by_essid.iterdir():
            bssid_essid = next(bssid_essid_from_22000(file_essid))
            bssid, essid = bssid_essid.split(':')
            essid = bytes.fromhex(essid).decode('utf-8')
            new_task = UploadedTask(user_id=current_user.id, filename=cap_path.name, wordlist=form.get_wordlist_name(),
                                    rule=form.rule.data, bssid=bssid, essid=essid, hashcat_args=hashcat_args)
            tasks[file_essid] = new_task
        db.session.add_all(tasks.values())
        db.session.commit()
        for file_essid, task in tasks.items():
            hashcat_worker.submit_capture(file_essid, uploaded_form=form, task=task)
        flask.flash(f"Uploaded {filename}")
        return redirect(url_for('user_profile'))
    return render_template('upload.html', title='Upload', form=form)

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
    
    if 'capture' not in request.files:
        return flask.abort(HTTPStatus.BAD_REQUEST, description="Missing capture file")
        
    # Disable CSRF for this API endpoint
    form = UploadForm(meta={'csrf': False})
    if not form.validate():
        return flask.abort(HTTPStatus.BAD_REQUEST, description=str(form.errors))
        
    filename = cap_uploads.save(request.files['capture'], folder=user.username)
    cap_path = Path(app.config['CAPTURES_DIR']) / filename
    cap_path = Path(shlex.quote(str(cap_path)))
    try:
        file_22000 = convert_to_22000(cap_path)
    except (FileNotFoundError, InvalidFileError) as error:
        logger.exception(error)
        return flask.abort(HTTPStatus.BAD_REQUEST, description=str(error))
        
    Thread(target=download_wordlist, args=(form.get_wordlist_path(),)).start()
    folder_split_by_essid = split_by_essid(file_22000)
    tasks = {}
    hashcat_args = ' '.join(form.hashcat_args())
    for file_essid in folder_split_by_essid.iterdir():
        bssid_essid = next(bssid_essid_from_22000(file_essid))
        bssid, essid = bssid_essid.split(':')
        essid = bytes.fromhex(essid).decode('utf-8')
        new_task = UploadedTask(user_id=user.id, filename=cap_path.name, wordlist=form.get_wordlist_name(),
                                rule=form.rule.data, bssid=bssid, essid=essid, hashcat_args=hashcat_args)
        tasks[file_essid] = new_task
    db.session.add_all(tasks.values())
    db.session.commit()
    for file_essid, task in tasks.items():
        hashcat_worker.submit_capture(file_essid, uploaded_form=form, task=task)
        
    return jsonify({"status": "success", "message": f"Uploaded {filename} with tasks scheduled."})



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
        tasks = UploadedTask.query.order_by(UploadedTask.uploaded_time.desc()).all()
    else:
        tasks = current_user.uploads[::-1]
    return render_template('user_profile.html', title='Home', tasks=tasks,
                           benchmark=read_last_benchmark(), devices=hashcat_devices_info(), progress=progress())


@app.route('/progress')
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
    base_file = Path(app.config['CAPTURES_DIR']) / task.filename
    
    if file_type == 'capture':
        p = base_file
    elif file_type == 'result':
        p = base_file.with_suffix('.key')
    else:
        return flask.abort(HTTPStatus.BAD_REQUEST)
        
    if not p.exists():
        return flask.abort(HTTPStatus.NOT_FOUND, description=f"The requested {file_type} file could not be found.")
        
    return flask.send_file(str(p), as_attachment=True)


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
        output.write(f"{task.essid} | {task.bssid} | {task.found_key}\n")
        
    # Seek to beginning to read
    output.seek(0)
    
    return flask.Response(
        output.getvalue(),
        mimetype="text/plain",
        headers={"Content-disposition": f"attachment; filename=cracked_passwords_{current_user.username}.txt"}
    )


@app.route('/login', methods=['GET', 'POST'])
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
    task = UploadedTask.query.get(task_id)
    if task is None:
        return flask.Response(status=HTTPStatus.BAD_REQUEST)
    if task.user_id != current_user.id:
        return flask.Response(status=HTTPStatus.FORBIDDEN)
    if hashcat_worker.cancel(task.id):
        return jsonify(TaskInfoStatus.CANCELLED)
    else:
        return jsonify("Cancelling...")


@app.route("/requeue/<int:task_id>")
@login_required
def requeue(task_id):
    from types import SimpleNamespace

    task = UploadedTask.query.get_or_404(task_id)
    if not user_has_roles(current_user, RoleEnum.ADMIN) and task.user_id != current_user.id:
        return flask.abort(HTTPStatus.FORBIDDEN, description="You do not have permission to re-queue this task.")

    if not task.completed:
        flask.flash("This task is still running. Cancel it first if you want to restart it.", category="info")
        return redirect(url_for('user_profile'))

    capture_path = Path(app.config['CAPTURES_DIR']) / task.filename
    if not capture_path.exists():
        flask.flash("The original capture file could not be found, so this task cannot be re-queued.", category="error")
        return redirect(url_for('user_profile'))

    try:
        file_22000 = convert_to_22000(capture_path)
        folder_split_by_essid = split_by_essid(file_22000)

        matched_file = None
        for file_essid in folder_split_by_essid.iterdir():
            bssid, essid = decode_task_essid(file_essid)
            if bssid == task.bssid and essid == task.essid:
                matched_file = file_essid
                break

        if matched_file is None:
            raise InvalidFileError("Could not match the original ESSID/BSSID pair in the capture file.")

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

        requeue_form = SimpleNamespace(
            timeout=SimpleNamespace(data=None),
            workload=SimpleNamespace(data="2"),
            get_wordlist_path=lambda: wordlist_path,
            get_rule=lambda: rule,
            hashcat_args=lambda secret=False: list(filtered_hashcat_args)
        )

        new_task = UploadedTask(
            user_id=task.user_id,
            filename=task.filename,
            wordlist=task.wordlist,
            rule=task.rule,
            bssid=task.bssid,
            essid=task.essid,
            hashcat_args=' '.join(split_hashcat_args(task.hashcat_args))
        )
        db.session.add(new_task)
        db.session.commit()

        hashcat_worker.submit_capture(matched_file, uploaded_form=requeue_form, task=new_task)
        flask.flash(f"Task #{task.id} was re-queued as task #{new_task.id}.", category="success")
    except (FileNotFoundError, InvalidFileError, ValueError) as error:
        logger.exception(error)
        flask.flash(f"Failed to re-queue task #{task.id}: {error}", category="error")

    return redirect(url_for('user_profile'))


@app.route('/terminate')
@login_required
@roles_required(RoleEnum.ADMIN)
def terminate():
    hashcat_worker.terminate()
    return jsonify("Terminated all jobs")


@app.route('/hashcat.potfile')
@login_required
@roles_required(RoleEnum.ADMIN)
def hashcat_potfile():
    hashcat_potfile = Path.home() / ".hashcat" / "hashcat.potfile"
    if hashcat_potfile.exists():
        return hashcat_potfile.read_text()
    return jsonify("Empty hashcat.potfile")

from flask_wtf import FlaskForm
from wtforms import IntegerField, SubmitField, PasswordField, StringField
from wtforms.validators import DataRequired, NumberRange, EqualTo
from app.utils.settings import read_settings, write_settings

from wtforms import StringField

from wtforms import SelectMultipleField, widgets

class MultiCheckboxField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()

class SettingsForm(FlaskForm):
    cpu_percent = IntegerField('Global CPU Thread Limit (%)', validators=[DataRequired(), NumberRange(min=1, max=100)], description="Limit total CPU threads for host operations.")
    gpu_temp_limit = IntegerField('GPU Max Temp (°C)', validators=[DataRequired(), NumberRange(min=50, max=100)], default=90, description="Hashcat will abort if GPU exceeds this temperature.")
    cpu_temp_limit = IntegerField('CPU Max Temp (°C)', validators=[DataRequired(), NumberRange(min=50, max=100)], default=90, description="Server will pause jobs if CPU exceeds this temperature.")
    default_devices = MultiCheckboxField('Default Devices (for Pwnagotchi/API)', choices=[])
    submit = SubmitField('Save Performance Settings')

class TailscaleForm(FlaskForm):
    auth_key = StringField('Tailscale Auth Key', validators=[DataRequired()])
    submit_tailscale = SubmitField('Connect Tailscale')

class UpdateAppForm(FlaskForm):
    submit_update = SubmitField('Update App & Restart')

class UninstallAppForm(FlaskForm):
    submit_uninstall = SubmitField('Permanently Uninstall Server')

class AutostartForm(FlaskForm):
    submit_enable_autostart = SubmitField('Enable Autostart')
    submit_disable_autostart = SubmitField('Disable Autostart')

class AccountSettingsForm(FlaskForm):
    new_username = StringField('Update Username', validators=[DataRequired()])
    new_password = PasswordField('New Password (leave blank to keep current)')
    confirm_password = PasswordField('Confirm New Password', validators=[EqualTo('new_password', message='Passwords must match')])
    submit_account = SubmitField('Update Account')


class EditUserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    new_password = PasswordField('New Password (leave blank to keep current)')
    confirm_password = PasswordField('Confirm New Password', validators=[EqualTo('new_password', message='Passwords must match')])
    roles = MultiCheckboxField('Roles', choices=[])
    submit_user = SubmitField('Save User Changes')

@app.route('/settings', methods=['GET', 'POST'])
@login_required
@roles_required(RoleEnum.ADMIN)
def admin_settings():
    form = SettingsForm()
    ts_form = TailscaleForm()
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
            default_devices=form.default_devices.data
        )
        flask.flash('Performance settings updated successfully!')
        return redirect(url_for('admin_settings'))
    
    # Populate form from current settings
    form.cpu_percent.data = settings.get("cpu_percent", 100)
    form.gpu_temp_limit.data = settings.get("gpu_temp_limit", 90)
    form.cpu_temp_limit.data = settings.get("cpu_temp_limit", 90)
    form.default_devices.data = settings.get("default_devices", ["1"])
        
    if ts_form.submit_tailscale.data and ts_form.validate():
        import subprocess
        try:
            subprocess.Popen(["sudo", "/opt/hashcat-wpa-server/bash/install_tailscale.sh", ts_form.auth_key.data])
            flask.flash('Tailscale connection initiated in the background! Check your Tailscale admin console.', category='success')
        except Exception as e:
            flask.flash(f'Failed to run Tailscale securely: {e}', category='error')
        return redirect(url_for('admin_settings'))

    if update_form.submit_update.data and update_form.validate():
        import subprocess
        try:
            subprocess.Popen(["sudo", "/opt/hashcat-wpa-server/bash/update_app.sh"])
            flask.flash('🚀 Update initiated! The system is now downloading the latest version and rebuilding the package in the background. The server will automatically restart and be back online in roughly 60 seconds.', category='success')
        except Exception as e:
            flask.flash(f'Failed to start update script: {e}', category='error')
        return redirect(url_for('admin_settings'))

    if uninstall_form.submit_uninstall.data and uninstall_form.validate():
        import subprocess
        try:
            subprocess.Popen(["sudo", "/opt/hashcat-wpa-server/bash/uninstall_app.sh"])
            flask.flash('App uninstallation process started! The web server will be permanently deleted and go offline in 5 seconds.', category='danger')
        except Exception as e:
            flask.flash(f'Failed to start uninstall script: {e}', category='error')
        return redirect(url_for('admin_settings'))

    if autostart_form.submit_enable_autostart.data and autostart_form.validate():
        try:
            subprocess.run(
                ["sudo", get_management_script_path("autostart_service.sh"), "enable"],
                capture_output=True,
                text=True,
                timeout=15,
                check=True
            )
            flask.flash('Autostart enabled. The server will now start automatically on boot.', category='success')
        except subprocess.CalledProcessError as e:
            message = (e.stderr or e.stdout or str(e)).strip()
            flask.flash(f'Failed to enable autostart: {message}', category='error')
        except Exception as e:
            flask.flash(f'Failed to enable autostart: {e}', category='error')
        return redirect(url_for('admin_settings'))

    if autostart_form.submit_disable_autostart.data and autostart_form.validate():
        try:
            subprocess.run(
                ["sudo", get_management_script_path("autostart_service.sh"), "disable"],
                capture_output=True,
                text=True,
                timeout=15,
                check=True
            )
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
        account_form.new_username.data = current_user.username

    autostart_status = get_autostart_status()
        
    return render_template('settings.html', title='Admin Settings', form=form, ts_form=ts_form, 
                           update_form=update_form, uninstall_form=uninstall_form,
                           devices=devices, device_intensities=device_intensities,
                           account_form=account_form, autostart_form=autostart_form,
                           autostart_status=autostart_status)


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
        'total_active': UploadedTask.query.filter(UploadedTask.completed == False).count(),
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

with app.app_context():
    create_first_users()
    check_incomplete_tasks()
    backward_db_compatibility()
