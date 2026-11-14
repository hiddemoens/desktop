#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: GPL-2.0-or-later

"""Merge, build and install personal hdrive updates; never resolve conflicts."""

import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
BUNDLE_ID = 'com.nextcloud.desktopclient.findertest'
TEAM = 'BTQQVL2S6N'
BUILT_APP = 'hdriveDev.app'
APP = 'hdrive.app'
FIX_BRANCH = 'fix/macos-remove-download'
CLIENT_APPS = {APP, BUILT_APP, 'NextcloudDev.app'}


def command(*args, **kwargs):
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env.pop('GIT_EXEC_PATH', None)
    return subprocess.run([str(arg) for arg in args], check=True, env=env, **kwargs)


def git(repo, *args):
    return command('/opt/local/bin/git', '-C', repo, *args, stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, text=True, timeout=120).stdout.strip()


def ancestor(repo, older, newer):
    try:
        git(repo, 'merge-base', '--is-ancestor', older, newer)
        return True
    except subprocess.CalledProcessError as error:
        if error.returncode == 1:
            return False
        raise


def synchronize(repo):
    if git(repo, 'branch', '--show-current') != 'hdrive':
        raise RuntimeError('Checkout must be on hdrive branch; no branch was switched.')
    if git(repo, 'status', '--porcelain'):
        raise RuntimeError('Checkout has uncommitted work; update stopped.')
    git(repo, 'fetch', '--no-tags', 'upstream', '+refs/heads/master:refs/remotes/upstream/master')
    git(repo, 'fetch', '--no-tags', 'origin',
        f'+refs/heads/{FIX_BRANCH}:refs/remotes/origin/{FIX_BRANCH}',
        '+refs/heads/hdrive:refs/remotes/origin/hdrive')
    remote = 'origin/hdrive'
    if ancestor(repo, 'HEAD', remote):
        git(repo, 'merge', '--ff-only', remote)
    elif not ancestor(repo, remote, 'HEAD'):
        raise RuntimeError('Local and remote hdrive histories diverged; review required.')
    for source in ('upstream/master', 'origin/' + FIX_BRANCH):
        if ancestor(repo, source, 'HEAD'):
            continue
        try:
            git(repo, 'merge', '--no-ff', '-m',
                f'chore(hdrive): merge {source}\n\nAssisted-by: Codex:GPT-6', source)
        except subprocess.CalledProcessError as error:
            git(repo, 'merge', '--abort')
            raise RuntimeError(f'Cannot merge {source}; conflict requires review.') from error
    return git(repo, 'rev-parse', 'HEAD')


