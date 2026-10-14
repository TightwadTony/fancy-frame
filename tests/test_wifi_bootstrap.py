"""Exercise bootstrap decisions without changing the host's network or services."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class BootstrapTests(unittest.TestCase):
    def run_bootstrap(self, *, configured=False, saved=False, connected=False, forced=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = (Path(__file__).resolve().parents[1] / 'scripts/wifi_bootstrap.sh').read_text()
            for path in ('/var/lib/fancy-frame/wifi-configured',
                         '/etc/wpa_supplicant/wpa_supplicant.conf',
                         '/etc/wpa_supplicant/wpa_supplicant-wlan0.conf',
                         '/boot/firmware/force-onboarding', '/boot/force-onboarding'):
                replacement = root / path.lstrip('/')
                replacement.parent.mkdir(parents=True, exist_ok=True)
                script = script.replace(path, str(replacement))
            flag = root / 'var/lib/fancy-frame/wifi-configured'
            if configured:
                flag.touch()
            if saved:
                (root / 'etc/wpa_supplicant/wpa_supplicant-wlan0.conf').write_text('network={\nssid="Home"\n}\n')
            if forced:
                (root / 'boot/firmware/force-onboarding').touch()
            for name, body in {
                'systemctl': 'echo "$*" >> "$TEST_LOG"',
                'sleep': 'echo sleep >> "$TEST_LOG"',
                'iwgetid': 'echo Home' if connected else 'exit 0',
                'wpa_cli': 'exit 0',
            }.items():
                command = root / name
                command.write_text('#!/bin/sh\n' + body + '\n')
                command.chmod(0o755)
            log = root / 'calls'
            subprocess.run(['bash', '-c', script], check=True, env={
                **os.environ, 'PATH': f'{root}:{os.environ["PATH"]}', 'TEST_LOG': str(log),
            })
            return log.read_text().splitlines() if log.exists() else [], flag.exists()

    def test_fresh_boot_starts_ap_without_waiting(self):
        calls, configured = self.run_bootstrap()
        self.assertEqual(calls, ['start fancy-frame-setup-mode.service', 'start fancy-frame-setup-portal.service'])
        self.assertFalse(configured)

    def test_preconfigured_connection_is_remembered(self):
        calls, configured = self.run_bootstrap(saved=True, connected=True)
        self.assertEqual(calls, [])
        self.assertTrue(configured)

    def test_known_and_provisioned_frames_keep_connection_window(self):
        for options in ({'configured': True}, {'saved': True}):
            with self.subTest(options=options):
                calls, _ = self.run_bootstrap(**options)
                self.assertEqual(calls.count('sleep'), 60)
                self.assertEqual(calls[-1], 'start fancy-frame-setup-portal.service')

    def test_forced_onboarding_still_wins(self):
        calls, _ = self.run_bootstrap(configured=True, connected=True, forced=True)
        self.assertEqual(calls, ['start fancy-frame-setup-mode.service', 'start fancy-frame-setup-portal.service'])
