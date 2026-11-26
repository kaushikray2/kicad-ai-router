#!/usr/bin/env python3
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

"""Build the KiCad PCM package + self-hosted repository index.
Zip metadata.json carries NO download_* keys (PCM rule); those live only in
pcm-repo/packages.json, regenerated on every build - forgetting that step is
the silent 'updates never appear' failure, so it happens here, always."""
import datetime, hashlib, json, os, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = '1.12.3'
IDENTIFIER = 'ca.siliconbrane.ai-router'
REPO_BASE_URL = 'https://raw.githubusercontent.com/kaushikray2/kicad-ai-router/main/pcm-repo'
DESCRIPTION = ('LLM-orchestrated PCB routing for KiCad: deterministic TraceMaker passes, '
               'DRC gating, finishing and stitching. Local or Docker backend.')
assert len(DESCRIPTION) <= 150, len(DESCRIPTION)
DESCRIPTION_FULL = (
    'AI Router finishes PCB layouts in KiCad 10 with deterministic tools '
    'under an exact-geometry gate. An LLM (or the built-in AUTO strategy, '
    'which needs no LLM) makes strategy decisions only - it never draws '
    'geometry. All copper is produced by deterministic code: TraceMaker '
    'routing passes, a grid-A* finisher with displacement and re-bridging, '
    'a plane-fragment healer and a pour stitcher. Every result is accepted '
    'or reverted by kicad-cli DRC plus exact clearance checks, with a '
    'per-layer track census and split-net metrics on every gate. Requires '
    'the TraceMaker routing engine (open source, GPL-3.0) and, for routing '
    'jobs, the routing backend whose Docker image is provided in the '
    'repository. The plugin always works on a copy of your board; the open '
    'board is never modified.')
assert len(DESCRIPTION_FULL) <= 1000, len(DESCRIPTION_FULL)

METADATA = {
    '$schema': 'https://go.kicad.org/pcm/schemas/v2',
    'name': 'AI Router',
    'description': DESCRIPTION,
    'description_full': DESCRIPTION_FULL,
    'identifier': IDENTIFIER,
    'type': 'plugin',
    'author': {'name': 'SiliconBrane Inc', 'contact': {'email': 'ray@siliconbrane.com'}},
    'maintainer': {'name': 'SiliconBrane Inc', 'contact': {'email': 'ray@siliconbrane.com'}},
    'license': 'GPL-3.0-or-later',
    'resources': {'homepage': 'https://github.com/kaushikray2/kicad-ai-router',
                  'website': 'https://www.siliconbrane.com'},
    'tags': ['autorouting', 'pcb-routing', 'ai', 'llm'],
    'versions': [{'version': VERSION, 'status': 'stable', 'kicad_version': '10.0', 'runtime': 'swig'}],
}

# KiCad discovers a PCM package through the __init__.py at the root
# of the package's plugins/ folder: pcbnew.LoadPlugins loads a package
# directory only when it contains an __init__.py, and records
# 'Skip subdir' otherwise. Through v1.11.5 the zip shipped only
# plugins/ai_router/*, so KiCad silently skipped the installed
# package -- no toolbar button, no External Plugins entry (#156).
# This shim is written into the zip as plugins/__init__.py. After
# installation the bundled ai_router package sits beside it, so the
# shim puts its own directory on sys.path and imports it;
# ai_router/__init__.py performs the ActionPlugin registration.
PCM_ENTRYPOINT = '''\
"""AI Router -- KiCad PCM entry point.

KiCad loads this file when it discovers the package; importing the
bundled ai_router package (installed beside this file) registers
the ActionPlugin. See build_package.py (#156).
"""
import os, sys
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)
try:
    import ai_router  # noqa: F401  (importing registers the plugin)
except Exception as _e:
    print('ai_router: PCM entry point failed to load the bundled plugin (%r)' % (_e,))
'''


