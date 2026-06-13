import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _fake_pwnagotchi_modules():
    pwnagotchi = types.ModuleType("pwnagotchi")
    plugins = types.ModuleType("pwnagotchi.plugins")
    ui = types.ModuleType("pwnagotchi.ui")
    components = types.ModuleType("pwnagotchi.ui.components")
    view = types.ModuleType("pwnagotchi.ui.view")
    fonts = types.ModuleType("pwnagotchi.ui.fonts")
    requests = types.ModuleType("requests")

    class Plugin:
        pass

    def labeled_value(*args, **kwargs):
        return SimpleNamespace(args=args, kwargs=kwargs)

    plugins.Plugin = Plugin
    components.LabeledValue = labeled_value
    view.BLACK = "black"
    fonts.Small = "small"
    requests.get = mock.Mock()
    requests.post = mock.Mock()
    pwnagotchi.plugins = plugins
    pwnagotchi.ui = ui
    ui.components = components
    ui.view = view
    ui.fonts = fonts

    return {
        "pwnagotchi": pwnagotchi,
        "pwnagotchi.plugins": plugins,
        "pwnagotchi.ui": ui,
        "pwnagotchi.ui.components": components,
        "pwnagotchi.ui.view": view,
        "pwnagotchi.ui.fonts": fonts,
        "requests": requests,
    }


class PwnagotchiPluginTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="hashcat-pwnagotchi-plugin-")
        self.root = Path(self.tempdir.name)
        self.module_patch = mock.patch.dict(sys.modules, _fake_pwnagotchi_modules())
        self.module_patch.start()

        sys.modules.pop("pwnagotchi_hashcat_wpa", None)
        self.plugin_module = importlib.import_module("pwnagotchi_hashcat_wpa")
        self.plugin_module._STATE_PATH = str(self.root / "uploaded-state.txt")
        self.plugin = self.plugin_module.PwnagotchiHashcatWPA()
        self.plugin._CONFIG_PATH = str(self.root / "config.toml")

    def tearDown(self):
        sys.modules.pop("pwnagotchi_hashcat_wpa", None)
        self.module_patch.stop()
        self.tempdir.cleanup()

    def test_save_webhook_writes_full_config_and_preserves_password_characters(self):
        weird_password = ' pass:"word"\\with\\tabs\tand newline\nend '
        request = SimpleNamespace(
            method="POST",
            form={
                "url": " https://upload.example.com/ ",
                "username": " admin ",
                "password": weird_password,
            },
        )

        body = self.plugin.on_webhook("save", request)

        self.assertIn("Config saved", body)
        self.assertTrue(self.plugin.ready)
        self.assertEqual(self.plugin._upload_url(), "https://upload.example.com/api/upload")
        self.assertEqual(self.plugin.options["password"], weird_password)

        config_text = Path(self.plugin._CONFIG_PATH).read_text(encoding="utf-8")
        self.assertIn('main.plugins.pwnagotchi_hashcat_wpa.enabled = true', config_text)
        self.assertIn('main.plugins.pwnagotchi_hashcat_wpa.url = "https://upload.example.com"', config_text)
        self.assertIn('main.plugins.pwnagotchi_hashcat_wpa.handshake_dir = "/home/pi/handshakes"', config_text)
        self.assertIn("main.plugins.pwnagotchi_hashcat_wpa.upload_existing = true", config_text)
        self.assertIn("main.plugins.pwnagotchi_hashcat_wpa.batch_size = 8", config_text)
        self.assertEqual(self.plugin._read_plugin_config()["password"], weird_password)

    def test_save_webhook_blank_password_keeps_existing_secret(self):
        existing_password = " existing:secret with spaces "
        self.plugin.options.update({
            "url": "https://old.example.com",
            "username": "old-user",
            "password": existing_password,
        })
        self.plugin._write_plugin_config(dict(self.plugin.options))
        request = SimpleNamespace(
            method="POST",
            form={
                "url": "https://upload.example.com",
                "username": "admin",
                "password": "",
            },
        )

        body = self.plugin.on_webhook("save", request)

        self.assertIn("Config saved", body)
        self.assertEqual(self.plugin.options["password"], existing_password)
        self.assertEqual(self.plugin._read_plugin_config()["password"], existing_password)

    def test_save_webhook_blank_password_uses_default_only_for_fresh_config(self):
        request = SimpleNamespace(
            method="POST",
            form={
                "url": "https://upload.example.com",
                "username": "admin",
                "password": "",
            },
        )

        body = self.plugin.on_webhook("save", request)

        self.assertIn("Config saved", body)
        self.assertEqual(self.plugin.options["password"], "changeme")
        self.assertEqual(self.plugin._read_plugin_config()["password"], "changeme")

    def test_web_ui_does_not_render_saved_password_value(self):
        secret = "dont-render-this-secret"
        self.plugin.options.update({
            "url": "https://upload.example.com",
            "username": "admin",
            "password": secret,
        })

        body = self.plugin.on_webhook("", SimpleNamespace(method="GET", form={}))

        self.assertIn('placeholder="Leave blank to keep saved password"', body)
        self.assertIn('type="password" value=""', body)
        self.assertNotIn(secret, body)

    def test_read_config_accepts_legacy_auto_upload_option(self):
        Path(self.plugin._CONFIG_PATH).write_text(
            "\n".join([
                'main.plugins.pwnagotchi_hashcat_wpa.url = "http://100.64.0.2:9111"',
                'main.plugins.pwnagotchi_hashcat_wpa.username = "admin"',
                'main.plugins.pwnagotchi_hashcat_wpa.password = "changeme"',
                "main.plugins.pwnagotchi_hashcat_wpa.auto_upload = false",
            ]) + "\n",
            encoding="utf-8",
        )

        config = self.plugin._read_plugin_config()

        self.assertIs(config["upload_existing"], False)

    def test_upload_pending_files_posts_multipart_batches_and_remembers_success(self):
        capture_one = self.root / "one.22000"
        capture_two = self.root / "two.pcap"
        capture_one.write_bytes(b"hashcat one")
        capture_two.write_bytes(b"hashcat two")
        opened = []
        calls = []

        self.plugin.ready = True
        self.plugin.options.update({
            "url": "https://upload.example.com",
            "username": "admin",
            "password": "pw:with spaces",
            "batch_size": 1,
        })
        self.plugin._add_pending_file(str(capture_one))
        self.plugin._add_pending_file(str(capture_two))

        def fake_post(url, auth, files, timeout):
            self.assertEqual(url, "https://upload.example.com/api/upload")
            self.assertEqual(auth, ("admin", "pw:with spaces"))
            self.assertEqual(timeout, 30)
            self.assertEqual(len(files), 1)
            field, file_tuple = files[0]
            filename, handle, content_type = file_tuple
            self.assertEqual(field, "capture")
            self.assertIn(filename, {capture_one.name, capture_two.name})
            self.assertEqual(content_type, "application/octet-stream")
            self.assertFalse(handle.closed)
            self.assertTrue(handle.read())
            opened.append(handle)
            calls.append(filename)
            return SimpleNamespace(status_code=200, text="ok")

        with mock.patch.object(self.plugin_module.requests, "post", side_effect=fake_post):
            self.plugin._upload_pending_files()

        self.assertEqual(calls, [capture_one.name, capture_two.name])
        self.assertEqual(self.plugin._pending_files, {})
        self.assertTrue(Path(self.plugin_module._STATE_PATH).exists())
        self.assertEqual(len(self.plugin._uploaded_fingerprints), 2)
        self.assertTrue(all(handle.closed for handle in opened))

    def test_failed_upload_keeps_pending_file_for_retry(self):
        capture = self.root / "retry.22000"
        capture.write_bytes(b"retry me")
        self.plugin.ready = True
        self.plugin.options.update({
            "url": "https://upload.example.com",
            "username": "admin",
            "password": "changeme",
            "batch_size": 4,
        })
        self.plugin._add_pending_file(str(capture))

        with mock.patch.object(
            self.plugin_module.requests,
            "post",
            return_value=SimpleNamespace(status_code=500, text="nope"),
        ):
            self.plugin._upload_pending_files()

        self.assertEqual(list(self.plugin._pending_files.keys()), [str(capture)])
        self.assertFalse(Path(self.plugin_module._STATE_PATH).exists())


if __name__ == "__main__":
    unittest.main()
