#!/bin/zsh
# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: GPL-2.0-or-later

set -eu
repo_dir="${SOURCE_ROOT}/../../.."
exec /opt/homebrew/opt/python@3.12/bin/python3.12 \
    "$repo_dir/admin/osx/hdrive/update.py" --repo "$repo_dir" --no-fetch
