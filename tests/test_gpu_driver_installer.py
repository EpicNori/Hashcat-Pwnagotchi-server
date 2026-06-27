import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class GpuDriverInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        cls.bash = str(git_bash) if git_bash.exists() else shutil.which("bash")

    def run_installer(self, lspci_output, fake_packages, os_release, ubuntu_drivers_devices="",
                      nvidia_smi_l_output="", hashcat_info_output="", create_hashcat=True,
                      ignore_host_gpu_tools=True, machine_arch="x86_64"):
        if self.bash is None:
            self.skipTest("bash is not available")
        with tempfile.TemporaryDirectory(prefix="hashcat-gpu-test-") as temp_name:
            root = Path(temp_name)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            progress_file = root / "gpu.progress"
            os_release_file = root / "os-release"
            os_release_file.write_text(os_release, encoding="utf-8")
            (bin_dir / "uname").write_text(
                "#!/bin/bash\n"
                "case \"${1:-}\" in\n"
                f"  -m) echo \"{machine_arch}\" ;;\n"
                "  -r) echo \"test-kernel\" ;;\n"
                "  *) /usr/bin/uname \"$@\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            (bin_dir / "uname").chmod(0o755)
            (bin_dir / "lspci").write_text(
                "#!/bin/bash\ncat <<'EOF'\n" + lspci_output + "\nEOF\n",
                encoding="utf-8",
            )
            (bin_dir / "lspci").chmod(0o755)
            if nvidia_smi_l_output:
                (bin_dir / "nvidia-smi").write_text(
                    "#!/bin/bash\n"
                    "if [ \"${1:-}\" = \"-L\" ]; then\n"
                    "cat <<'EOF'\n" + nvidia_smi_l_output + "\nEOF\n"
                    "exit 0\n"
                    "fi\n"
                    "exit 0\n",
                    encoding="utf-8",
                )
            else:
                (bin_dir / "nvidia-smi").write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
            (bin_dir / "nvidia-smi").chmod(0o755)
            if create_hashcat:
                (bin_dir / "hashcat").write_text(
                    "#!/bin/bash\n"
                    "if [ \"${1:-}\" = \"-I\" ]; then\n"
                    "cat <<'EOF'\n" + hashcat_info_output + "\nEOF\n"
                    "exit 0\n"
                    "fi\n"
                    "exit 0\n",
                    encoding="utf-8",
                )
                (bin_dir / "hashcat").chmod(0o755)
            (bin_dir / "rocminfo").write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
            (bin_dir / "rocminfo").chmod(0o755)
            (bin_dir / "rocm-smi").write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
            (bin_dir / "rocm-smi").chmod(0o755)
            (bin_dir / "clinfo").write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
            (bin_dir / "clinfo").chmod(0o755)

            def bash_path(path):
                raw = str(path.resolve())
                if len(raw) >= 3 and raw[1:3] == ":\\":
                    drive = raw[0].lower()
                    return "/" + drive + raw[2:].replace("\\", "/")
                return raw.replace("\\", "/")

            script = textwrap.dedent(f"""
                set -euo pipefail
                export PATH="{bash_path(bin_dir)}:$PATH"
                export HASHCAT_WPA_DRY_RUN=1
                export HASHCAT_WPA_IGNORE_HOST_PCI=1
                export HASHCAT_WPA_IGNORE_HOST_GPU_TOOLS={"1" if ignore_host_gpu_tools else "0"}
                export HASHCAT_WPA_TEST_LSPCI_OUTPUT="{lspci_output}"
                export HASHCAT_WPA_TEST_UBUNTU_DRIVERS_DEVICES="{ubuntu_drivers_devices}"
                export HASHCAT_WPA_FAKE_APT_PACKAGES="{fake_packages}"
                export HASHCAT_WPA_TEST_OS_RELEASE="{bash_path(os_release_file)}"
                export HASHCAT_WPA_NVIDIA_PROGRESS_FILE="{bash_path(progress_file)}"
                bash ./bash/install_gpu_drivers.sh check
                echo "PROGRESS=$(cat "{bash_path(progress_file)}")"
            """)
            return subprocess.run(
                [self.bash, "-lc", script],
                cwd=self.repo_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
            )

    def test_nvidia_gpu_installs_ubuntu_recommended_driver_package(self):
        result = self.run_installer(
            "01:00.0 VGA compatible controller: NVIDIA Corporation GeForce RTX 4070",
            "pciutils ubuntu-drivers-common nvidia-driver-535 nvidia-driver-570",
            'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\nID_LIKE=debian\n',
            "driver   : nvidia-driver-570 - distro non-free recommended",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("NVIDIA GPU detected", result.stdout)
        self.assertIn("apt-get install -y nvidia-driver-570", result.stdout)
        self.assertIn("PROGRESS=success|100|NVIDIA driver installation completed", result.stdout)

    def test_nvidia_gpu_uses_newest_available_ubuntu_driver_fallback(self):
        result = self.run_installer(
            "01:00.0 3D controller: NVIDIA Corporation AD104GL [RTX 4000 SFF Ada Generation]",
            "pciutils ubuntu-drivers-common nvidia-driver-535 nvidia-driver-575",
            'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\nID_LIKE=debian\n',
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("apt-get install -y nvidia-driver-575", result.stdout)
        self.assertIn("PROGRESS=success|100|NVIDIA driver installation completed", result.stdout)

    def test_nvidia_smi_visible_but_hashcat_missing_runtime_installs_stack(self):
        result = self.run_installer(
            "01:00.0 VGA compatible controller: NVIDIA Corporation GeForce RTX 4070",
            "pciutils ubuntu-drivers-common nvidia-driver-570 ocl-icd-libopencl1 clinfo nvidia-opencl-icd-570",
            'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\nID_LIKE=debian\n',
            ubuntu_drivers_devices="driver   : nvidia-driver-570 - distro non-free recommended",
            nvidia_smi_l_output="GPU 0: NVIDIA GeForce RTX 4070 (UUID: GPU-test)",
            hashcat_info_output=textwrap.dedent("""\
                Backend Device ID #1
                  Name...........: pthread-AMD Ryzen CPU
                  Device Type....: CPU
            """),
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("apt-get install -y nvidia-driver-570", result.stdout)
        self.assertIn("apt-get install -y ocl-icd-libopencl1 clinfo", result.stdout)
        self.assertIn("apt-get install -y nvidia-opencl-icd-570", result.stdout)

    def test_nvidia_smi_visible_but_hashcat_missing_does_not_skip_install(self):
        result = self.run_installer(
            "",
            "pciutils ubuntu-drivers-common nvidia-driver-570 ocl-icd-libopencl1 clinfo nvidia-opencl-icd-570",
            'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\nID_LIKE=debian\n',
            ubuntu_drivers_devices="driver   : nvidia-driver-570 - distro non-free recommended",
            nvidia_smi_l_output="GPU 0: NVIDIA GeForce RTX 4070 (UUID: GPU-test)",
            create_hashcat=False,
            ignore_host_gpu_tools=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("NVIDIA GPU detected", result.stdout)
        self.assertIn("apt-get install -y nvidia-driver-570", result.stdout)
        self.assertNotIn("NVIDIA runtime is already available", result.stdout)

    def test_debian_nvidia_requires_driver_package_not_only_firmware(self):
        result = self.run_installer(
            "01:00.0 VGA compatible controller: NVIDIA Corporation GeForce RTX 4070",
            "pciutils firmware-misc-nonfree",
            'ID=debian\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\nID_LIKE=debian\n',
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("nvidia-driver package is not available", result.stdout)

    def test_amd_gpu_installs_rocm_opencl_runtime(self):
        result = self.run_installer(
            "03:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Radeon RX 7900 XT",
            "pciutils curl ca-certificates gnupg ocl-icd-libopencl1 clinfo rocm-opencl-runtime rocminfo rocm-smi",
            'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\nID_LIKE=debian\n',
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("AMD GPU detected", result.stdout)
        self.assertIn("rocm-opencl-runtime", result.stdout)
        self.assertIn("PROGRESS=success|100|AMD ROCm/OpenCL runtime installation completed", result.stdout)

    def test_amd_gpu_arm64_reuses_distro_rocm_opencl_runtime(self):
        result = self.run_installer(
            "03:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Radeon RX 7900 XT",
            "pciutils curl ca-certificates gnupg ocl-icd-libopencl1 clinfo rocm-opencl-runtime rocminfo rocm-smi",
            'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\nID_LIKE=debian\n',
            machine_arch="aarch64",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("AMD GPU detected", result.stdout)
        self.assertIn("rocm-opencl-runtime", result.stdout)
        self.assertNotIn("not supported on this architecture", result.stdout)
        self.assertIn("PROGRESS=success|100|AMD ROCm/OpenCL runtime installation completed", result.stdout)

    def test_amd_gpu_arm64_skips_upstream_rocm_repo_and_uses_opencl_fallback(self):
        result = self.run_installer(
            "03:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Radeon RX 7600",
            "pciutils curl ca-certificates gnupg ocl-icd-libopencl1 clinfo mesa-opencl-icd",
            'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\nID_LIKE=debian\n',
            machine_arch="aarch64",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("AMD upstream ROCm repository setup is only available for amd64 packages", result.stdout)
        self.assertIn("mesa-opencl-icd", result.stdout)
        self.assertNotIn("write /etc/apt/sources.list.d/rocm.list", result.stdout)
        self.assertIn("PROGRESS=success|100|AMD ROCm/OpenCL runtime installation completed", result.stdout)


if __name__ == "__main__":
    unittest.main()
