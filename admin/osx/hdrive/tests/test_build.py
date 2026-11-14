# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: GPL-2.0-or-later

from pathlib import Path
import subprocess
import tempfile
import unittest
import shutil
import importlib.util
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]


class BrandingTests(unittest.TestCase):
    def test_finder_bundle_display_names_use_hdrive(self):
        import plistlib
        main = (ROOT / 'cmake/modules/MacOSXBundleInfo.plist.in').read_text()
        main = main.replace('@MACOSX_BUNDLE_LOCALIZATIONS@', '')
        self.assertEqual(plistlib.loads(main.encode())['CFBundleDisplayName'], 'hdrive')
        self.assertEqual(plistlib.loads(main.encode())['CFBundleExecutable'], '@APPLICATION_NAME@')
        for extension in ['FileProviderExt', 'FileProviderUIExt']:
            info = plistlib.loads((ROOT / 'shell_integration/MacOSX/NextcloudIntegration' / extension / 'Info.plist').read_bytes())
            self.assertEqual(info['CFBundleDisplayName'], 'hdrive')

    def test_development_branding_preserves_account_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / 'branding.cmake'
            script.write_text(f'''set(APPLE TRUE)
set(NEXTCLOUD_DEV ON)
set(CMAKE_SOURCE_DIR "{ROOT}")
include("{ROOT}/NEXTCLOUD.cmake")
foreach(pair "APPLICATION_NAME;hdriveDev" "APPLICATION_SHORTNAME;NextcloudDev" "APPLICATION_EXECUTABLE;nextclouddev" "APPLICATION_CONFIG_NAME;nextclouddev" "APPLICATION_REV_DOMAIN;com.nextcloud.desktopclient.findertest" "APPLICATION_ICON_NAME;hdrive" "APPLICATION_SERVER_URL;https://hdrive.nl")
 list(GET pair 0 key)
 list(GET pair 1 expected)
 if(NOT "${{${{key}}}}" STREQUAL "${{expected}}")
  message(FATAL_ERROR "${{key}}: ${{${{key}}}} != ${{expected}}")
 endif()
endforeach()
if(APPLICATION_SERVER_URL_ENFORCE OR MACOS_USE_ICON_COMPOSER)
 message(FATAL_ERROR "Server must remain editable and hdrive icon must be used")
endif()
''')
            result = subprocess.run(['cmake', '-P', str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PreparationTests(unittest.TestCase):
    def test_builds_committed_sources_without_local_edits(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / 'repo'
            repo.mkdir()
            for name in ['NEXTCLOUD.cmake', 'craftmaster.ini', 'cmake/modules/GenerateIconsUtils.cmake', 'admin/osx/mac-crafter/Sources/Commands/Build.swift']:
                destination = repo / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, destination)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
            git('init', '-q', '-b', 'hdrive')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            git('add', '.')
            git('commit', '-qm', 'test: fixture', '--trailer', 'Assisted-by: Codex:GPT-6')
            commit = git('rev-parse', 'HEAD')
            (repo / 'NEXTCLOUD.cmake').write_text('uncommitted')
            state = Path(temporary) / 'build'
            result = subprocess.run(['python3', str(ROOT / 'admin/osx/hdrive/build.py'), '--prepare-only', '--repo', str(repo), '--build-dir', str(state), '--commit', commit], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual((state / 'source/NEXTCLOUD.cmake').read_text(), 'uncommitted')
            self.assertIn('DEVELOPMENT_TEAM "BTQQVL2S6N"', (state / 'source/NEXTCLOUD.cmake').read_text())
            self.assertIn('APPLICATION_REV_DOMAIN "com.nextcloud.desktopclient.findertest"', (state / 'source/NEXTCLOUD.cmake').read_text())
            import json
            self.assertEqual(json.loads((state / 'source/admin/osx/hdrive/build-info.json').read_text())['commit'], commit)
            self.assertIn('SVG_CONVERTER', (state / 'source/cmake/modules/GenerateIconsUtils.cmake').read_text().splitlines()[0])
            self.assertNotIn('installIfMissing("inkscape"', (state / 'source/admin/osx/mac-crafter/Sources/Commands/Build.swift').read_text())
            self.assertEqual((repo / 'NEXTCLOUD.cmake').read_text(), 'uncommitted')

    def test_failed_build_invalidates_old_artifact_receipt(self):
        spec = importlib.util.spec_from_file_location('hdrive_build', ROOT / 'admin/osx/hdrive/build.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            receipt = output / 'build-receipt.json'
            receipt.write_text('old success')
            with patch.object(builder, 'prepare', side_effect=RuntimeError('failed preparation')):
                with self.assertRaisesRegex(RuntimeError, 'failed preparation'):
                    builder.build(ROOT, output, 'HEAD')
            self.assertFalse(receipt.exists())

    def test_resolves_head_before_build_starts(self):
        spec = importlib.util.spec_from_file_location('hdrive_build', ROOT / 'admin/osx/hdrive/build.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temporary:
            def prepare(repo, output, revision):
                self.assertEqual(revision, 'a' * 40)
                raise RuntimeError('stop after preparation')
            with patch.object(builder, 'git', return_value='a' * 40), patch.object(builder, 'prepare', side_effect=prepare):
                with self.assertRaisesRegex(RuntimeError, 'stop after preparation'):
                    builder.build(ROOT, Path(temporary), 'HEAD')

    def test_refuses_symlink_source_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            outside = state / 'outside'
            outside.mkdir()
            (outside / 'keep').write_text('safe')
            (state / 'source').symlink_to(outside, target_is_directory=True)
            result = subprocess.run(['python3', str(ROOT / 'admin/osx/hdrive/build.py'), '--prepare-only', '--repo', str(ROOT), '--build-dir', str(state)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('symlink', result.stderr.lower())
            self.assertEqual((outside / 'keep').read_text(), 'safe')

    def test_normal_build_keeps_upstream_branding(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / 'branding.cmake'
            script.write_text(f'''set(CMAKE_SOURCE_DIR "{ROOT}")
include("{ROOT}/NEXTCLOUD.cmake")
if(NOT APPLICATION_NAME STREQUAL "Nextcloud")
 message(FATAL_ERROR "Non-development build changed")
endif()
''')
            result = subprocess.run(['cmake', '-P', str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