def verify_bundle(app):
    if app.is_symlink():
        raise RuntimeError('App bundle must not be a symlink.')
    with (app / 'Contents/Info.plist').open('rb') as source:
        info = plistlib.load(source)
    if info['CFBundleIdentifier'] != BUNDLE_ID:
        raise RuntimeError('Unexpected app bundle identifier.')
    if info['CFBundleExecutable'] not in ('hdriveDev', 'NextcloudDev'):
        raise RuntimeError('Unexpected app executable.')
    command('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
    details = command('/usr/bin/codesign', '-dvvv', app, capture_output=True, text=True).stderr
    if f'TeamIdentifier={TEAM}' not in details:
        raise RuntimeError('Unexpected app signing team.')
    for extension in ('FileProviderExt', 'FileProviderUIExt'):
        bundle = app / 'Contents/PlugIns' / (extension + '.appex')
        with (bundle / 'Contents/Info.plist').open('rb') as source:
            if plistlib.load(source)['CFBundleIdentifier'] != BUNDLE_ID + '.' + extension:
                raise RuntimeError('Unexpected File Provider extension identifier.')
    signature = re.search(r'^CDHash=(\w+)$', details, re.M)
    if not signature:
        raise RuntimeError('Signed app has no CodeDirectory hash.')
    return signature[1]


def prepare_storage(preferences):
    old = preferences / 'NextcloudDev'
    new = preferences / 'hdriveDev'
    if new.is_symlink() and new.resolve() == old.resolve():
        return
    if new.exists() or new.is_symlink():
        raise RuntimeError('hdrive storage already exists; review before replacing account storage.')
    if old.is_dir():
        new.symlink_to('NextcloudDev', target_is_directory=True)


def process_ids(app):
    with (app / 'Contents/Info.plist').open('rb') as source:
        executable = plistlib.load(source)['CFBundleExecutable']
    expected = str(app / 'Contents/MacOS' / executable)
    listing = command('/bin/ps', '-axo', 'pid=,comm=', capture_output=True, text=True).stdout
    return [int(parts[0]) for line in listing.splitlines()
            if len(parts := line.strip().split(None, 1)) == 2 and parts[1] == expected]


def stop_app(app):
    if not process_ids(app):
        return
    with (app / 'Contents/Info.plist').open('rb') as source:
        executable = plistlib.load(source)['CFBundleExecutable']
    command(app / 'Contents/MacOS' / executable, '--quit', timeout=20,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 20
    while process_ids(app):
        if time.monotonic() > deadline:
            raise RuntimeError('App did not quit; installation stopped.')
        time.sleep(0.5)


def start_app(app):
    command('/usr/bin/open', '-g', app, '--args', '--logdebug', '--logflush')
    deadline = time.monotonic() + 20
    while not process_ids(app):
        if time.monotonic() > deadline:
            raise RuntimeError('Installed app did not launch.')
        time.sleep(0.5)
    time.sleep(3)
    if not process_ids(app):
        raise RuntimeError('Installed app exited immediately after launch.')


def cleanup_bundles(outputs):
    if not outputs.exists():
        return
    marker = outputs / '.hdrive-build'
    if not marker.is_file() or marker.is_symlink() or marker.read_text().strip() != 'hdrive':
        raise RuntimeError('Cleanup requires a tool-owned hdrive build directory.')
    if outputs.is_symlink():
        raise RuntimeError('Build output root must not be a symlink.')
    for directory, folders, _ in os.walk(outputs, followlinks=False):
        for name in list(folders):
            path = Path(directory) / name
            if name in CLIENT_APPS:
                if path.is_symlink():
                    path.unlink()
                else:
                    shutil.rmtree(path)
                folders.remove(name)
            elif path.is_symlink():
                folders.remove(name)


def write_state(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def validate_receipt(app, receipt_file, commit):
    receipt = json.loads(receipt_file.read_text())
    if receipt.get('commit') != commit or receipt.get('signature') != verify_bundle(app):
        raise RuntimeError('Build receipt does not match requested commit and signed artifact.')


def recover_install(applications, state, *, verify=verify_bundle, stop=stop_app, start=start_app):
    journal = state / 'transaction.json'
    if not journal.exists():
        return
    transaction = json.loads(journal.read_text())
    previous_name = transaction['previous']
    if previous_name is not None and previous_name not in CLIENT_APPS:
        raise RuntimeError('Invalid interrupted installation state; review required.')
    retired = applications / '.hdrive-previous'
    stage = applications / '.hdrive-installing'
    destination = applications / APP
    previous = applications / previous_name if previous_name else None
    if transaction.get('phase') == 'installed':
        if verify(destination) != transaction['signature']:
            raise RuntimeError('Installed app changed during recovery; review required.')
        start(destination)
        if retired.is_symlink():
            raise RuntimeError('Retired bundle must not be a symlink.')
        if retired.exists():
            shutil.rmtree(retired)
    elif retired.exists():
        verify(retired)
        if previous is None or (previous != destination and previous.exists()):
            raise RuntimeError('Interrupted installation has conflicting app paths.')
        if destination.exists():
            if verify(destination) != transaction['signature']:
                raise RuntimeError('Installed app changed during recovery; review required.')
            stop(destination)
            shutil.rmtree(destination)
        retired.rename(previous)
        verify(previous)
        start(previous)
    elif previous and previous.exists():
        verify(previous)
        start(previous)
    elif previous:
        raise RuntimeError('Previous installation missing; restore previous-app.zip before retrying.')
    if stage.exists():
        if stage.is_symlink():
            raise RuntimeError('Installation staging must not be a symlink.')
        shutil.rmtree(stage)
    journal.unlink()


def install(candidate, applications, state, commit, outputs, *,
            verify=verify_bundle, stop=stop_app, start=start_app):
    signature = verify(candidate)
    state.mkdir(parents=True, exist_ok=True)
    applications.mkdir(parents=True, exist_ok=True)
    recover_install(applications, state, verify=verify, stop=stop, start=start)
    destination = applications / APP
    old_candidates = [applications / name for name in CLIENT_APPS if (applications / name).exists()]
    if len(old_candidates) > 1:
        raise RuntimeError('Multiple development installations exist; review before updating.')
    previous = old_candidates[0] if old_candidates else None
    if previous:
        verify(previous)
    stage = applications / '.hdrive-installing'
    retired = applications / '.hdrive-previous'
    if stage.exists() or stage.is_symlink() or retired.exists() or retired.is_symlink():
        raise RuntimeError('Previous installation staging remains; review required.')
    backup = state / 'previous-app.zip'
    journal = state / 'transaction.json'
    try:
        command('/usr/bin/ditto', candidate, stage)
        if verify(stage) != signature:
            raise RuntimeError('Staged signature differs from built app.')
        write_state(journal, {'previous': previous.name if previous else None, 'signature': signature})
        if previous:
            stop(previous)
            command('/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', previous, backup)
            command('/usr/bin/unzip', '-tq', backup, stdout=subprocess.DEVNULL)
            previous.rename(retired)
        stage.rename(destination)
        if verify(destination) != signature:
            raise RuntimeError('Installed signature differs from staged app.')
        start(destination)
    except BaseException:
        if journal.exists():
            recover_install(applications, state, verify=verify, stop=stop, start=start)
        elif stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        raise
    write_state(journal, {'previous': previous.name if previous else None,
                          'signature': signature, 'phase': 'installed'})
    if retired.exists():
        shutil.rmtree(retired)
    journal.unlink()
    cleanup_bundles(outputs)
    write_state(state / 'installed.json', {'commit': commit, 'app': str(destination), 'signature': signature})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=ROOT)
    parser.add_argument('--state-dir', type=Path, default=Path.home() / 'Library/Application Support/hdrive-updater')
    parser.add_argument('--build-dir', type=Path)
    parser.add_argument('--no-fetch', action='store_true', help='Build current hdrive commit without merging')
    parser.add_argument('--no-push', action='store_true', help='Do not publish hdrive merges')
    parser.add_argument('--check', action='store_true', help='Fetch and merge, but do not build or install')
    parser.add_argument('--install-built', metavar='COMMIT', help='Install already-built exact commit after verification')
    args = parser.parse_args()
    repo = args.repo.resolve()
    outputs = (args.build_dir or repo / 'build/hdrive').absolute()
    state = args.state_dir.expanduser().absolute()
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'update.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another hdrive update is running.') from error
        checkout_head = git(repo, 'rev-parse', 'HEAD')
        recover_install(Path('/Applications'), state)
        if args.install_built:
            commit = git(repo, 'rev-parse', args.install_built + '^{commit}')
            validate_receipt(outputs / 'product' / BUILT_APP, outputs / 'build-receipt.json', commit)
            if not ancestor(repo, commit, 'HEAD') or git(repo, 'branch', '--show-current') != 'hdrive':
                raise RuntimeError('Requested built commit must belong to current hdrive history.')
        elif args.no_fetch:
            if git(repo, 'branch', '--show-current') != 'hdrive' or git(repo, 'status', '--porcelain'):
                raise RuntimeError('Build requires clean hdrive checkout.')
            commit = git(repo, 'rev-parse', 'HEAD')
        else:
            commit = synchronize(repo)
            checkout_head = commit
        installed_file = state / 'installed.json'
        if installed_file.exists():
            installed = json.loads(installed_file.read_text())
            if (installed['commit'] == commit and Path(installed['app']) == Path('/Applications') / APP
                    and Path(installed['app']).is_dir()):
                if verify_bundle(Path(installed['app'])) != installed.get('signature'):
                    raise RuntimeError('Installed signature differs from recorded build; rebuild required.')
                cleanup_bundles(outputs)
                if not args.no_push:
                    git(repo, 'push', 'origin', 'HEAD:refs/heads/hdrive')
                print('hdrive already up to date.')
                return
        if args.check:
            print(f'hdrive update available: {commit}')
            return
        if not args.install_built:
            command(sys.executable, '-m', 'unittest', 'discover', '-s', repo / 'admin/osx/hdrive/tests', '-v', cwd=repo)
            command(sys.executable, repo / 'admin/osx/hdrive/build.py', '--repo', repo,
                    '--build-dir', outputs, '--commit', commit, cwd=repo)
        validate_receipt(outputs / 'product' / BUILT_APP, outputs / 'build-receipt.json', commit)
        if git(repo, 'rev-parse', 'HEAD') != checkout_head or git(repo, 'status', '--porcelain'):
            raise RuntimeError('Checkout changed during build; installation stopped.')
        if not args.no_push:
            git(repo, 'push', 'origin', 'HEAD:refs/heads/hdrive')
        preferences = Path.home() / 'Library/Containers' / BUNDLE_ID / 'Data/Library/Preferences'
        prepare_storage(preferences)
        install(outputs / 'product' / BUILT_APP, Path('/Applications'), state, commit, outputs)
        print(f'Installed hdrive {commit[:12]}; restarted app and removed build app copies.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        sys.exit(f'hdrive update stopped: {error}')
