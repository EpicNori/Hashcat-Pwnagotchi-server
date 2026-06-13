import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class UninstallWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        cls.bash = str(git_bash) if git_bash.exists() else shutil.which("bash")

    def run_bash(self, script):
        if self.bash is None:
            self.skipTest("bash is not available")
        return subprocess.run(
            [self.bash, "-lc", script],
            cwd=self.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )

    @staticmethod
    def bash_path(path):
        raw = str(Path(path).resolve())
        if len(raw) >= 3 and raw[1:3] == ":\\":
            return "/" + raw[0].lower() + raw[2:].replace("\\", "/")
        return raw.replace("\\", "/")

    def test_help_uses_configured_paths_and_lists_dry_run(self):
        with tempfile.TemporaryDirectory(prefix="hashcat-uninstall-help-") as temp_name:
            root = Path(temp_name)
            data_dir = root / "data path"
            log_dir = root / "logs path"
            script = textwrap.dedent(f"""
                export HASHCAT_WPA_DATA_DIR="{self.bash_path(data_dir)}"
                export HASHCAT_WPA_LOG_DIR="{self.bash_path(log_dir)}"
                bash ./bash/uninstall_app.sh --help
            """)

            result = self.run_bash(script)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("--dry-run", result.stdout)
        self.assertIn(self.bash_path(data_dir), result.stdout)
        self.assertIn(self.bash_path(log_dir), result.stdout)

    def test_wizard_cancel_does_not_require_root_or_spawn_background(self):
        result = self.run_bash("printf 'n\\n' | bash ./bash/uninstall_app.sh --wizard\n")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Hashcat WPA Server Uninstall", result.stdout)
        self.assertIn("Cancelled.", result.stdout)
        self.assertNotIn("Starting uninstall in the background", result.stdout)
        self.assertNotIn("must be run as root", result.stdout)

    def test_dry_run_keep_data_uses_safe_configured_paths(self):
        with tempfile.TemporaryDirectory(prefix="hashcat-uninstall-dry-") as temp_name:
            root = Path(temp_name)
            app_root = root / "app root"
            data_dir = root / "data dir"
            log_dir = root / "log dir"
            service_file = root / "service" / "hashcat-wpa-server.service"
            cli_link = root / "bin" / "crackserver"
            sudoers_file = root / "sudoers" / "hashcat-tailscale"
            for path in [app_root, data_dir, log_dir, service_file.parent, cli_link.parent, sudoers_file.parent]:
                path.mkdir(parents=True, exist_ok=True)
            service_file.write_text("service", encoding="utf-8")
            cli_link.write_text("cli", encoding="utf-8")
            sudoers_file.write_text("sudoers", encoding="utf-8")

            script = textwrap.dedent(f"""
                export HASHCAT_WPA_APP_ROOT="{self.bash_path(app_root)}"
                export HASHCAT_WPA_DATA_DIR="{self.bash_path(data_dir)}"
                export HASHCAT_WPA_LOG_DIR="{self.bash_path(log_dir)}"
                export HASHCAT_WPA_SERVICE_FILE="{self.bash_path(service_file)}"
                export HASHCAT_WPA_CLI_LINK="{self.bash_path(cli_link)}"
                export HASHCAT_WPA_SUDOERS_FILE="{self.bash_path(sudoers_file)}"
                export HASHCAT_WPA_PACKAGE_NAME="hashcat-wpa-server-test-only"
                bash ./bash/uninstall_app.sh --dry-run --yes --keep-data
            """)

            result = self.run_bash(script)

            self.assertTrue(app_root.exists())
            self.assertTrue(data_dir.exists())
            self.assertTrue(log_dir.exists())
            self.assertTrue(service_file.exists())
            self.assertTrue(cli_link.exists())
            self.assertTrue(sudoers_file.exists())

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("[dry-run] rm -rf --", result.stdout)
        self.assertIn(self.bash_path(app_root), result.stdout)
        self.assertIn(f"Data kept in {self.bash_path(data_dir)}", result.stdout)
        self.assertIn(f"Logs kept in {self.bash_path(log_dir)}", result.stdout)

    def test_dry_run_purge_data_reports_user_and_data_removal(self):
        with tempfile.TemporaryDirectory(prefix="hashcat-uninstall-purge-") as temp_name:
            root = Path(temp_name)
            script = textwrap.dedent(f"""
                export HASHCAT_WPA_APP_ROOT="{self.bash_path(root / 'app')}"
                export HASHCAT_WPA_DATA_DIR="{self.bash_path(root / 'data')}"
                export HASHCAT_WPA_LOG_DIR="{self.bash_path(root / 'logs')}"
                export HASHCAT_WPA_SERVICE_FILE="{self.bash_path(root / 'service')}"
                export HASHCAT_WPA_CLI_LINK="{self.bash_path(root / 'crackserver')}"
                export HASHCAT_WPA_SUDOERS_FILE="{self.bash_path(root / 'sudoers')}"
                export HASHCAT_WPA_APP_USER="hashcat-test-user"
                export HASHCAT_WPA_PACKAGE_NAME="hashcat-wpa-server-test-only"
                bash ./bash/uninstall_app.sh --dry-run --yes --purge-data
            """)

            result = self.run_bash(script)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Purging user data", result.stdout)
        self.assertIn(self.bash_path(root / "data"), result.stdout)
        self.assertIn(self.bash_path(root / "logs"), result.stdout)
        self.assertIn("[dry-run] userdel hashcat-test-user", result.stdout)

    def test_background_dry_run_preserves_custom_log_and_paths(self):
        with tempfile.TemporaryDirectory(prefix="hashcat-uninstall-bg-") as temp_name:
            root = Path(temp_name)
            log_file = root / "nested" / "uninstall.log"
            app_root = root / "app"
            script = textwrap.dedent(f"""
                export HASHCAT_WPA_APP_ROOT="{self.bash_path(app_root)}"
                export HASHCAT_WPA_DATA_DIR="{self.bash_path(root / 'data')}"
                export HASHCAT_WPA_LOG_DIR="{self.bash_path(root / 'logs')}"
                export HASHCAT_WPA_SERVICE_FILE="{self.bash_path(root / 'service')}"
                export HASHCAT_WPA_CLI_LINK="{self.bash_path(root / 'crackserver')}"
                export HASHCAT_WPA_SUDOERS_FILE="{self.bash_path(root / 'sudoers')}"
                export HASHCAT_WPA_PACKAGE_NAME="hashcat-wpa-server-test-only"
                export HASHCAT_WPA_UNINSTALL_LOG_FILE="{self.bash_path(log_file)}"
                bash ./bash/uninstall_app.sh --background --dry-run --keep-data
                for _ in $(seq 1 50); do
                    [ -s "{self.bash_path(log_file)}" ] && break
                    sleep 0.1
                done
                echo "LOG_CONTENT_BEGIN"
                cat "{self.bash_path(log_file)}"
            """)

            result = self.run_bash(script)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"Log: {self.bash_path(log_file)}", result.stdout)
        self.assertIn("LOG_CONTENT_BEGIN", result.stdout)
        self.assertIn(self.bash_path(app_root), result.stdout)
        self.assertIn("[dry-run]", result.stdout)


if __name__ == "__main__":
    unittest.main()
