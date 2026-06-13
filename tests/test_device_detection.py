import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_TEST_HOME = Path(tempfile.mkdtemp(prefix="hashcat-device-tests-"))
os.environ["HASHCAT_WPA_SERVER_HOME"] = str(_TEST_HOME)
os.environ["HASHCAT_WPA_SKIP_STARTUP_MAINTENANCE"] = "1"
_TEST_HOME.mkdir(parents=True, exist_ok=True)

from app.utils import utils as device_utils


class DeviceDetectionTests(unittest.TestCase):
    def setUp(self):
        device_utils.get_hashcat_devices.cache_clear()

    def tearDown(self):
        device_utils.get_hashcat_devices.cache_clear()

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


if __name__ == "__main__":
    unittest.main()
