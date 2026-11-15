"""Actual native tests are confined to a non-input desktop and bounded process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "Actual desktop APIs require Windows")
class NativeDesktopTests(unittest.TestCase):
    def test_real_overlay_and_disposable_editor_on_non_input_desktop(self):
        probe = Path(__file__).with_name("native_overlay_probe.py")
        with tempfile.TemporaryDirectory(prefix="chuddy-native-desktop-") as folder:
            report = Path(folder)/"report.json"
            run = subprocess.run([sys.executable, str(probe), "--isolated-desktop", "--report", str(report)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(run.returncode, 0, run.stderr or run.stdout)
            result = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertFalse(result["visible_desktop_switched"])
            self.assertFalse(result["real_mouse"])
            self.assertFalse(result["retailer_launched"])
            self.assertFalse(result["audio"])
            self.assertGreaterEqual(len(result["checks"]), 35)


if __name__ == "__main__":
    unittest.main()
