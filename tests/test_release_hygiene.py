# Copyright (C) 2026 SiliconBrane Inc.
#
# This file is part of KiCad AI Router.
#
# KiCad AI Router is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# KiCad AI Router is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with KiCad AI Router. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #126 (code half): the Dockerfile must pin the KRT
grid_router binary's sha256 (the Freerouting download was already
pinned; KRT's was not), run the service as a non-root USER, and
carry a HEALTHCHECK; server.py must not shell out to kicad-cli
--version per /health request (L12) -- the version is probed once
and cached. The git-tag half of #126 is repo state, verified by
git tag -l, not here. Run: python3 tests/test_release_hygiene.py"""
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
df = open(os.path.join(ROOT, 'docker', 'Dockerfile')).read()
KRT_SHA = '0d3e028c392081a0266f3f7e5f967ba89c9aafc92559e355e541d5c3b2ea52f2'  # v0.24.2 grid_router-linux-x86_64.so, GitHub API digest
assert KRT_SHA in df and 'grid_router.so" | sha256sum -c' in df, 'KRT binary not hash-pinned in the Dockerfile'
assert '\nUSER airuser' in df, 'container still runs as root (no USER)'
assert 'HEALTHCHECK' in df and '/health' in df, 'no HEALTHCHECK against /health'
sv = open(os.path.join(ROOT, 'docker', 'server.py')).read()
health = sv.split("if path == '/health':")[1][:700]
assert '_kicad_cli_version()' in health, '/health does not use the cached kicad-cli version'
assert 'def _kicad_cli_version():' in sv and '_KC_VERSION' in sv, 'no startup-cached kicad-cli version helper'
print('PASS test_release_hygiene')
