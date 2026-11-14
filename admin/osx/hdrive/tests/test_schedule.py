# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: GPL-2.0-or-later

import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]


class ScheduleTests(unittest.TestCase):
    def test_launch_agent_runs_updater_directly_at_ten(self):
        spec = importlib.util.spec_from_file_location('hdrive_schedule', ROOT / 'admin/osx/hdrive/schedule.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='hdrive schedule ') as temporary:
            home = Path(temporary)
            repo = home / 'repo with spaces'
            settings = module.configuration(repo, home)
            roundtrip = plistlib.loads(plistlib.dumps(settings))
            self.assertEqual(roundtrip['StartCalendarInterval'], {'Hour': 10, 'Minute': 0})
            self.assertEqual(roundtrip['WorkingDirectory'], str(repo))
            self.assertEqual(roundtrip['ProgramArguments'], ['/opt/homebrew/opt/python@3.12/bin/python3.12', str(repo / 'admin/osx/hdrive/update.py')])
            self.assertFalse(roundtrip.get('RunAtLoad', False))
            self.assertFalse(roundtrip.get('KeepAlive', False))
            self.assertTrue(roundtrip['StandardErrorPath'].startswith(str(home)))


if __name__ == '__main__':
    unittest.main()
