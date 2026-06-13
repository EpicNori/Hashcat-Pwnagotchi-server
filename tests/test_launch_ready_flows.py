import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import Text

_TEST_HOME = Path(tempfile.mkdtemp(prefix="hashcat-wpa-server-tests-"))
os.environ["HASHCAT_WPA_SERVER_HOME"] = str(_TEST_HOME)
os.environ["HASHCAT_WPA_SKIP_STARTUP_MAINTENANCE"] = "1"
_TEST_HOME.mkdir(parents=True, exist_ok=True)
(_TEST_HOME / "benchmark.csv").write_text("test,0\n", encoding="utf-8")

from app import app, db
from app.config import ADMIN_SETTINGS_PATH
from app.domain import NONE_STR, Workload
from app.login import Role, RoleEnum, User, create_first_users, user_has_roles
from app.uploader import PwnagotchiStatus, UploadedTask
from app.utils.settings import read_settings
from app.utils.file_io import (
    build_pmk_rainbow_cache,
    extract_passwords_from_found_key,
    read_plain_key,
    resolve_pmk_rainbow_password,
)
import app.views as views


def basic_auth(username="admin", password="changeme"):
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


class FakeHashcatWorker:
    def __init__(self):
        self.submitted = []
        self.locks = {}
        self.locks_onetime = set()

    def submit_capture(self, file_22000, uploaded_form, task):
        self.submitted.append({
            "path": Path(file_22000),
            "task_id": task.id,
            "essid": task.essid,
            "bssid": task.bssid,
            "workload": uploaded_form.workload.data,
            "wordlist_path": str(uploaded_form.get_wordlist_path()) if uploaded_form.get_wordlist_path() else None,
            "hashcat_args": uploaded_form.hashcat_args(secret=False),
            "hashcat_args_secret": uploaded_form.hashcat_args(secret=True),
        })


class FakeProcess:
    calls = []

    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.returncode = self.next_returncode
        self.output = self.next_output
        self.stdin = SimpleNamespace(closed=False, close=lambda: setattr(self.stdin, "closed", True))
        FakeProcess.calls.append(self)

    def communicate(self, input=None, timeout=None):
        self.input = input
        log = self.kwargs.get("stdout")
        if self.output and log is not None:
            log.write(self.output)
            log.flush()
        if self.next_timeout:
            raise views.subprocess.TimeoutExpired(self.command, timeout)
        return "", ""

    @classmethod
    def configure(cls, *, returncode=0, output="", timeout=False):
        cls.calls = []
        cls.next_returncode = returncode
        cls.next_output = output
        cls.next_timeout = timeout


FakeProcess.configure()


class LaunchReadyFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()

    def setUp(self):
        self.original_worker = views.hashcat_worker
        self.fake_worker = FakeHashcatWorker()
        views.hashcat_worker = self.fake_worker

        with app.app_context():
            db.session.remove()
            db.drop_all()
            create_first_users()
        ADMIN_SETTINGS_PATH.unlink(missing_ok=True)

        self.client = app.test_client()

    def tearDown(self):
        views.hashcat_worker = self.original_worker
        with app.app_context():
            db.session.remove()

    def login_admin(self):
        with app.app_context():
            user = User.query.filter_by(username="admin").first()
            self.assertIsNotNone(user)
            user_id = str(user.id)
        with self.client.session_transaction() as session:
            session["_user_id"] = user_id
            session["_fresh"] = True

    def test_bootstrap_does_not_recreate_default_admin_after_rename(self):
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            self.assertIsNotNone(admin)
            admin.username = "renamed-admin"
            db.session.commit()

            create_first_users()

            self.assertIsNone(User.query.filter_by(username="admin").first())
            renamed = User.query.filter_by(username="renamed-admin").first()
            self.assertIsNotNone(renamed)
            self.assertTrue(user_has_roles(renamed, RoleEnum.ADMIN, RoleEnum.USER))

    def test_bootstrap_promotes_existing_named_user_without_resetting_password(self):
        original_password = "A1!-" + ("existing-pass-" * 40)
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            self.assertIsNotNone(admin)
            admin.roles = []
            admin.set_password(original_password)
            db.session.commit()

            create_first_users()

            admin = User.query.filter_by(username="admin").first()
            self.assertIsNotNone(admin)
            self.assertTrue(user_has_roles(admin, RoleEnum.ADMIN, RoleEnum.USER))
            self.assertTrue(admin.verify_password(original_password))
            self.assertFalse(admin.verify_password("changeme"))

    def test_found_key_parser_preserves_password_characters_and_length(self):
        very_long_password = "Aa1!" * 300
        key_file = _TEST_HOME / "weird.key"
        key_file.write_text(
            "\n".join([
                "hashA:  leading and trailing  ",
                "hashB:colon:inside:password",
                f"hashC:{very_long_password}",
                "hashB:colon:inside:password",
            ]) + "\n",
            encoding="utf-8",
        )

        found_key = read_plain_key(key_file)

        self.assertEqual(
            found_key,
            "\n".join([
                "hashA:  leading and trailing  ",
                "hashB:colon:inside:password",
                f"hashC:{very_long_password}",
            ]),
        )
        self.assertEqual(
            extract_passwords_from_found_key(found_key),
            ["  leading and trailing  ", "colon:inside:password", very_long_password],
        )

    def test_upload_models_do_not_cap_free_text_launch_metadata(self):
        unbounded_columns = [
            PwnagotchiStatus.__table__.c.hostname,
            PwnagotchiStatus.__table__.c.plugin_version,
            PwnagotchiStatus.__table__.c.last_event,
            PwnagotchiStatus.__table__.c.last_message,
            PwnagotchiStatus.__table__.c.last_upload_filename,
            UploadedTask.__table__.c.filename,
            UploadedTask.__table__.c.wordlist,
            UploadedTask.__table__.c.rule,
            UploadedTask.__table__.c.hashcat_args,
            UploadedTask.__table__.c.workload,
            UploadedTask.__table__.c.status,
            UploadedTask.__table__.c.essid,
            UploadedTask.__table__.c.found_key,
        ]

        for column in unbounded_columns:
            self.assertIsInstance(column.type, Text, column.name)

    def test_admin_registration_accepts_long_account_passwords(self):
        long_password = "A1!-" + ("long-password-" * 80) + "end"
        self.login_admin()

        response = self.client.post(
            "/register",
            data={
                "username": "longpass",
                "password": long_password,
                "password2": long_password,
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        with app.app_context():
            user = User.query.filter_by(username="longpass").first()
            self.assertIsNotNone(user)
            self.assertTrue(user.verify_password(long_password))

    def test_settings_account_accepts_long_special_passwords(self):
        long_password = " A1!:" + ("settings-pass-" * 160) + "\nline2: end "
        self.login_admin()

        response = self.client.post(
            "/settings",
            data={
                "new_username": "admin",
                "new_password": long_password,
                "confirm_password": long_password,
                "submit_account": "Update Account",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        with app.app_context():
            user = User.query.filter_by(username="admin").first()
            self.assertIsNotNone(user)
            self.assertTrue(user.verify_password(long_password))

    def test_admin_edit_user_accepts_long_special_passwords(self):
        long_password = " A1!:" + ("managed-pass-" * 160) + "\nline2: end "
        with app.app_context():
            managed = User(username="managed-user")
            managed.set_password("old-password")
            managed.roles = [Role.by_enum(RoleEnum.USER)]
            db.session.add(managed)
            db.session.commit()
            managed_id = managed.id

        self.login_admin()
        response = self.client.post(
            f"/admin/edit_user/{managed_id}",
            data={
                "username": "managed-user",
                "new_password": long_password,
                "confirm_password": long_password,
                "roles": [RoleEnum.USER.value],
                "submit_user": "Save User Changes",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        with app.app_context():
            user = User.query.filter_by(username="managed-user").first()
            self.assertIsNotNone(user)
            self.assertTrue(user.verify_password(long_password))

    def test_login_page_gets_are_not_post_rate_limited(self):
        original = app.config.get("RATELIMIT_ENABLED")
        app.config["RATELIMIT_ENABLED"] = True
        try:
            for _ in range(12):
                response = self.client.get("/login")
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        finally:
            app.config["RATELIMIT_ENABLED"] = original

    def test_admin_can_start_benchmark(self):
        self.login_admin()

        with mock.patch.object(views.hashcat_worker, "benchmark", return_value=None, create=True) as benchmark:
            response = self.client.get("/benchmark")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json(), "Started benchmark.")
        benchmark.assert_called_once_with()

    def test_non_admin_cannot_start_system_benchmark(self):
        with app.app_context():
            user = User(username="benchmark-user")
            user.set_password("benchmark-password")
            user.roles = [Role.by_enum(RoleEnum.USER)]
            db.session.add(user)
            db.session.commit()
            user_id = str(user.id)

        with self.client.session_transaction() as session:
            session["_user_id"] = user_id
            session["_fresh"] = True

        with mock.patch.object(views.hashcat_worker, "benchmark", return_value=None, create=True) as benchmark:
            response = self.client.get("/benchmark")

        self.assertEqual(response.status_code, 403)
        benchmark.assert_not_called()

    def test_api_upload_schedules_capture_without_running_hashcat(self):
        sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
        payload = {
            "wordlist": NONE_STR,
            "rule": NONE_STR,
            "workload": Workload.Normal.value,
            "brain_client_feature": "2",
            "capture": (io.BytesIO(sample_capture.read_bytes()), sample_capture.name),
        }

        response = self.client.post(
            "/api/upload",
            data=payload,
            headers=basic_auth(),
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["uploaded"][0]["tasks"], 1)
        self.assertEqual(len(self.fake_worker.submitted), 1)
        self.assertTrue(self.fake_worker.submitted[0]["path"].exists())

        with app.app_context():
            self.assertEqual(UploadedTask.query.count(), 1)
            status = PwnagotchiStatus.query.filter_by(username="admin").first()
            self.assertIsNotNone(status)
            self.assertEqual(status.upload_count, 1)

    def test_api_upload_accepts_cli_style_job_options(self):
        sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
        devices = [
            {"id": "1", "name": "GPU One", "memory": "8 GB", "is_gpu": True, "hashcat_usable": True},
            {"id": "2", "name": "GPU Two", "memory": "8 GB", "is_gpu": True, "hashcat_usable": True},
        ]

        with mock.patch("app.utils.utils.get_hashcat_devices", return_value=devices):
            response = self.client.post(
                "/api/upload",
                data={
                    "wordlist": NONE_STR,
                    "rule": NONE_STR,
                    "workload": Workload.Rainbow.value,
                    "brain": "y",
                    "brain_client_feature": "3",
                    "devices": ["1", "2"],
                    "capture": (io.BytesIO(sample_capture.read_bytes()), sample_capture.name),
                },
                headers=basic_auth(),
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(len(self.fake_worker.submitted), 1)
        submitted = self.fake_worker.submitted[0]
        self.assertEqual(submitted["workload"], Workload.Rainbow.value)
        self.assertEqual(submitted["hashcat_args"], [
            "-d",
            "1,2",
            "--brain-client",
            "--brain-client-features=3",
        ])
        with app.app_context():
            task = UploadedTask.query.one()
            self.assertEqual(task.workload, Workload.Rainbow.value)

    def test_api_upload_and_requeue_preserve_external_server_wordlist_path(self):
        sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
        external_wordlist = _TEST_HOME / "external wordlists" / "custom launch list.txt"
        external_wordlist.parent.mkdir(parents=True, exist_ok=True)
        external_wordlist.write_text("LaunchReadyPass123!\n", encoding="utf-8")

        response = self.client.post(
            "/api/upload",
            data={
                "wordlist": str(external_wordlist),
                "rule": NONE_STR,
                "workload": Workload.Normal.value,
                "brain_client_feature": "2",
                "capture": (io.BytesIO(sample_capture.read_bytes()), sample_capture.name),
            },
            headers=basic_auth(),
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(len(self.fake_worker.submitted), 1)
        self.assertEqual(self.fake_worker.submitted[0]["wordlist_path"], str(external_wordlist))
        with app.app_context():
            task = UploadedTask.query.one()
            self.assertEqual(task.wordlist, str(external_wordlist))
            task.completed = True
            task.found_key = None
            db.session.commit()
            task_id = task.id

        self.fake_worker.submitted.clear()
        self.login_admin()
        with mock.patch("app.views.resolve_task_attack_file", return_value=sample_capture):
            requeue_response = self.client.get(f"/requeue/{task_id}", follow_redirects=True)

        self.assertEqual(requeue_response.status_code, 200, requeue_response.get_data(as_text=True))
        self.assertEqual(len(self.fake_worker.submitted), 1)
        self.assertEqual(self.fake_worker.submitted[0]["wordlist_path"], str(external_wordlist))
        with app.app_context():
            requeued = UploadedTask.query.filter(
                UploadedTask.id != task_id,
                UploadedTask.completed == False,
            ).one()
            self.assertEqual(requeued.wordlist, str(external_wordlist))

    def test_api_upload_rejects_external_wordlist_generator_scripts(self):
        sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
        external_script = _TEST_HOME / "external wordlists" / "generate.sh"
        external_script.parent.mkdir(parents=True, exist_ok=True)
        external_script.write_text("#!/bin/sh\necho LaunchReadyPass123!\n", encoding="utf-8")

        response = self.client.post(
            "/api/upload",
            data={
                "wordlist": str(external_script),
                "rule": NONE_STR,
                "workload": Workload.Normal.value,
                "brain_client_feature": "2",
                "capture": (io.BytesIO(sample_capture.read_bytes()), sample_capture.name),
            },
            headers=basic_auth(),
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("generator scripts must live", response.get_data(as_text=True))
        self.assertEqual(len(self.fake_worker.submitted), 0)

    def test_api_upload_shortens_unsafe_long_capture_filename(self):
        sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
        long_name = "../" + ("Capture With Spaces " * 24) + ".22000"

        response = self.client.post(
            "/api/upload",
            data={
                "wordlist": NONE_STR,
                "rule": NONE_STR,
                "workload": Workload.Normal.value,
                "brain_client_feature": "2",
                "capture": (io.BytesIO(sample_capture.read_bytes()), long_name),
            },
            headers=basic_auth(),
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        stored_filename = body["uploaded"][0]["filename"]
        stored_basename = stored_filename.rsplit("/", 1)[-1]
        self.assertTrue(stored_filename.startswith("admin/"))
        self.assertNotIn("..", stored_filename)
        self.assertNotIn("\\", stored_filename)
        self.assertLessEqual(len(stored_basename), views.MAX_CAPTURE_UPLOAD_NAME_CHARS)
        self.assertTrue(stored_basename.endswith(".22000"))
        self.assertTrue(views.resolve_capture_path(stored_filename).exists())

        with app.app_context():
            task = UploadedTask.query.one()
            status = PwnagotchiStatus.query.filter_by(username="admin").first()
            self.assertEqual(task.filename, stored_filename)
            self.assertEqual(status.last_upload_filename, stored_filename)

    def test_api_upload_sanitizes_capture_folder_for_unusual_username(self):
        sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
        username = "../operator name"
        password = "A1!-" + ("folder-pass-" * 20)
        with app.app_context():
            user = User(username=username)
            user.set_password(password)
            user.roles = [Role.by_enum(RoleEnum.USER)]
            db.session.add(user)
            db.session.commit()

        response = self.client.post(
            "/api/upload",
            data={
                "wordlist": NONE_STR,
                "rule": NONE_STR,
                "workload": Workload.Normal.value,
                "brain_client_feature": "2",
                "capture": (io.BytesIO(sample_capture.read_bytes()), sample_capture.name),
            },
            headers=basic_auth(username, password),
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        stored_filename = response.get_json()["uploaded"][0]["filename"]
        stored_folder = stored_filename.split("/", 1)[0]
        self.assertNotIn("..", stored_filename)
        self.assertNotIn("\\", stored_filename)
        self.assertNotEqual(stored_folder, username)
        self.assertLessEqual(len(stored_folder), views.MAX_CAPTURE_UPLOAD_FOLDER_CHARS)
        self.assertTrue(views.resolve_capture_path(stored_filename).exists())

    def test_api_upload_accepts_long_special_basic_auth_password(self):
        sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
        long_password = " A1!:" + ("weird-pass-" * 200) + "\nline2: end "
        with app.app_context():
            user = User(username="api-long-password")
            user.set_password(long_password)
            user.roles = [Role.by_enum(RoleEnum.USER)]
            db.session.add(user)
            db.session.commit()

        response = self.client.post(
            "/api/upload",
            data={
                "wordlist": NONE_STR,
                "rule": NONE_STR,
                "workload": Workload.Normal.value,
                "brain_client_feature": "2",
                "capture": (io.BytesIO(sample_capture.read_bytes()), sample_capture.name),
            },
            headers=basic_auth("api-long-password", long_password),
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["status"], "success")
        self.assertEqual(len(self.fake_worker.submitted), 1)

    def test_web_upload_form_schedules_capture(self):
        sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
        self.login_admin()

        response = self.client.post(
            "/upload",
            data={
                "wordlist": NONE_STR,
                "rule": NONE_STR,
                "workload": Workload.Normal.value,
                "brain_client_feature": "2",
                "capture": (io.BytesIO(sample_capture.read_bytes()), sample_capture.name),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        self.assertEqual(len(self.fake_worker.submitted), 1)
        with app.app_context():
            task = UploadedTask.query.one()
            self.assertEqual(task.essid, "hashcat-essid")
            self.assertEqual(task.bssid, "fc690c158264")

    def test_settings_page_loads_launch_controls(self):
        self.login_admin()
        progress = {
            "update": {"state": "idle", "progress": 0, "message": "Waiting"},
            "gpu": {"state": "idle", "progress": 0, "message": "Waiting"},
            "nvidia": {"state": "idle", "progress": 0, "message": "Waiting"},
        }
        devices = [{"id": "cpu", "name": "Host CPU", "memory": "1024 MB", "is_gpu": False, "hashcat_usable": True}]

        with mock.patch("app.utils.utils.get_hashcat_devices", return_value=devices), \
                mock.patch("app.views.get_autostart_status", return_value="disabled"), \
                mock.patch("app.views.get_update_status", return_value=("idle", "No update running", "")), \
                mock.patch("app.views.get_install_progress", return_value=progress), \
                mock.patch("app.views.get_tailscale_snapshot", return_value={"status": "Not installed", "running": False, "ip": "", "plugin_url": ""}), \
                mock.patch("app.views.get_cloudflare_snapshot", return_value={"status": "Not installed", "installed": False, "running": False, "plugin_url": ""}):
            response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("Update the Server", text)
        self.assertIn("Remote Access Setup", text)
        self.assertIn("Permanent Uninstall", text)
        self.assertIn("Login Settings", text)
        self.assertIn('id="gpu-progress-bar"', text)
        self.assertIn('id="gpu-install-btn"', text)
        self.assertNotIn("nvidia-progress", text)

    def test_install_progress_exposes_gpu_key_and_legacy_nvidia_alias(self):
        log_dir = _TEST_HOME / "progress-alias"
        log_dir.mkdir(parents=True, exist_ok=True)

        with mock.patch("app.views.get_runtime_logs_dir", return_value=log_dir):
            views.write_progress_snapshot("gpu", "running", 42, "Installing AMD ROCm runtime")
            progress = views.get_install_progress()

        self.assertEqual(progress["gpu"]["state"], "running")
        self.assertEqual(progress["gpu"]["progress"], 42)
        self.assertEqual(progress["gpu"]["message"], "Installing AMD ROCm runtime")
        self.assertEqual(progress["nvidia"], progress["gpu"])

    def test_tailscale_settings_accepts_blank_auth_key(self):
        self.login_admin()
        FakeProcess.configure(returncode=0, output="Tailscale is now active.\n")

        with mock.patch("app.views.get_runtime_logs_dir", return_value=_TEST_HOME / "logs"), \
                mock.patch("app.views.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess(command, **kwargs)):
            response = self.client.post(
                "/settings",
                data={"auth_key": "", "submit_tailscale": "Install / Connect Tailscale"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(FakeProcess.calls), 1)
        self.assertEqual(FakeProcess.calls[0].input, "")
        self.assertIn("install_tailscale.sh", FakeProcess.calls[0].command[1])

    def test_cloudflare_settings_saves_url_only_after_success(self):
        self.login_admin()
        FakeProcess.configure(returncode=0, output="Cloudflare Tunnel connector is installed.\n")

        with mock.patch("app.views.get_runtime_logs_dir", return_value=_TEST_HOME / "logs"), \
                mock.patch("app.views.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess(command, **kwargs)):
            response = self.client.post(
                "/settings",
                data={
                    "public_hostname": "UPLOAD.Example.COM",
                    "tunnel_token": "secret-token",
                    "submit_public_website": "Install / Start Public Website",
                },
                follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(read_settings().get("public_plugin_url"), "https://upload.example.com")
        cloudflare_call = next(
            call for call in FakeProcess.calls
            if "install_cloudflare_tunnel.sh" in " ".join(str(part) for part in call.command)
        )
        self.assertEqual(cloudflare_call.input, "secret-token")
        self.assertEqual(cloudflare_call.command[-1], "upload.example.com")

    def test_cloudflare_settings_rejects_invalid_public_hostname(self):
        self.login_admin()
        FakeProcess.configure(returncode=0, output="Cloudflare Tunnel connector is installed.\n")
        devices = [{"id": "cpu", "name": "Host CPU", "memory": "1024 MB", "is_gpu": False, "hashcat_usable": True}]
        progress = {
            "update": {"state": "idle", "progress": 0, "message": "Waiting"},
            "gpu": {"state": "idle", "progress": 0, "message": "Waiting"},
            "nvidia": {"state": "idle", "progress": 0, "message": "Waiting"},
        }

        with mock.patch("app.utils.utils.get_hashcat_devices", return_value=devices), \
                mock.patch("app.views.get_autostart_status", return_value="disabled"), \
                mock.patch("app.views.get_update_status", return_value=("idle", "No update running", "")), \
                mock.patch("app.views.get_install_progress", return_value=progress), \
                mock.patch("app.views.get_tailscale_snapshot", return_value={"status": "Not installed", "running": False, "ip": "", "plugin_url": ""}), \
                mock.patch("app.views.get_cloudflare_snapshot", return_value={"status": "Not installed", "installed": False, "running": False, "plugin_url": ""}), \
                mock.patch("app.views.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess(command, **kwargs)):
            response = self.client.post(
                "/settings",
                data={
                    "public_hostname": "https://upload.example.com/path",
                    "tunnel_token": "secret-token",
                    "submit_public_website": "Install / Start Public Website",
                },
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Use a hostname only", response.get_data(as_text=True))
        self.assertEqual(FakeProcess.calls, [])
        self.assertNotEqual(read_settings().get("public_plugin_url"), "https://upload.example.com")

    def test_cloudflare_settings_saves_url_when_setup_is_still_running(self):
        self.login_admin()
        FakeProcess.configure(returncode=0, output="Downloading cloudflared\n", timeout=True)

        with mock.patch("app.views.get_runtime_logs_dir", return_value=_TEST_HOME / "logs"), \
                mock.patch("app.views.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess(command, **kwargs)):
            response = self.client.post(
                "/settings",
                data={
                    "public_hostname": "upload.example.com",
                    "tunnel_token": "secret-token",
                    "submit_public_website": "Install / Start Public Website",
                },
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Public website connector is still starting", response.get_data(as_text=True))
        self.assertEqual(read_settings().get("public_plugin_url"), "https://upload.example.com")
        self.assertTrue(FakeProcess.calls[0].stdin.closed)

    def test_cloudflare_settings_keeps_url_unset_on_script_failure(self):
        self.login_admin()
        FakeProcess.configure(returncode=1, output="Systemd is not running\n")

        with mock.patch("app.views.get_runtime_logs_dir", return_value=_TEST_HOME / "logs"), \
                mock.patch("app.views.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess(command, **kwargs)):
            response = self.client.post(
                "/settings",
                data={
                    "public_hostname": "upload.example.com",
                    "tunnel_token": "secret-token",
                    "submit_public_website": "Install / Start Public Website",
                },
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Failed to start public website connector", response.get_data(as_text=True))
        self.assertNotEqual(read_settings().get("public_plugin_url"), "https://upload.example.com")

    def test_cloudflare_snapshot_hides_missing_systemctl_when_not_installed(self):
        with mock.patch("app.views.shutil.which", return_value=None), \
                mock.patch("app.views.subprocess.run", side_effect=FileNotFoundError("systemctl")):
            snapshot = views.get_cloudflare_snapshot()

        self.assertEqual(snapshot["status"], "Not installed")
        self.assertFalse(snapshot["installed"])
        self.assertFalse(snapshot["running"])

    def test_gpu_settings_reports_background_driver_check(self):
        self.login_admin()
        FakeProcess.configure(returncode=0, output="Installing drivers\n", timeout=True)
        devices = [{"id": "0", "name": "AMD Radeon Test GPU", "memory": "8 GB", "is_gpu": True, "hashcat_usable": False}]

        with mock.patch("app.utils.utils.get_hashcat_devices", return_value=devices), \
                mock.patch("app.views.get_runtime_logs_dir", return_value=_TEST_HOME / "logs"), \
                mock.patch("app.views.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess(command, **kwargs)):
            response = self.client.post(
                "/settings",
                data={"submit_check_nvidia": "Check GPU Drivers"},
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("GPU driver check started", response.get_data(as_text=True))
        self.assertIn("install_gpu_drivers.sh", FakeProcess.calls[0].command[1])
        self.assertEqual(FakeProcess.calls[0].command[-1], "check")
        self.assertTrue(FakeProcess.calls[0].stdin.closed)

    def test_update_settings_redirects_to_wait_page_after_start(self):
        self.login_admin()
        FakeProcess.configure(returncode=0, output="Update process spawned in the background.\n")

        with mock.patch("app.views.get_runtime_logs_dir", return_value=_TEST_HOME / "logs"), \
                mock.patch("app.views.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess(command, **kwargs)):
            response = self.client.post(
                "/settings",
                data={"submit_update": "Update App & Restart"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/update_wait", response.headers["Location"])
        self.assertIn("update_app.sh", FakeProcess.calls[0].command[1])

    def test_update_status_detects_transient_systemd_updater(self):
        log_dir = _TEST_HOME / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        def fake_run(command, **kwargs):
            if command[:2] == ["systemctl", "is-active"]:
                return SimpleNamespace(stdout="inactive\n", stderr="", returncode=3)
            if command[:2] == ["systemctl", "list-units"]:
                return SimpleNamespace(
                    stdout="hashcat-server-updater-123.service loaded active running Update\n",
                    stderr="",
                    returncode=0,
                )
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        with mock.patch("app.views.get_runtime_logs_dir", return_value=log_dir), \
                mock.patch("app.views.subprocess.run", side_effect=fake_run):
            status, summary, _ = views.get_update_status()

        self.assertEqual(status, "running")
        self.assertIn("running", summary.lower())

    def test_update_status_treats_manual_restart_warning_as_success(self):
        log_dir = _TEST_HOME / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "app_update.progress").write_text(
            "success|100|Update complete. Restart the manual gunicorn process to load it.\n",
            encoding="utf-8",
        )
        (log_dir / "updater.log").write_text(
            "\n".join([
                "[!] Systemd is not running, so the background service cannot be restarted automatically.",
                "[!] Manual restart required because systemd is not running.",
                "[*] Update complete. All user data and settings have been preserved.",
            ]),
            encoding="utf-8",
        )

        with mock.patch("app.views.get_runtime_logs_dir", return_value=log_dir), \
                mock.patch("app.views.subprocess.run", return_value=SimpleNamespace(stdout="inactive\n", stderr="", returncode=3)):
            status, summary, excerpt = views.get_update_status()

        self.assertEqual(status, "success")
        self.assertIn("manual gunicorn", summary)
        self.assertIn("Manual restart required", excerpt)

    def test_uninstall_settings_starts_background_uninstall(self):
        self.login_admin()
        FakeProcess.configure(returncode=0, output="Uninstall process spawned.\n")

        with mock.patch("app.views.get_runtime_logs_dir", return_value=_TEST_HOME / "logs"), \
                mock.patch("app.views.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess(command, **kwargs)):
            response = self.client.post(
                "/settings",
                data={"submit_uninstall": "Permanently Uninstall Server"},
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("App uninstallation process started", response.get_data(as_text=True))
        self.assertIn("uninstall_app.sh", FakeProcess.calls[0].command[1])
        self.assertIn("--background", FakeProcess.calls[0].command)

    def test_admin_can_cancel_another_users_task_from_gui(self):
        with app.app_context():
            owner = User(username="job-owner")
            owner.set_password("owner-password")
            owner.roles = [Role.by_enum(RoleEnum.USER)]
            db.session.add(owner)
            db.session.commit()
            task = UploadedTask(
                user_id=owner.id,
                filename="job-owner/test_capture_hashcat_essid.22000",
                bssid="fc690c158264",
                essid="hashcat-essid",
                completed=False,
            )
            db.session.add(task)
            db.session.commit()
            task_id = task.id

        self.login_admin()
        with mock.patch.object(views.hashcat_worker, "cancel", return_value=True, create=True) as cancel_task:
            response = self.client.get(f"/cancel/{task_id}")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json(), "Cancelled")
        cancel_task.assert_called_once_with(task_id)

    def test_non_owner_cannot_cancel_another_users_task(self):
        with app.app_context():
            owner = User(username="job-owner")
            owner.set_password("owner-password")
            owner.roles = [Role.by_enum(RoleEnum.USER)]
            intruder = User(username="job-intruder")
            intruder.set_password("intruder-password")
            intruder.roles = [Role.by_enum(RoleEnum.USER)]
            db.session.add_all([owner, intruder])
            db.session.commit()
            task = UploadedTask(
                user_id=owner.id,
                filename="job-owner/test_capture_hashcat_essid.22000",
                bssid="fc690c158264",
                essid="hashcat-essid",
                completed=False,
            )
            db.session.add(task)
            db.session.commit()
            task_id = task.id
            intruder_id = str(intruder.id)

        with self.client.session_transaction() as session:
            session["_user_id"] = intruder_id
            session["_fresh"] = True

        with mock.patch.object(views.hashcat_worker, "cancel", return_value=True, create=True) as cancel_task:
            response = self.client.get(f"/cancel/{task_id}")

        self.assertEqual(response.status_code, 403)
        cancel_task.assert_not_called()

    def test_requeue_all_non_admin_ignores_other_users_active_tasks(self):
        sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
        with app.app_context():
            retry_user = User(username="retry-user")
            retry_user.set_password("retry-password")
            retry_user.roles = [Role.by_enum(RoleEnum.USER)]
            busy_user = User(username="busy-user")
            busy_user.set_password("busy-password")
            busy_user.roles = [Role.by_enum(RoleEnum.USER)]
            db.session.add_all([retry_user, busy_user])
            db.session.commit()
            failed_task = UploadedTask(
                user_id=retry_user.id,
                filename="retry-user/test_capture_hashcat_essid.22000",
                bssid="fc690c158264",
                essid="hashcat-essid",
                completed=True,
                found_key=None,
            )
            other_active_task = UploadedTask(
                user_id=busy_user.id,
                filename="busy-user/test_capture_hashcat_essid.22000",
                bssid="fc690c158264",
                essid="hashcat-essid",
                completed=False,
            )
            db.session.add_all([failed_task, other_active_task])
            db.session.commit()
            retry_user_id = str(retry_user.id)

        with self.client.session_transaction() as session:
            session["_user_id"] = retry_user_id
            session["_fresh"] = True

        with mock.patch("app.views.resolve_task_attack_file", return_value=sample_capture):
            response = self.client.post("/requeue_all", follow_redirects=True)

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn("Re-queued 1 failed task", response.get_data(as_text=True))
        self.assertEqual(len(self.fake_worker.submitted), 1)
        with app.app_context():
            retry_user = User.query.filter_by(username="retry-user").first()
            self.assertEqual(
                UploadedTask.query.filter_by(user_id=retry_user.id, completed=False).count(),
                1,
            )

    def test_requeue_all_non_admin_still_blocks_on_own_active_tasks(self):
        with app.app_context():
            retry_user = User(username="retry-user")
            retry_user.set_password("retry-password")
            retry_user.roles = [Role.by_enum(RoleEnum.USER)]
            db.session.add(retry_user)
            db.session.commit()
            db.session.add_all([
                UploadedTask(
                    user_id=retry_user.id,
                    filename="retry-user/failed.22000",
                    bssid="fc690c158264",
                    essid="hashcat-essid",
                    completed=True,
                    found_key=None,
                ),
                UploadedTask(
                    user_id=retry_user.id,
                    filename="retry-user/active.22000",
                    bssid="fc690c158264",
                    essid="hashcat-essid",
                    completed=False,
                ),
            ])
            db.session.commit()
            retry_user_id = str(retry_user.id)

        with self.client.session_transaction() as session:
            session["_user_id"] = retry_user_id
            session["_fresh"] = True

        response = self.client.post("/requeue_all", follow_redirects=True)

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn("Cannot retry all while 1 task", response.get_data(as_text=True))
        self.assertEqual(len(self.fake_worker.submitted), 0)

    def test_requeue_preserves_stored_rainbow_workload(self):
        sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            task = UploadedTask(
                user_id=admin.id,
                filename="admin/test_capture_hashcat_essid.22000",
                bssid="fc690c158264",
                essid="hashcat-essid",
                workload=Workload.Rainbow.value,
                completed=True,
                found_key=None,
            )
            db.session.add(task)
            db.session.commit()
            task_id = task.id

        self.login_admin()
        with mock.patch("app.views.resolve_task_attack_file", return_value=sample_capture):
            response = self.client.get(f"/requeue/{task_id}", follow_redirects=True)

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn("was re-queued", response.get_data(as_text=True))
        self.assertEqual(len(self.fake_worker.submitted), 1)
        self.assertEqual(self.fake_worker.submitted[0]["workload"], Workload.Rainbow.value)
        with app.app_context():
            requeued = UploadedTask.query.filter(
                UploadedTask.id != task_id,
                UploadedTask.completed == False,
            ).one()
            self.assertEqual(requeued.workload, Workload.Rainbow.value)

    def test_requeue_restores_brain_secret_only_for_worker_args(self):
        sample_capture = Path(app.static_folder) / "test_capture_hashcat_essid.22000"
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            task = UploadedTask(
                user_id=admin.id,
                filename="admin/test_capture_hashcat_essid.22000",
                bssid="fc690c158264",
                essid="hashcat-essid",
                hashcat_args="--brain-client --brain-client-features=3 --brain-password=old-secret",
                completed=True,
                found_key=None,
            )
            db.session.add(task)
            db.session.commit()
            task_id = task.id

        self.login_admin()
        with mock.patch("app.views.resolve_task_attack_file", return_value=sample_capture), \
                mock.patch("app.views.read_hashcat_brain_password", return_value="fresh-secret"):
            response = self.client.get(f"/requeue/{task_id}", follow_redirects=True)

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(len(self.fake_worker.submitted), 1)
        submitted = self.fake_worker.submitted[0]
        self.assertEqual(submitted["hashcat_args"], [
            "--brain-client",
            "--brain-client-features=3",
        ])
        self.assertEqual(submitted["hashcat_args_secret"], [
            "--brain-client",
            "--brain-client-features=3",
            "--brain-password=fresh-secret",
        ])
        with app.app_context():
            requeued = UploadedTask.query.filter(
                UploadedTask.id != task_id,
                UploadedTask.completed == False,
            ).one()
            self.assertNotIn("old-secret", requeued.hashcat_args)

    def test_pwnagotchi_heartbeat_updates_status(self):
        response = self.client.post(
            "/api/pwnagotchi/heartbeat",
            json={"event": "startup", "hostname": "pwny", "plugin_version": "1.4.7"},
            headers=basic_auth(),
        )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body["status"], "success")
        with app.app_context():
            status = PwnagotchiStatus.query.filter_by(username="admin").first()
            self.assertIsNotNone(status)
            self.assertEqual(status.hostname, "pwny")
            self.assertEqual(status.plugin_version, "1.4.7")

    def test_pwnagotchi_page_uses_local_visual_asset(self):
        self.login_admin()

        response = self.client.get("/pwnagotchi")

        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("/static/pwnagotchi-device.svg", text)
        self.assertNotIn("pwnagotchi.ai/images", text)

    def test_long_launch_metadata_renders_and_exports_without_truncation(self):
        long_filename = "admin/" + "capture-" + ("x" * 320) + ".22000"
        long_essid = "wifi-" + ("segment-" * 120)
        long_status = "Running " + ("status detail " * 160)
        long_wordlist = "wordlist-" + ("name-" * 120)
        long_rule = "rule-" + ("detail-" * 120)
        long_args = "--custom " + ("argument-value " * 160)
        long_password = "P@ss:" + ("LongPassword123!" * 320)
        long_hostname = "pwnagotchi-" + ("host-" * 90)
        long_event = "heartbeat-" + ("event-" * 90)
        long_message = "message " + ("upload telemetry detail " * 90)
        long_plugin_version = "version-" + ("build-" * 90)

        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            db.session.add(UploadedTask(
                user_id=admin.id,
                filename=long_filename,
                bssid="fc690c158264",
                essid=long_essid,
                wordlist=long_wordlist,
                rule=long_rule,
                hashcat_args=long_args,
                status=long_status,
                found_key=f"hashZ:{long_password}",
                completed=True,
            ))
            db.session.commit()

        heartbeat = self.client.post(
            "/api/pwnagotchi/heartbeat",
            json={
                "event": long_event,
                "hostname": long_hostname,
                "plugin_version": long_plugin_version,
                "message": long_message,
            },
            headers=basic_auth(),
        )
        self.assertEqual(heartbeat.status_code, 200, heartbeat.get_data(as_text=True))

        self.login_admin()
        profile = self.client.get("/user_profile")
        self.assertEqual(profile.status_code, 200)
        profile_text = profile.get_data(as_text=True)
        self.assertIn(long_filename, profile_text)
        self.assertIn(long_essid, profile_text)
        self.assertIn(long_wordlist, profile_text)
        self.assertIn(long_rule, profile_text)
        self.assertIn(long_status, profile_text)
        self.assertIn(long_password, profile_text)

        export = self.client.get("/download_all_results")
        self.assertEqual(export.status_code, 200)
        self.assertIn(f"{long_essid} | fc690c158264 | {long_password}\n", export.get_data(as_text=True))

        pwnagotchi_page = self.client.get("/pwnagotchi")
        self.assertEqual(pwnagotchi_page.status_code, 200)
        pwnagotchi_text = pwnagotchi_page.get_data(as_text=True)
        self.assertIn(long_hostname, pwnagotchi_text)
        self.assertIn(long_event, pwnagotchi_text)
        self.assertIn(long_message, pwnagotchi_text)
        self.assertIn(long_plugin_version, pwnagotchi_text)

    def test_download_export_keeps_multiple_passwords_verbatim(self):
        long_password = "Xy9!" * 180
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            task = UploadedTask(
                user_id=admin.id,
                filename="admin/test_capture_hashcat_essid.22000",
                bssid="fc690c158264",
                essid="hashcat-essid",
                found_key="\n".join([
                    "hashA:  leading and trailing  ",
                    "hashB:colon:inside:password",
                    f"hashC:{long_password}",
                ]),
                completed=True,
            )
            db.session.add(task)
            db.session.commit()

        self.login_admin()
        response = self.client.get("/download_all_results")

        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("hashcat-essid | fc690c158264 |   leading and trailing  \n", text)
        self.assertIn("hashcat-essid | fc690c158264 | colon:inside:password\n", text)
        self.assertIn(f"hashcat-essid | fc690c158264 | {long_password}\n", text)

    def test_download_export_uses_safe_filename_for_unusual_username(self):
        raw_username = '../admin "quoted"\r\nbad'
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            admin.username = raw_username
            db.session.add(UploadedTask(
                user_id=admin.id,
                filename="admin/test_capture_hashcat_essid.22000",
                bssid="fc690c158264",
                essid="hashcat-essid",
                found_key="hashA:LaunchReadyPass123!",
                completed=True,
            ))
            db.session.commit()
            admin_id = str(admin.id)

        with self.client.session_transaction() as session:
            session["_user_id"] = admin_id
            session["_fresh"] = True

        response = self.client.get("/download_all_results")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        disposition = response.headers.get("Content-Disposition", "")
        self.assertIn("attachment;", disposition)
        self.assertIn("filename=cracked_passwords_", disposition)
        self.assertNotIn("..", disposition)
        self.assertNotIn('"', disposition)
        self.assertNotIn("\r", disposition)
        self.assertNotIn("\n", disposition)

    def test_pmk_rainbow_cache_uses_wpa_byte_length_boundaries(self):
        valid_63_bytes = "A" * 63
        too_long_64_bytes = "B" * 64
        special_password = " pass:with:spaces! "
        source = _TEST_HOME / "rainbow-source.txt"
        source.write_text(
            "\n".join([valid_63_bytes, too_long_64_bytes, special_password]) + "\n",
            encoding="utf-8",
        )

        pmk_path, map_path = build_pmk_rainbow_cache("hashcat-essid", source)

        self.assertIsNotNone(pmk_path)
        self.assertIsNotNone(map_path)
        mapping = map_path.read_text(encoding="utf-8")
        self.assertIn(f"\t{valid_63_bytes}\n", mapping)
        self.assertIn(f"\t{special_password}\n", mapping)
        self.assertNotIn(too_long_64_bytes, mapping)

        pmk = mapping.split("\t", 1)[0]
        self.assertEqual(resolve_pmk_rainbow_password(f"hash:{pmk}", map_path), valid_63_bytes)


if __name__ == "__main__":
    unittest.main()
