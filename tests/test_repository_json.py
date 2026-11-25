"""Issue #120: build_package.py emitted a repository.json with no
'packages' entry, so KiCad's PCM could not follow the repository
to its package index -- the update channel was broken (and
packages.json had gone stale at 1.10.2). Issue #154: the emitted
repository.json must ALSO match the official KiCad repository's
field shape -- KiCad's valijson loader rejected ours ('does not
look like a valid KiCad package repository') over three extra
top-level fields (schema_version / url / kicad_version) and a
missing packages.update_time_utc, even though the file validated
against the JSON schemas. Builds in a scratch copy so the repo
tree is not dirtied.
Run: python3 tests/test_repository_json.py"""
import hashlib, json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
d = tempfile.mkdtemp()
shutil.copy(os.path.join(ROOT, 'build_package.py'), d)
shutil.copytree(os.path.join(ROOT, 'ai_router'), os.path.join(d, 'ai_router'))
r = subprocess.run([sys.executable, os.path.join(d, 'build_package.py')], capture_output=True, text=True)
assert r.returncode == 0, (r.stdout, r.stderr[-400:])
repo = json.load(open(os.path.join(d, 'pcm-repo', 'repository.json')))
allowed = {'$schema', 'name', 'maintainer', 'packages', 'resources'}
extra = set(repo) - allowed
assert not extra, 'repository.json carries non-official top-level fields %r (#154)' % (extra,)
for gone in ('schema_version', 'url', 'kicad_version'):
    assert gone not in repo, '%s must not be emitted (#154)' % gone
pkgs = repo.get('packages')
assert isinstance(pkgs, dict), "repository.json has no 'packages' resource -- PCM cannot follow it (#120)"
assert pkgs.get('url', '').endswith('/packages.json'), pkgs
blob = open(os.path.join(d, 'pcm-repo', 'packages.json'), 'rb').read()
assert pkgs.get('sha256') == hashlib.sha256(blob).hexdigest(), 'packages sha256 does not match the emitted packages.json'
assert isinstance(pkgs.get('update_timestamp'), int) and pkgs['update_timestamp'] > 0, pkgs
import datetime
want_utc = datetime.datetime.fromtimestamp(pkgs['update_timestamp'], datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
assert pkgs.get('update_time_utc') == want_utc, 'update_time_utc %r inconsistent with update_timestamp (%r expected, #154)' % (pkgs.get('update_time_utc'), want_utc)
pj = json.loads(blob)
assert pj['packages'][0]['versions'][0]['version'], pj
print('PASS test_repository_json (packages.json advertises %s)' % pj['packages'][0]['versions'][0]['version'])
