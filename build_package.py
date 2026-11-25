#!/usr/bin/env python3
"""Build the KiCad PCM package + self-hosted repository index.
Zip metadata.json carries NO download_* keys (PCM rule); those live only in
pcm-repo/packages.json, regenerated on every build - forgetting that step is
the silent 'updates never appear' failure, so it happens here, always."""
import hashlib, json, os, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = '1.11.2'
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

def main():
    out_zip_dir = os.path.join(HERE, 'pcm')
    repo_dir = os.path.join(HERE, 'pcm-repo')
    os.makedirs(out_zip_dir, exist_ok=True); os.makedirs(repo_dir, exist_ok=True)
    zip_name = 'ai-router-%s.zip' % VERSION
    zip_path = os.path.join(out_zip_dir, zip_name)
    pkg_src = os.path.join(HERE, 'ai_router')
    files = ['__init__.py', 'ai_router_plugin.py', 'dialog.py', 'config.py', 'llm_client.py',
             'orchestrator.py', 'pipeline.py', 'worker.py', 'backend_client.py', 'icon.png']
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('metadata.json', json.dumps(METADATA, indent=2))
        for f in files:
            z.write(os.path.join(pkg_src, f), 'plugins/ai_router/' + f)
        z.write(os.path.join(pkg_src, 'icon64.png'), 'resources/icon.png')
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
    import shutil, time
    shutil.copy(zip_path, os.path.join(repo_dir, zip_name))
    repository = {'$schema': 'https://go.kicad.org/pcm/schemas/v2',
                  'name': 'SiliconBrane AI Router (LAN)',
                  'maintainer': {'name': 'SiliconBrane Inc', 'contact': {'email': 'ray@siliconbrane.com'}},
                  'url': REPO_BASE_URL, 'kicad_version': '10.0'}
    with open(os.path.join(repo_dir, 'repository.json'), 'w') as f: json.dump(repository, f, indent=2)
    print('zip: %s (%d bytes, sha256 %s)' % (zip_path, len(blob), sha[:16]))
    print('repo index regenerated: pcm-repo/{packages.json, repository.json, %s}' % zip_name)

if __name__ == '__main__':
    main()
