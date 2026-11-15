"""Exercise real window placement on a private, non-input Windows desktop."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(os.name == 'nt', 'Native monitor placement requires Windows')
class NativeLeagueLayoutTests(unittest.TestCase):
    def test_client_coverage_for_captioned_borderless_and_refusing_windows(self):
        probe = Path(__file__).with_name('league_native_probe.py')
        result = subprocess.run(
            [sys.executable, str(probe), '--check-native-layout'],
            capture_output=True, text=True, timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        self.assertEqual({row['frame_kind'] for row in rows},
                         {'borderless', 'caption', 'caption_no_maximize', 'fixed_caption'})
        for row in rows:
            with self.subTest(frame=row['frame_kind']):
                self.assertFalse(row['actual_foreground'])
                self.assertTrue(row['test_substituted_foreground_gate'])
                self.assertFalse(row['win32_client_before']['coverage'])
                if row['frame_kind'] == 'fixed_caption':
                    self.assertFalse(row['win32_client_after']['coverage'])
                else:
                    self.assertTrue(row['win32_client_after']['coverage'])
                    self.assertEqual(row['win32_client_after']['client_size'],
                                     row['win32_client_after']['monitor_size'])
