import shutil
import subprocess
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


if __name__ == "__main__":
    unittest.main()
