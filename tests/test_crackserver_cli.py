import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class CrackserverCliTests(unittest.TestCase):
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

    def test_upload_accepts_equals_options_and_dash_prefixed_password(self):
        script = r'''
set -euo pipefail
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
capture="$tmp/weird capture.22000"
printf 'sample capture' > "$capture"
cat > "$tmp/env.sh" <<SH
curl() {
  printf '%s\n' "\$@" > "$tmp/curl.args"
}
SH
BASH_ENV="$tmp/env.sh" bash ./bash/crackserver upload \
  --url=https://upload.example.com/root/ \
  --user=admin \
  "--password=-dash: with spaces and : colon" \
  "$capture"
cat "$tmp/curl.args"
'''

        result = self.run_bash(script)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("--user\nadmin:-dash: with spaces and : colon\n", result.stdout)
        self.assertIn("-F\ncapture=", result.stdout)
        self.assertIn("weird capture.22000\n", result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith("https://upload.example.com/root/api/upload"))

    def test_upload_rejects_unknown_options(self):
        result = self.run_bash("bash ./bash/crackserver upload --not-real\n")

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("Unknown upload option: --not-real", result.stdout)

    def test_uninstall_passes_runtime_paths_through_sudo_env(self):
        with tempfile.TemporaryDirectory(prefix="hashcat-crackserver-uninstall-") as temp_name:
            root = Path(temp_name)
            data_dir = root / "data with spaces"
            log_dir = root / "logs with spaces"
            env_file = root / "env.sh"
            calls_file = root / "sudo.calls"
            env_file.write_text(
                textwrap.dedent(f"""
                    sudo() {{
                      printf '%s\\n' "$*" > "{self.bash_path(calls_file)}"
                    }}
                """),
                encoding="utf-8",
            )
            script = textwrap.dedent(f"""
                BASH_ENV="{self.bash_path(env_file)}" \\
                HASHCAT_WPA_APP_ROOT="$PWD" \\
                HASHCAT_WPA_DATA_DIR="{self.bash_path(data_dir)}" \\
                HASHCAT_WPA_LOG_DIR="{self.bash_path(log_dir)}" \\
                HASHCAT_WPA_SERVICE_NAME="hashcat-test.service" \\
                bash ./bash/crackserver uninstall --yes --dry-run
                cat "{self.bash_path(calls_file)}"
            """)

            result = self.run_bash(script)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("HASHCAT_WPA_APP_ROOT=", result.stdout)
        self.assertIn(f"HASHCAT_WPA_DATA_DIR={self.bash_path(data_dir)}", result.stdout)
        self.assertIn(f"HASHCAT_WPA_LOG_DIR={self.bash_path(log_dir)}", result.stdout)
        self.assertIn("HASHCAT_WPA_SERVICE_NAME=hashcat-test.service", result.stdout)
        self.assertIn("--wizard --yes --dry-run", result.stdout)

    def test_reset_uses_configured_data_dir_instead_of_hardcoded_var_lib(self):
        with tempfile.TemporaryDirectory(prefix="hashcat-crackserver-reset-") as temp_name:
            root = Path(temp_name)
            data_dir = root / "data with spaces"
            data_dir.mkdir()
            env_file = root / "env.sh"
            calls_file = root / "sudo.calls"
            env_file.write_text(
                textwrap.dedent(f"""
                    sudo() {{
                      printf '%s\\n' "$*" >> "{self.bash_path(calls_file)}"
                      if [ "$1" = "test" ]; then
                        shift
                        command test "$@"
                        return $?
                      fi
                      return 0
                    }}
                """),
                encoding="utf-8",
            )
            script = textwrap.dedent(f"""
                printf 'y\\n' | BASH_ENV="{self.bash_path(env_file)}" \\
                HASHCAT_WPA_DATA_DIR="{self.bash_path(data_dir)}" \\
                HASHCAT_WPA_SERVICE_NAME="hashcat-test.service" \\
                bash ./bash/crackserver reset
                cat "{self.bash_path(calls_file)}"
            """)

            result = self.run_bash(script)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"Data path: {self.bash_path(data_dir)}", result.stdout)
        self.assertIn(f"find {self.bash_path(data_dir)} -mindepth 1 -maxdepth 1", result.stdout)
        self.assertNotIn("/var/lib/hashcat-wpa-server", result.stdout)

    def test_package_sudoers_allows_uninstall_path_overrides(self):
        postinst = (self.repo_root / "debian" / "postinst").read_text(encoding="utf-8")

        self.assertIn(
            "NOPASSWD: SETENV: /opt/hashcat-wpa-server/bash/uninstall_app.sh",
            postinst,
        )

    def test_service_unit_does_not_hardcode_bootstrap_password(self):
        service = (self.repo_root / "debian" / "hashcat-wpa-server.service").read_text(encoding="utf-8")

        self.assertIn('Environment="HASHCAT_WPA_SERVER_HOME=/var/lib/hashcat-wpa-server"', service)
        self.assertIn('Environment="HOME=/var/lib/hashcat-wpa-server"', service)
        self.assertNotIn("HASHCAT_ADMIN_PASSWORD", service)
        self.assertNotIn("changeme", service)


if __name__ == "__main__":
    unittest.main()
