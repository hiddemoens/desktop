# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: GPL-2.0-or-later

from pathlib import Path
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / 'admin/osx/hdrive/update.py'


def load_updater():
    spec = importlib.util.spec_from_file_location('hdrive_update', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def git(self, repo, *args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True, stderr=subprocess.STDOUT).strip()

    def repo(self, name):
        repo = self.root / name
        repo.mkdir()
        self.git(repo, 'init', '-q', '-b', 'master')
        self.git(repo, 'config', 'user.name', 'Fixture')
        self.git(repo, 'config', 'user.email', 'fixture@example.invalid')
        return repo

    def commit(self, repo, name, text):
        (repo / name).write_text(text)
        self.git(repo, 'add', '.')
        self.git(repo, 'commit', '-qm', 'test: fixture', '--trailer', 'Assisted-by: Codex:GPT-6')
        return self.git(repo, 'rev-parse', 'HEAD')

    def fixture(self):
        upstream = self.repo('upstream')
        self.commit(upstream, 'base', 'base')
        origin = self.repo('origin')
        self.git(origin, 'remote', 'add', 'upstream', str(upstream))
        self.git(origin, 'fetch', '-q', 'upstream')
        self.git(origin, 'reset', '--hard', 'upstream/master')
        self.git(origin, 'checkout', '-qb', 'fix/macos-remove-download')
        self.commit(origin, 'fix', 'fix')
        self.git(origin, 'checkout', '-qb', 'hdrive')
        self.commit(origin, 'brand', 'hdrive')
        local = self.root / 'local'
        subprocess.run(['git', 'clone', '-q', '-b', 'hdrive', str(origin), str(local)], check=True)
        self.git(local, 'config', 'user.name', 'Fixture')
        self.git(local, 'config', 'user.email', 'fixture@example.invalid')
        self.git(local, 'remote', 'add', 'upstream', str(upstream))
        return local, origin, upstream

    def test_merges_both_sources_and_skips_unchanged(self):
        updater = load_updater()
        local, origin, upstream = self.fixture()
        before = self.git(local, 'rev-parse', 'HEAD')
        self.assertEqual(updater.synchronize(local), before)
        self.commit(upstream, 'upstream-change', 'new upstream')
        self.git(origin, 'checkout', '-q', 'fix/macos-remove-download')
        self.commit(origin, 'personal-change', 'new fix')
        target = updater.synchronize(local)
        self.assertNotEqual(before, target)
        for name in ['brand', 'fix', 'personal-change', 'upstream-change']:
            self.assertTrue((local / name).exists())
        self.assertEqual(updater.synchronize(local), target)
        self.assertIn('Assisted-by:', self.git(local, 'log', '-1', '--format=%B'))
        self.assertEqual(self.git(local, 'status', '--porcelain'), '')

    def test_conflict_aborts_and_preserves_hdrive(self):
        updater = load_updater()
        local, _, upstream = self.fixture()
        before = self.commit(local, 'base', 'branded base')
        self.commit(upstream, 'base', 'conflicting base')
        with self.assertRaisesRegex(RuntimeError, 'conflict|merge'):
            updater.synchronize(local)
        self.assertEqual(self.git(local, 'rev-parse', 'HEAD'), before)
        self.assertEqual(self.git(local, 'status', '--porcelain'), '')
        self.assertEqual((local / 'base').read_text(), 'branded base')

    def test_dirty_checkout_is_preserved(self):
        updater = load_updater()
        local, _, _ = self.fixture()
        (local / 'brand').write_text('user work')
        with self.assertRaisesRegex(RuntimeError, 'uncommitted'):
            updater.synchronize(local)
        self.assertEqual((local / 'brand').read_text(), 'user work')

    def test_remote_divergence_is_rejected(self):
        updater = load_updater()
        local, origin, _ = self.fixture()
        self.commit(local, 'brand', 'local brand')
        self.commit(origin, 'brand', 'remote brand')
        with self.assertRaisesRegex(RuntimeError, 'diverg'):
            updater.synchronize(local)
        self.assertEqual((local / 'brand').read_text(), 'local brand')

    def test_install_success_cleans_only_managed_client_bundles(self):
        updater = load_updater()
        outputs = self.root / 'build'
        candidate = outputs / 'product/hdriveDev.app'
        candidate.mkdir(parents=True)
        (candidate / 'version').write_text('new')
        duplicate = outputs / 'craft/bin/hdriveDev.app'
        shutil.copytree(candidate, duplicate)
        unrelated = outputs / 'SomeDependency.app'
        unrelated.mkdir()
        apps = self.root / 'Applications'
        previous = apps / 'NextcloudDev.app'
        previous.mkdir(parents=True)
        (previous / 'version').write_text('old')
        state = self.root / 'state'
        (outputs / '.hdrive-build').write_text('hdrive')
        started = []
        def verify(app):
            self.assertTrue((app / 'version').exists())
        updater.install(candidate, apps, state, 'a' * 40, outputs,
                        verify=verify, stop=lambda app: None, start=lambda app: started.append(app))
        installed = apps / 'hdrive.app'
        self.assertEqual((installed / 'version').read_text(), 'new')
        self.assertEqual(started, [installed])
        self.assertFalse(previous.exists())
        self.assertFalse(candidate.exists())
        self.assertFalse(duplicate.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue((state / 'previous-app.zip').exists())
        self.assertEqual(json.loads((state / 'installed.json').read_text())['commit'], 'a' * 40)

    def test_launch_failure_restores_previous_app_without_success_record(self):
        updater = load_updater()
        outputs = self.root / 'build'
        candidate = outputs / 'product/hdriveDev.app'
        candidate.mkdir(parents=True)
        (candidate / 'version').write_text('new')
        apps = self.root / 'Applications'
        old = apps / 'hdriveDev.app'
        old.mkdir(parents=True)
        (old / 'version').write_text('old')
        state = self.root / 'state'
        (outputs / '.hdrive-build').write_text('hdrive')
        starts = []
        def start(app):
            starts.append(app)
            if len(starts) == 1:
                raise RuntimeError('launch failed')
        with self.assertRaisesRegex(RuntimeError, 'launch failed'):
            updater.install(candidate, apps, state, 'a' * 40, outputs,
                            verify=lambda app: None, stop=lambda app: None, start=start)
        self.assertEqual((old / 'version').read_text(), 'old')
        self.assertEqual(len(starts), 2)
        self.assertFalse((state / 'installed.json').exists())
        self.assertTrue(candidate.exists())

    def test_cleanup_never_follows_symlinks(self):
        updater = load_updater()
        outputs = self.root / 'build'
        outputs.mkdir()
        (outputs / '.hdrive-build').write_text('hdrive')
        real = self.root / 'Applications/hdriveDev.app'
        real.mkdir(parents=True)
        (real / 'keep').write_text('installed')
        (outputs / 'hdriveDev.app').symlink_to(real, target_is_directory=True)
        updater.cleanup_bundles(outputs)
        self.assertEqual((real / 'keep').read_text(), 'installed')
        self.assertFalse((outputs / 'hdriveDev.app').exists())

    def test_cleanup_refuses_unowned_directory(self):
        updater = load_updater()
        app = self.root / 'hdriveDev.app'
        app.mkdir()
        (app / 'keep').write_text('safe')
        with self.assertRaisesRegex(RuntimeError, 'owned'):
            updater.cleanup_bundles(self.root)
        self.assertEqual((app / 'keep').read_text(), 'safe')

    def test_missing_fix_branch_stops_without_installing(self):
        updater = load_updater()
        local, origin, _ = self.fixture()
        self.git(origin, 'branch', '-D', 'fix/macos-remove-download')
        before = self.git(local, 'rev-parse', 'HEAD')
        with self.assertRaises(subprocess.CalledProcessError):
            updater.synchronize(local)
        self.assertEqual(self.git(local, 'rev-parse', 'HEAD'), before)

    def test_wrong_checkout_branch_is_not_switched(self):
        updater = load_updater()
        local, _, _ = self.fixture()
        self.git(local, 'checkout', '-qb', 'contribution')
        with self.assertRaisesRegex(RuntimeError, 'hdrive branch'):
            updater.synchronize(local)
        self.assertEqual(self.git(local, 'branch', '--show-current'), 'contribution')

    def test_invalid_candidate_never_stops_existing_app(self):
        updater = load_updater()
        candidate = self.root / 'candidate.app'
        candidate.mkdir()
        stopped = []
        def verify(app):
            raise RuntimeError('invalid signature')
        with self.assertRaisesRegex(RuntimeError, 'invalid signature'):
            updater.install(candidate, self.root / 'Applications', self.root / 'state',
                            'a' * 40, self.root / 'build', verify=verify,
                            stop=lambda app: stopped.append(app), start=lambda app: None)
        self.assertEqual(stopped, [])
        self.assertFalse((self.root / 'state/installed.json').exists())

    def test_interrupted_install_recovers_old_bundle_before_retry(self):
        updater = load_updater()
        apps = self.root / 'Applications'
        apps.mkdir()
        retired = apps / '.hdrive-previous'
        retired.mkdir()
        (retired / 'version').write_text('old')
        new = apps / 'hdrive.app'
        new.mkdir()
        (new / 'version').write_text('new')
        state = self.root / 'state'
        state.mkdir()
        (state / 'transaction.json').write_text(json.dumps({'previous': 'NextcloudDev.app', 'signature': None}))
        started = []
        updater.recover_install(apps, state, verify=lambda app: None,
                                stop=lambda app: None, start=lambda app: started.append(app))
        self.assertEqual((apps / 'NextcloudDev.app/version').read_text(), 'old')
        self.assertFalse(new.exists())
        self.assertFalse(retired.exists())
        self.assertFalse((state / 'transaction.json').exists())
        self.assertEqual(started, [apps / 'NextcloudDev.app'])

    def test_interrupted_retired_cleanup_keeps_verified_new_app(self):
        updater = load_updater()
        apps = self.root / 'Applications'
        apps.mkdir()
        retired = apps / '.hdrive-previous'
        retired.mkdir()
        (retired / 'partial').write_text('partly deleted previous bundle')
        new = apps / 'hdrive.app'
        new.mkdir()
        (new / 'version').write_text('new')
        state = self.root / 'state'
        state.mkdir()
        (state / 'transaction.json').write_text(json.dumps({'previous': 'NextcloudDev.app', 'signature': 'signed-new', 'phase': 'installed'}))
        updater.recover_install(apps, state, verify=lambda app: 'signed-new',
                                stop=lambda app: None, start=lambda app: None)
        self.assertEqual((new / 'version').read_text(), 'new')
        self.assertFalse(retired.exists())
        self.assertFalse((state / 'transaction.json').exists())

    def test_explicit_install_uses_verified_earlier_commit_from_same_branch(self):
        updater = load_updater()
        local, _, _ = self.fixture()
        built = self.git(local, 'rev-parse', 'HEAD~1')
        outputs = self.root / 'build'
        outputs.mkdir()
        (outputs / 'build-receipt.json').write_text(json.dumps({'commit': built, 'signature': 'signed'}))
        args = ['update.py', '--repo', str(local), '--build-dir', str(outputs), '--state-dir', str(self.root / 'state'), '--install-built', built, '--no-push']
        with patch.object(sys, 'argv', args), patch.object(updater, 'verify_bundle', return_value='signed'), patch.object(updater, 'recover_install'), patch.object(updater, 'prepare_storage'), patch.object(updater, 'install') as install:
            updater.main()
        self.assertEqual(install.call_args.args[3], built)
        self.assertNotEqual(built, self.git(local, 'rev-parse', 'HEAD'))

    def test_git_uses_normal_keychain_approved_installation(self):
        updater = load_updater()
        def command(*args, **kwargs):
            self.assertEqual(args[0], '/opt/local/bin/git')
            return subprocess.CompletedProcess(args, 0, stdout='revision\n')
        with patch.object(updater, 'command', side_effect=command):
            self.assertEqual(updater.git(self.root, 'rev-parse', 'HEAD'), 'revision')

    def test_verifier_requests_full_code_directory_hash(self):
        updater = load_updater()
        import plistlib
        candidate = self.root / 'hdriveDev.app'
        bundles = [(candidate, updater.BUNDLE_ID, 'hdriveDev')]
        for extension in ['FileProviderExt', 'FileProviderUIExt']:
            bundles.append((candidate / 'Contents/PlugIns' / (extension + '.appex'), updater.BUNDLE_ID + '.' + extension, extension))
        for bundle, identifier, executable in bundles:
            (bundle / 'Contents').mkdir(parents=True)
            with (bundle / 'Contents/Info.plist').open('wb') as target:
                plistlib.dump({'CFBundleIdentifier': identifier, 'CFBundleExecutable': executable}, target)
        def command(*args, **kwargs):
            details = 'TeamIdentifier=' + updater.TEAM + '\n'
            if '-dvvv' in args:
                details += 'CDHash=0123456789abcdef\n'
            return subprocess.CompletedProcess(args, 0, stdout='', stderr=details)
        with patch.object(updater, 'command', side_effect=command):
            self.assertEqual(updater.verify_bundle(candidate), '0123456789abcdef')

    def test_stale_product_cannot_use_new_source_commit(self):
        updater = load_updater()
        receipt = self.root / 'build-receipt.json'
        receipt.write_text(json.dumps({'commit': 'a' * 40, 'signature': 'old-signature'}))
        with patch.object(updater, 'verify_bundle', return_value='new-signature'):
            with self.assertRaisesRegex(RuntimeError, 'receipt'):
                updater.validate_receipt(self.root / 'hdriveDev.app', receipt, 'a' * 40)
        with patch.object(updater, 'verify_bundle', return_value='old-signature'):
            with self.assertRaisesRegex(RuntimeError, 'receipt'):
                updater.validate_receipt(self.root / 'hdriveDev.app', receipt, 'b' * 40)
            updater.validate_receipt(self.root / 'hdriveDev.app', receipt, 'a' * 40)

    def test_shutdown_uses_supported_quit_workflow(self):
        updater = load_updater()
        app = self.root / 'NextcloudDev.app'
        (app / 'Contents').mkdir(parents=True)
        import plistlib
        with (app / 'Contents/Info.plist').open('wb') as target:
            plistlib.dump({'CFBundleExecutable': 'NextcloudDev'}, target)
        with patch.object(updater, 'process_ids', side_effect=[[42], []]), patch.object(updater, 'command') as command:
            updater.stop_app(app)
        self.assertIn('--quit', command.call_args.args)
        self.assertEqual(command.call_args.args[0], app / 'Contents/MacOS/NextcloudDev')

    def test_lock_prevents_concurrent_update(self):
        import fcntl
        state = self.root / 'state'
        state.mkdir()
        with (state / 'update.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run([sys.executable, str(SCRIPT), '--state-dir', str(state), '--no-fetch'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Another hdrive update', result.stderr)

    def test_storage_alias_reuses_existing_config_and_rejects_conflicts(self):
        updater = load_updater()
        preferences = self.root / 'Preferences'
        existing = preferences / 'NextcloudDev'
        existing.mkdir(parents=True)
        (existing / 'nextclouddev.cfg').write_text('account')
        updater.prepare_storage(preferences)
        self.assertTrue((preferences / 'hdriveDev').is_symlink())
        self.assertEqual((preferences / 'hdriveDev/nextclouddev.cfg').read_text(), 'account')
        updater.prepare_storage(preferences)
        (preferences / 'hdriveDev').unlink()
        (preferences / 'hdriveDev').mkdir()
        with self.assertRaisesRegex(RuntimeError, 'storage'):
            updater.prepare_storage(preferences)


if __name__ == '__main__':
    unittest.main()
