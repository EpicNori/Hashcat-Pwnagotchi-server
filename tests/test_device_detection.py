import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_TEST_HOME = Path(tempfile.mkdtemp(prefix="hashcat-device-tests-"))
os.environ["HASHCAT_WPA_SERVER_HOME"] = str(_TEST_HOME)
os.environ["HASHCAT_WPA_SKIP_STARTUP_MAINTENANCE"] = "1"
_TEST_HOME.mkdir(parents=True, exist_ok=True)

from app.utils import utils as device_utils
from app.config import ADMIN_SETTINGS_PATH
from app.utils import settings as device_settings


class DeviceDetectionTests(unittest.TestCase):
    def setUp(self):
        device_utils.get_hashcat_devices.cache_clear()
        ADMIN_SETTINGS_PATH.unlink(missing_ok=True)

    def tearDown(self):
        device_utils.get_hashcat_devices.cache_clear()
        ADMIN_SETTINGS_PATH.unlink(missing_ok=True)

    def test_nvidia_smi_only_gpu_is_not_marked_hashcat_usable(self):
        def fail_hashcat_detection(_args, cwd=None):
            raise RuntimeError("hashcat cannot enumerate devices")

        with mock.patch.object(device_utils, "subprocess_call", side_effect=fail_hashcat_detection), \
                mock.patch.object(
                    device_utils.subprocess,
                    "check_output",
                    return_value="0, NVIDIA GeForce RTX 4070, 12282 MiB\n",
                ), \
                mock.patch.object(device_utils, "get_linux_pci_gpus", return_value=[]):
            devices = device_utils.get_hashcat_devices()

        gpu = next(device for device in devices if device["id"] == "0")
        self.assertTrue(gpu["is_gpu"])
        self.assertFalse(gpu["hashcat_usable"])

    def test_hashcat_detected_nvidia_gpu_remains_hashcat_usable_after_nvidia_smi_merge(self):
        hashcat_output = """\
Backend Device ID #0
  Name...........: NVIDIA GeForce RTX 4070
  Device Type....: GPU
  Memory.Total...: 12282 MB
"""

        with mock.patch.object(device_utils, "subprocess_call", return_value=(hashcat_output, "")), \
                mock.patch.object(
                    device_utils.subprocess,
                    "check_output",
                    return_value="0, NVIDIA GeForce RTX 4070, 12282 MiB\n",
                ), \
                mock.patch.object(device_utils, "get_linux_pci_gpus", return_value=[]):
            devices = device_utils.get_hashcat_devices()

        gpu = next(device for device in devices if device["id"] == "0")
        self.assertTrue(gpu["is_gpu"])
        self.assertTrue(gpu["hashcat_usable"])

    def test_new_zero_index_hashcat_gpu_is_enabled_by_default(self):
        devices = [
            {"id": "0", "name": "NVIDIA GeForce RTX 4070", "memory": "12 GB", "is_gpu": True, "hashcat_usable": True},
        ]

        with mock.patch.object(device_settings, "get_hashcat_devices", return_value=devices), \
                mock.patch.object(device_settings, "is_arm_host", return_value=False):
            self.assertEqual(device_settings.enabled_hashcat_device_ids(), ["0"])
            self.assertEqual(device_settings.default_hashcat_device_ids(), ["0"])
            self.assertEqual(device_settings.apply_hashcat_limits(["--quiet"]), [
                "--quiet",
                "-d",
                "0",
                "--workload-profile=4",
            ])

    def test_arm_host_with_usable_gpu_keeps_device_acceleration_args(self):
        devices = [
            {"id": "0", "name": "AMD Radeon RX 7900 XT", "memory": "20 GB", "is_gpu": True, "hashcat_usable": True},
        ]

        with mock.patch.object(device_settings, "get_hashcat_devices", return_value=devices), \
                mock.patch.object(device_settings, "is_arm_host", return_value=True):
            self.assertEqual(device_settings.apply_hashcat_limits(["--quiet"]), [
                "--quiet",
                "-d",
                "0",
                "--workload-profile=4",
            ])

    def test_arm_host_without_usable_gpu_keeps_cpu_safe_hashcat_args(self):
        devices = [
            {"id": "1", "name": "pthread ARM CPU", "memory": "4 GB", "is_gpu": False, "hashcat_usable": True},
        ]

        with mock.patch.object(device_settings, "get_hashcat_devices", return_value=devices), \
                mock.patch.object(device_settings, "is_arm_host", return_value=True):
            args = device_settings.apply_hashcat_limits(["--quiet", "--backend-ignore-opencl"])

        self.assertIn("--quiet", args)
        self.assertNotIn("-d", args)
        self.assertNotIn("--backend-ignore-opencl", args)
        self.assertIn("-D", args)
        self.assertEqual(args[args.index("-D") + 1], "1")
        self.assertIn("--backend-ignore-hip", args)

    def test_explicit_zero_intensity_disables_new_hashcat_gpu(self):
        devices = [
            {"id": "0", "name": "NVIDIA GeForce RTX 4070", "memory": "12 GB", "is_gpu": True, "hashcat_usable": True},
        ]
        ADMIN_SETTINGS_PATH.write_text(
            json.dumps({
                "device_intensities": {"0": 0},
                "cpu_percent": 100,
                "default_devices": ["0"],
            }),
            encoding="utf-8",
        )

        with mock.patch.object(device_settings, "get_hashcat_devices", return_value=devices):
            self.assertEqual(device_settings.enabled_hashcat_device_ids(), [])


if __name__ == "__main__":
    unittest.main()
