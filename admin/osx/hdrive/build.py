#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build a signed hdrive developer app from an exact Git commit."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
APP_NAME = 'hdriveDev'
BUNDLE_ID = 'com.nextcloud.desktopclient.findertest'


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def replace_once(path, pattern, replacement):
    text, count = re.subn(pattern, replacement, path.read_text(), count=1)
    if count != 1:
        raise RuntimeError(f'Build setup changed: {path}. Update hdrive build preparation.')
    path.write_text(text)


def prepare(repo, build_dir, commit):
    source = build_dir / 'source'
    if source.is_symlink() or build_dir.is_symlink():
        raise RuntimeError('Build/source directory must not be a symlink.')
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / '.hdrive-build').write_text('hdrive\n')
    revision = git(repo, 'rev-parse', '--verify', commit + '^{commit}')
    with tempfile.TemporaryDirectory(dir=build_dir, prefix='archive-') as temporary:
        archive = Path(temporary) / 'source.tar'
        run('git', '-C', repo, 'archive', '--format=tar', f'--output={archive}', revision)
        unpacked = Path(temporary) / 'source'
        unpacked.mkdir()
        run('tar', '-xf', archive, '-C', unpacked)
        source.mkdir(exist_ok=True)
        run('rsync', '-rplc', '--delete', str(unpacked) + '/', str(source) + '/')
    # mac-crafter reads these literals directly rather than evaluating CMake includes.
    branding = source / 'NEXTCLOUD.cmake'
    replace_once(branding, r'(set\(\s*DEVELOPMENT_TEAM\s+")[^"]+', lambda match: match[1] + 'BTQQVL2S6N')
    replace_once(branding, r'(set\(\s*APPLICATION_REV_DOMAIN\s+")[^"]+', lambda match: match[1] + BUNDLE_ID)
    converter = shutil.which('rsvg-convert')
    if not converter:
        raise RuntimeError('rsvg-convert is required (existing librsvg installation).')
    icons = source / 'cmake/modules/GenerateIconsUtils.cmake'
    icons.write_text(f'set(SVG_CONVERTER "{converter}" CACHE FILEPATH "hdrive icon converter" FORCE)\n' + icons.read_text())
    replace_once(source / 'admin/osx/mac-crafter/Sources/Commands/Build.swift',
                 re.escape('try await installIfMissing("inkscape", "brew install inkscape")'),
                 'try await installIfMissing("rsvg-convert", "brew install librsvg")')
    replace_once(source / 'craftmaster.ini', r'(?m)^Paths/Python = /Users/runner/[^\n]+',
                 'Paths/Python = ' + sys.executable)
    info = source / 'admin/osx/hdrive/build-info.json'
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps({'commit': revision}) + '\n')
    return source


def build(repo, build_dir, commit, prepare_only=False):
    (build_dir / 'build-receipt.json').unlink(missing_ok=True)
    commit = git(repo, 'rev-parse', '--verify', commit + '^{commit}')
    source = prepare(repo, build_dir, commit)
    if prepare_only:
        print(source)
        return source
    developer_dir = Path('/Applications/Xcode.app/Contents/Developer')
    if not developer_dir.is_dir():
        raise RuntimeError('Xcode.app is required in /Applications.')
    identity = subprocess.check_output(['security', 'find-identity', '-v', '-p', 'codesigning'], text=True)
    if '"Apple Development:' not in identity:
        raise RuntimeError('Apple Development signing certificate is unavailable.')
    tools = build_dir / 'tools'
    tools.mkdir(exist_ok=True)
    codesign = tools / 'codesign'
    codesign.write_text('''#!/bin/zsh
args=()
for arg in "$@"; do
    if [[ "$arg" == --timestamp ]]; then
        args+=(--timestamp=none)
    else
        args+=("$arg")
    fi
done
exec /usr/bin/codesign "${args[@]}"
''')
    codesign.chmod(0o755)
    env = os.environ.copy()
    env['DEVELOPER_DIR'] = str(developer_dir)
    env['PATH'] = str(tools) + ':/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:' + env.get('PATH', '')
    for key in ('COMPILER_INDEX_STORE_ENABLE', 'INDEX_ENABLE_DATA_STORE', 'INDEX_DATA_STORE_DIR',
                'INDEX_ENABLE_OPTIMIZATION_LEVEL_OVERRIDE', 'INDEX_STORE_COMPRESS',
                'INDEX_STORE_ONLY_PROJECT_FILES', 'CCHROOT'):
        env.pop(key, None)
    run('swift', 'run', '--package-path', source / 'admin/osx/mac-crafter',
        '--scratch-path', build_dir / 'mac-crafter', 'mac-crafter',
        '--build-path', build_dir / 'craft', '--product-path', build_dir / 'product',
        '--build-type', 'Debug', '--dev', '--app-name', 'hdrive',
        '--disable-auto-updater', '--build-file-provider-module',
        '--code-sign-identity', 'Apple Development', source, env=env)
    app = build_dir / 'product' / (APP_NAME + '.app')
    record_artifact(app, build_dir, commit)
    print(f'Built {app}', flush=True)
    return app


def record_artifact(app, build_dir, commit):
    info = json.loads((build_dir / 'source/admin/osx/hdrive/build-info.json').read_text())
    if info['commit'] != commit:
        raise RuntimeError('Prepared sources do not match requested artifact commit.')
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
    details = subprocess.check_output(['/usr/bin/codesign', '-dvvv', str(app)], stderr=subprocess.STDOUT, text=True)
    signature = re.search(r'^CDHash=(\w+)$', details, re.M)
    if not signature:
        raise RuntimeError('Signed artifact has no CodeDirectory hash.')
    receipt = {'commit': commit, 'signature': signature[1]}
    (build_dir / 'build-receipt.json').write_text(json.dumps(receipt) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=ROOT)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/hdrive')
    parser.add_argument('--commit', default='HEAD')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    build(args.repo.resolve(), args.build_dir.absolute(), args.commit, args.prepare_only)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