def main():
    repo_dir = os.path.join(HERE, 'pcm-repo')
    os.makedirs(repo_dir, exist_ok=True)
    zip_name = 'ai-router-%s.zip' % VERSION
    zip_path = os.path.join(repo_dir, zip_name)
    pkg_src = os.path.join(HERE, 'ai_router')
    files = ['__init__.py', 'ai_router_plugin.py', 'dialog.py', 'config.py', 'llm_client.py',
             'orchestrator.py', 'pipeline.py', 'worker.py', 'backend_client.py', 'icon.png',
             'naming.py', 'ui_model.py', 'board_facts.py', 'engine_run.py']  # v1.12 (#167/#168) + v1.12.2 (#170): every ai_router/*.py ships in the zip (CI asserts it)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('metadata.json', json.dumps(METADATA, indent=2))
        z.writestr('plugins/__init__.py', PCM_ENTRYPOINT)
        for f in files:
            z.write(os.path.join(pkg_src, f), 'plugins/ai_router/' + f)
        z.write(os.path.join(pkg_src, 'icon64.png'), 'resources/icon.png')
    # CI's package job reads the zip from the historical pcm/
    # staging path (the workflow file is unchanged -- the token
    # available here cannot push workflow changes). Since #157
    # pcm/ is gitignored build output only; the canonical zip
    # lives in pcm-repo/ and packages.json points there.
    out_zip_dir = os.path.join(HERE, 'pcm')
    os.makedirs(out_zip_dir, exist_ok=True)
    import shutil
    shutil.copy(zip_path, os.path.join(out_zip_dir, zip_name))
    blob = open(zip_path, 'rb').read()
    sha = hashlib.sha256(blob).hexdigest()
    with zipfile.ZipFile(zip_path) as z:
        install_size = sum(e.file_size for e in z.infolist())
    pkg_entry = dict(METADATA)
    pkg_entry['versions'] = [dict(METADATA['versions'][0],
                                  download_sha256=sha, download_size=len(blob),
                                  download_url='%s/%s' % (REPO_BASE_URL, zip_name),
                                  install_size=install_size)]
    packages = {'$schema': 'https://go.kicad.org/pcm/schemas/v2', 'packages': [pkg_entry]}
    with open(os.path.join(repo_dir, 'packages.json'), 'w') as f: json.dump(packages, f, indent=2)
    import time
    # v1.11.5 (#120): PCM v2 requires a packages RESOURCE (url +
    # sha256 of packages.json + update_timestamp).  Without it
    # KiCad cannot follow the repository to its package index at
    # all -- the update channel was silently broken.
    # #154: the field SHAPE must match the official KiCad
    # repository.json -- KiCad's valijson loader rejects ours
    # ('does not look like a valid KiCad package repository')
    # even though it validates against the JSON schemas.  No
    # schema_version / url / kicad_version top-level fields, and
    # the packages object carries update_time_utc alongside
    # update_timestamp.
    packages_blob = open(os.path.join(repo_dir, 'packages.json'), 'rb').read()
    ts = int(time.time())
    repository = {'$schema': 'https://go.kicad.org/pcm/schemas/v2',
                  'name': 'SiliconBrane AI Router (LAN)',
                  'maintainer': {'name': 'SiliconBrane Inc', 'contact': {'email': 'ray@siliconbrane.com'}},
                  'packages': {'url': REPO_BASE_URL + '/packages.json',
                               'sha256': hashlib.sha256(packages_blob).hexdigest(),
                               'update_timestamp': ts,
                               'update_time_utc': datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}}
    with open(os.path.join(repo_dir, 'repository.json'), 'w') as f: json.dump(repository, f, indent=2)
    print('zip: %s (%d bytes, sha256 %s)' % (zip_path, len(blob), sha[:16]))
    print('repo index regenerated: pcm-repo/{packages.json, repository.json, %s}' % zip_name)

if __name__ == '__main__':
    main()
