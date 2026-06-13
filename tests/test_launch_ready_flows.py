import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


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

    def submit_capture(self, file_22000, uploaded_form, task):
        self.submitted.append({
            "path": Path(file_22000),
            "task_id": task.id,
            "essid": task.essid,
            "bssid": task.bssid,
            "workload": uploaded_form.workload.data,
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
                    "public_hostname": "upload.example.com",
                    "tunnel_token": "secret-token",
                    "submit_public_website": "Install / Start Public Website",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(read_settings().get("public_plugin_url"), "https://upload.example.com")
        self.assertEqual(FakeProcess.calls[0].input, "secret-token")
        self.assertIn("install_cloudflare_tunnel.sh", FakeProcess.calls[0].command[1])

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
