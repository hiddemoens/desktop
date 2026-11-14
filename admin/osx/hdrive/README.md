<!-- SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
# Personal hdrive macOS client

`hdrive` is a personal distribution branch of `hiddemoens/desktop`. Do not open upstream pull requests from this branch. Keep contributor work on separate branches, including `fix/macos-remove-download`.

macOS development builds use approved hdrive logo and `hdrive` display name and install as `/Applications/hdrive.app` (build product is also `hdrive.app`). Internal account name `NextcloudDev`, config file `nextclouddev.cfg`, signing team and `com.nextcloud.desktopclient.findertest` app/extension identifiers are retained. First installation aliases sandbox Preferences/hdrive to existing Preferences/NextcloudDev rather than copying credentials or resetting File Provider domains. Finder-facing bundle and registered domain names are `hdrive`; existing domains are updated in place with unchanged identifiers. Initial setup prefills hdrive.nl; other servers remain available. Stock automatic updater is disabled for these builds.

## Build and update

Run in main checkout on clean `hdrive` branch:

```sh
/opt/homebrew/opt/python@3.12/bin/python3.12 admin/osx/hdrive/update.py
```

Updater fetches and merges `upstream/master` and `origin/fix/macos-remove-download`, runs tooling tests, builds exact commit with mac-crafter, verifies signatures/identifiers, and pushes only hdrive branch. It then stages replacement in Applications, archives previous app, stops existing development app, replaces it, verifies it again and restarts it. Conflicts, uncommitted work, wrong active branch, divergent hdrive history, signing failures and build failures stop operation. No force pushes or automatic conflict resolution.

Missing fix branch stops updates until its retirement is explicitly handled. Upstream `master` is development code; tests/build success cannot guarantee every upstream runtime behavior.

State and compressed previous-app backup live in `~/Library/Application Support/hdrive-updater/`. Lock prevents concurrent updater runs. Build cache lives in `build/hdrive/`; completed installation removes only generated hdrive.app, hdriveDev.app and NextcloudDev.app bundles under this tool-owned directory. Dependencies, compiler cache and logs remain. No second rollback .app is kept beside installed app.

Installed commit is recorded only after restart and cleanup. A failed build/install can be retried. If main checkout changes during build, installation stops. User branch switching can defer scheduled updates; updater never switches it automatically.

```sh
# Fetch/merge and report pending commit, without build/install:
python3 admin/osx/hdrive/update.py --check
# Build committed HEAD without network merges or installation:
python3 admin/osx/hdrive/build.py
# Update/install current clean hdrive HEAD without fetch:
python3 admin/osx/hdrive/update.py --no-fetch
# Tooling checks:
python3 -m unittest discover -s admin/osx/hdrive/tests -v
```

Requires Xcode, existing Apple Development certificate, librsvg/rsvg-convert, existing MacPorts Git at `/opt/local/bin/git` and Python 3.12. Updater pins this Git installation so Xcode and launchd use the same approved Keychain credential helper as interactive builds. mac-crafter resolves its established Craft dependencies. Build preparation reuses local NextcloudDev launcher approach: Git archive, isolated build sources, rsvg-convert and no signing timestamp for local development. No installed signed app is patched in place.

## Rollback

Updater restores previous app and restarts it if installation or immediate launch fails. For manual recovery, quit development client and extract `previous-app.zip` into a temporary directory using `ditto -x -k`, verify extracted app with `codesign --verify --deep --strict`, and move it back into Applications. Remove failed installation first only after confirming backup is valid. Preserve the app/extension identities and existing sandbox data. Do not reset accounts or File Provider domains to resolve branding problems.

## Validation limits

Tests cover source preparation, CMake branding, real temporary Git merges/conflicts, storage alias and installation/rollback/cleanup using isolated app fixtures. Actual build, signature, startup/account-log and installed-icon checks are recorded with deployment handoff. Process startup is checked automatically; Finder upload/download behavior remains a user check. Native launchd schedule runs in logged-in macOS user session; Codex is not required. Calendar runs missed during sleep run after waking.


## Native daily schedule

Install or refresh user LaunchAgent (daily 10:00 local time):

```sh
python3 admin/osx/hdrive/schedule.py
```

Agent is `nl.hdrive.desktop-updater`, stored in `~/Library/LaunchAgents/nl.hdrive.desktop-updater.plist`. It invokes Python updater directly, without an AI task. Logs live in `~/Library/Logs/hdrive-updater/`. Inspect `launchctl print gui/$(id -u)/nl.hdrive.desktop-updater`; unload with `launchctl bootout gui/$(id -u)/nl.hdrive.desktop-updater`. Calendar uses laptop time zone. Schedule does not repeatedly retry failures or switch active branch.
