import datetime
from pathlib import Path

from flask_uploads import UploadSet, configure_uploads
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileRequired
from werkzeug.datastructures import FileStorage
from wtforms.fields import MultipleFileField
from wtforms.validators import StopValidation
from wtforms.fields import RadioField, SubmitField, BooleanField, IntegerField
from wtforms.validators import Optional, ValidationError, NumberRange
from sqlalchemy import inspect, text

from app.config import WORDLISTS_USER_DIR
from app import app, db
from app.domain import Rule, NONE_STR, TaskInfoStatus, Workload, HashcatMode, BrainClientFeature
from app.utils import read_hashcat_brain_password, normalize_stored_capture_filename, resolve_existing_capture_path
from app.word_magic.wordlist import estimate_runtime_fmt, wordlist_choices, find_wordlist_by_path, is_wordlist_script


def _path_is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_server_wordlist_path(raw_value: str):
    if raw_value in (None, NONE_STR):
        return

    wordlist_path = Path(str(raw_value)).expanduser()
    if not wordlist_path.is_absolute():
        raise ValidationError("Use an absolute server-side wordlist path.")
    if not wordlist_path.exists() or not wordlist_path.is_file():
        raise ValidationError("Server-side wordlist path does not exist.")
    if is_wordlist_script(wordlist_path) and not _path_is_inside(wordlist_path, WORDLISTS_USER_DIR):
        raise ValidationError("Wordlist generator scripts must live in the user wordlists folder.")


class ServerWordlistRadioField(RadioField):
    def pre_validate(self, form):
        try:
            super().pre_validate(form)
        except ValidationError:
            validate_server_wordlist_path(self.data)


def ensure_upload_queue_position_column():
    inspector = inspect(db.engine)
    columns = {column["name"] for column in inspector.get_columns(UploadedTask.__tablename__)}
    if "queue_position" not in columns:
        db.session.execute(text("ALTER TABLE uploads ADD COLUMN queue_position INTEGER"))
        db.session.commit()
    if "workload" not in columns:
        db.session.execute(
            text(f"ALTER TABLE uploads ADD COLUMN workload TEXT DEFAULT '{Workload.Normal.value}'")
        )
        db.session.commit()


def check_incomplete_tasks():
    for task in UploadedTask.query.filter_by(completed=False):
        task.status = TaskInfoStatus.SCHEDULED
        task.completed = False
    db.session.commit()


def backward_db_compatibility():
    next_position = 1
    ordered_tasks = UploadedTask.query.order_by(UploadedTask.uploaded_time.asc(), UploadedTask.id.asc()).all()
    for task in ordered_tasks:
        if task.queue_position is None:
            task.queue_position = next_position
        next_position = max(next_position, (task.queue_position or 0) + 1)

    for task in UploadedTask.query.filter(UploadedTask.status.startswith("InterruptedError('Cancelled'")):
        task.status = TaskInfoStatus.CANCELLED
    for task in UploadedTask.query.filter(UploadedTask.filename.is_not(None)):
        normalized = normalize_stored_capture_filename(task.filename)
        if task.filename != normalized:
            task.filename = normalized
        if task.completed and str(task.status).startswith("FileNotFoundError("):
            resolved_capture = resolve_existing_capture_path(task.filename)
            if resolved_capture.exists():
                task.status = TaskInfoStatus.SCHEDULED
                task.completed = False
    db.session.commit()


class PwnagotchiStatus(db.Model):
    __tablename__ = "pwnagotchi_status"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, index=True, nullable=False)
    hostname = db.Column(db.Text)
    plugin_version = db.Column(db.Text)
    last_event = db.Column(db.Text)
    last_message = db.Column(db.Text)
    last_seen = db.Column(db.DateTime, default=datetime.datetime.now, index=True)
    last_upload_at = db.Column(db.DateTime)
    last_upload_filename = db.Column(db.Text)
    upload_count = db.Column(db.Integer, default=0)

    def mark_seen(self, *, event: str, message: str | None = None, hostname: str | None = None,
                  plugin_version: str | None = None, upload_filename: str | None = None):
        self.last_event = event
        self.last_message = message
        self.hostname = hostname or self.hostname
        self.plugin_version = plugin_version or self.plugin_version
        self.last_seen = datetime.datetime.now()
        if upload_filename:
            self.last_upload_at = self.last_seen
            self.last_upload_filename = upload_filename
            self.upload_count = (self.upload_count or 0) + 1

    @property
    def is_online(self):
        if not self.last_seen:
            return False
        return (datetime.datetime.now() - self.last_seen) <= datetime.timedelta(minutes=15)


