import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_TEST_HOME = Path(tempfile.mkdtemp(prefix="hashcat-wpa-server-tests-"))
os.environ["HASHCAT_WPA_SERVER_HOME"] = str(_TEST_HOME)
os.environ["HASHCAT_WPA_SKIP_STARTUP_MAINTENANCE"] = "1"
_TEST_HOME.mkdir(parents=True, exist_ok=True)
(_TEST_HOME / "benchmark.csv").write_text("test,0\n", encoding="utf-8")

from app import app, db
from app.domain import NONE_STR, Workload
from app.login import User, create_first_users
from app.uploader import PwnagotchiStatus, UploadedTask
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


class LaunchReadyFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

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

        self.client = app.test_client()

    def tearDown(self):
        views.hashcat_worker = self.original_worker
        with app.app_context():
            db.session.remove()

    def login_admin(self):
        return self.client.post(
            "/login",
            data={"username": "admin", "password": "changeme"},
            follow_redirects=False,
        )

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
