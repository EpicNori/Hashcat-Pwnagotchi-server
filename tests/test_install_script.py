import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class InstallScriptTests(unittest.TestCase):
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

    def test_optional_tailscale_install_failure_does_not_abort_installer(self):
        with tempfile.TemporaryDirectory(prefix="hashcat-install-test-") as temp_name:
            root = Path(temp_name)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            progress_file = root / "progress.txt"
            (bin_dir / "curl").write_text("#!/bin/bash\nexit 22\n", encoding="utf-8")
            (bin_dir / "curl").chmod(0o755)
            (bin_dir / "sh").write_text("#!/bin/bash\ncat >/dev/null\nexit 0\n", encoding="utf-8")
            (bin_dir / "sh").chmod(0o755)

            script = textwrap.dedent(f"""
                set -euo pipefail
                export PATH="{self.bash_path(bin_dir)}:$PATH"
                export HASHCAT_WPA_INSTALL_SOURCE_ONLY=1
                export HASHCAT_WPA_PROGRESS_FILE="{self.bash_path(progress_file)}"
                source ./install.sh
                install_tailscale_for_remote_access
                echo "STATUS=$TAILSCALE_STATUS"
            """)

            result = self.run_bash(script)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Optional Tailscale install failed", result.stdout)
        self.assertIn("STATUS=manual-required", result.stdout)

    def test_optional_tailscale_install_can_be_skipped(self):
        result = self.run_bash(
            "export HASHCAT_WPA_INSTALL_SOURCE_ONLY=1; "
            "export HASHCAT_WPA_SKIP_TAILSCALE_INSTALL=1; "
            "source ./install.sh; "
            "install_tailscale_for_remote_access; "
            "echo STATUS=$TAILSCALE_STATUS\n"
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Skipping optional Tailscale install", result.stdout)
        self.assertIn("STATUS=skipped", result.stdout)


if __name__ == "__main__":
    unittest.main()