class UploadedTask(db.Model):
    __tablename__ = "uploads"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    filename = db.Column(db.Text)
    wordlist = db.Column(db.Text)
    rule = db.Column(db.Text)
    hashcat_args = db.Column(db.Text, default='')
    workload = db.Column(db.Text, default=Workload.Normal.value)
    uploaded_time = db.Column(db.DateTime, index=True, default=datetime.datetime.now)
    duration = db.Column(db.Interval, default=datetime.timedelta)
    queue_position = db.Column(db.Integer, index=True)
    status = db.Column(db.Text, default=TaskInfoStatus.SCHEDULED)
    found_key = db.Column(db.Text)
    completed = db.Column(db.Boolean, default=False)
    essid = db.Column(db.Text)
    bssid = db.Column(db.String(64))


from wtforms import SelectMultipleField, widgets

class MultiCheckboxField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


class MultipleFilesRequired(FileRequired):
    def __call__(self, form, field):
        files = [upload for upload in (field.data or []) if isinstance(upload, FileStorage) and upload.filename]
        if not files:
            raise StopValidation(self.message or field.gettext("This field is required."))


class MultipleFilesAllowed(FileAllowed):
    def __call__(self, form, field):
        files = [upload for upload in (field.data or []) if isinstance(upload, FileStorage) and upload.filename]
        for upload in files:
            filename = upload.filename.lower()
            if isinstance(self.upload_set, tuple) or isinstance(self.upload_set, list) or isinstance(self.upload_set, set):
                if any(filename.endswith("." + extension) for extension in self.upload_set):
                    continue
                raise StopValidation(
                    self.message
                    or field.gettext("File does not have an approved extension: {extensions}").format(
                        extensions=", ".join(self.upload_set)
                    )
                )
            if not self.upload_set.file_allowed(upload, filename):
                raise StopValidation(self.message or field.gettext("File does not have an approved extension."))

class UploadForm(FlaskForm):
    wordlist = ServerWordlistRadioField('Wordlist', choices=wordlist_choices(), default=NONE_STR, description="The higher the rate, the better")
    rule = RadioField('Rule', choices=Rule.to_form(), default=NONE_STR)
    timeout = IntegerField('Timeout in minutes, optional', validators=[Optional(), NumberRange(min=1)])
    workload = RadioField("Work Mode", choices=Workload.to_form(), default=Workload.Normal.value,
                          description="Normal runs the full cracking chain. Rainbow builds an ESSID-specific WPA PMK cache from previously cracked passwords.")
    brain = BooleanField("Hashcat Brain", default=False, description="Hashcat Brain skips already tried password candidates")
    brain_client_feature = RadioField("Brain client features", choices=BrainClientFeature.to_form(),
                                      default=BrainClientFeature.POSITIONS.value)
    devices = MultiCheckboxField("Target Devices", choices=[])
    capture = MultipleFileField(
        'Captures',
        validators=[MultipleFilesRequired(), MultipleFilesAllowed(HashcatMode.valid_upload_suffixes(),
                                                                  message='Airodump & Hashcat capture files only')],
        render_kw={"accept": ".cap,.pcap,.pcapng,.hccapx,.pmkid,.2500,.2501,.16800,.16801,.22000,.22001"}
    )
    submit = SubmitField('Submit')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.wordlist.choices = wordlist_choices()
        from app.utils.utils import get_hashcat_devices
        try:
            detected = get_hashcat_devices()
            self.devices.choices = [(d['id'], f"{d['name']} ({d['memory']})") for d in detected]
            detected_ids = [str(d["id"]) for d in detected if str(d.get("id", "")).isdigit()]
            # Default to settings-defined devices
            if not self.devices.data:
                from app.utils.settings import read_settings
                configured_defaults = [str(device_id) for device_id in read_settings().get("default_devices", [])]
                selected_defaults = [device_id for device_id in configured_defaults if device_id in detected_ids]
                self.devices.data = selected_defaults or detected_ids
        except Exception:
            self.devices.choices = []

    def get_wordlist_path(self):
        if self.wordlist.data == NONE_STR:
            return None
        return Path(self.wordlist.data)

    def get_wordlist_name(self):
        wordlist_path = self.get_wordlist_path()
        wordlist = find_wordlist_by_path(wordlist_path)
        if wordlist is None:
            return None
        if not wordlist.custom:
            return str(wordlist_path)
        return wordlist.name

    def get_rule(self):
        return Rule.from_data(self.rule.data)

    @property
    def runtime(self):
        runtime = estimate_runtime_fmt(wordlist_path=self.get_wordlist_path(), rule=self.get_rule())
        return runtime

    def hashcat_args(self, secret=False):
        hashcat_args = []
        if self.devices.data:
            # -d X,Y,Z
            hashcat_args.append("-d")
            hashcat_args.append(",".join(self.devices.data))
            
        if self.brain.data:
            hashcat_args.append("--brain-client")
            hashcat_args.append(f"--brain-client-features={self.brain_client_feature.data}")
            if secret:
                hashcat_args.append(f"--brain-password={read_hashcat_brain_password()}")
        return hashcat_args


cap_uploads = UploadSet(
    name='files',
    extensions=HashcatMode.valid_upload_suffixes(),
    default_dest=lambda app: str(app.config['CAPTURES_DIR'])
)
configure_uploads(app, cap_uploads)
