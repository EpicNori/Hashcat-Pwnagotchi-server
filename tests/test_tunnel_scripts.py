import shutil
import subprocess
import unittest
from pathlib import Path


class TunnelScriptTests(unittest.TestCase):
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

    def test_cloudflare_dry_run_redacts_token_and_skips_systemd_requirement(self):
        secret = "cf-secret-token-123"
        result = self.run_bash(
            f"printf '%s\\n' '{secret}' | HASHCAT_WPA_DRY_RUN=1 "
            "bash ./bash/install_cloudflare_tunnel.sh UPLOAD.Example.COM\n"
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("cloudflared service install <redacted-token>", result.stdout)
        self.assertIn("https://upload.example.com", result.stdout)
        self.assertIn("[dry-run] systemctl enable --now cloudflared", result.stdout)
        self.assertNotIn(secret, result.stdout)

    def test_cloudflare_rejects_invalid_public_hostnames(self):
        invalid_hostnames = [
            "https://upload.example.com",
            "bad..example.com",
            "bad-.example.com",
            "-bad.example.com",
            "upload",
        ]

        for hostname in invalid_hostnames:
            with self.subTest(hostname=hostname):
                result = self.run_bash(
                    "printf '%s\\n' 'cf-secret-token-123' | HASHCAT_WPA_DRY_RUN=1 "
                    f"bash ./bash/install_cloudflare_tunnel.sh '{hostname}'\n"
                )

                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertNotIn("cloudflared service install", result.stdout)

    def test_cloudflare_requires_token_even_in_dry_run(self):
        result = self.run_bash(
            "HASHCAT_WPA_DRY_RUN=1 bash ./bash/install_cloudflare_tunnel.sh upload.example.com </dev/null\n"
        )

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("Cloudflare Tunnel token is required.", result.stdout)

    def test_tailscale_dry_run_redacts_auth_key_from_stdin(self):
        secret = "tskey-auth-secret-123"
        result = self.run_bash(
            f"printf '%s\\n' '{secret}' | HASHCAT_WPA_DRY_RUN=1 "
            "bash ./bash/install_tailscale.sh\n"
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("tailscale up --authkey=<redacted> --reset", result.stdout)
        self.assertIn("Tailscale would be active.", result.stdout)
        self.assertNotIn(secret, result.stdout)

    def test_tailscale_dry_run_supports_existing_configuration(self):
        result = self.run_bash("HASHCAT_WPA_DRY_RUN=1 bash ./bash/install_tailscale.sh </dev/null\n")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("[dry-run] tailscale up", result.stdout)
        self.assertIn("Tailscale would be active.", result.stdout)


if __name__ == "__main__":
    unittest.main()
