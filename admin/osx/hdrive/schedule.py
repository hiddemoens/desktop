#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: GPL-2.0-or-later

"""Install a daily user launchd schedule; no AI process is involved."""

from pathlib import Path
import os
import plistlib
import subprocess

LABEL = 'nl.hdrive.desktop-updater'
ROOT = Path(__file__).resolve().parents[3]
PYTHON = '/opt/homebrew/opt/python@3.12/bin/python3.12'


def configuration(repo, home):
    logs = home / 'Library/Logs/hdrive-updater'
    return {
        'Label': LABEL,
        'ProgramArguments': [PYTHON, str(repo / 'admin/osx/hdrive/update.py')],
        'WorkingDirectory': str(repo),
        'StartCalendarInterval': {'Hour': 10, 'Minute': 0},
        'LimitLoadToSessionType': 'Aqua',
        'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
        'StandardOutPath': str(logs / 'update.log'),
        'StandardErrorPath': str(logs / 'error.log'),
    }


def main():
    if not Path(PYTHON).is_file():
        raise SystemExit('Existing Homebrew Python 3.12 installation is required.')
    home = Path.home()
    (home / 'Library/Logs/hdrive-updater').mkdir(parents=True, exist_ok=True)
    agents = home / 'Library/LaunchAgents'
    agents.mkdir(parents=True, exist_ok=True)
    path = agents / (LABEL + '.plist')
    settings = configuration(ROOT, home)
    temporary = path.with_suffix('.tmp')
    with temporary.open('wb') as target:
        plistlib.dump(settings, target)
    temporary.replace(path)
    domain = f'gui/{os.getuid()}'
    subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True)
    subprocess.run(['launchctl', 'print', domain + '/' + LABEL], check=True)
    print('Daily hdrive update installed for 10:00 local time.')


if __name__ == '__main__':
    main()
