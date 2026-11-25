"""Issue #120: build_package.py emitted a repository.json with no
'packages' entry and no schema_version, so KiCad's PCM could not
follow the repository to its package index -- the update channel
was broken (and packages.json had gone stale at 1.10.2). The
emitted repository.json must carry schema_version 2 and a packages
resource whose sha256 matches the packages.json written alongside
it. Builds in a scratch copy so the repo tree is not dirtied.
Run: python3 tests/test_repository_json.py"""
import hashlib, json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
d = tempfile.mkdtemp()
shutil.copy(os.path.join(ROOT, 'build_package.py'), d)
shutil.copytree(os.path.join(ROOT, 'ai_router'), os.path.join(d, 'ai_router'))
r = subprocess.run([sys.executable, os.path.join(d, 'build_package.py')], capture_output=True, text=True)
assert r.returncode == 0, (r.stdout, r.stderr[-400:])
repo = json.load(open(os.path.join(d, 'pcm-repo', 'repository.json')))
assert repo.get('schema_version') == 2, 'schema_version missing/not 2: %r' % (repo.get('schema_version'),)
pkgs = repo.get('packages')
assert isinstance(pkgs, dict), "repository.json has no 'packages' resource -- PCM cannot follow it (#120)"
assert pkgs.get('url', '').endswith('/packages.json'), pkgs
blob = open(os.path.join(d, 'pcm-repo', 'packages.json'), 'rb').read()
assert pkgs.get('sha256') == hashlib.sha256(blob).hexdigest(), 'packages sha256 does not match the emitted packages.json'
assert isinstance(pkgs.get('update_timestamp'), int) and pkgs['update_timestamp'] > 0, pkgs
pj = json.loads(blob)
assert pj['packages'][0]['versions'][0]['version'], pj
print('PASS test_repository_json (packages.json advertises %s)' % pj['packages'][0]['versions'][0]['version'])
