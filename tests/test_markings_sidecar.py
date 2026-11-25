"""Issue #150 (B4): engineered-nets must be able to REACH users.
(a) the PCM zip ships every ai_router/*.py (glob, not a hard-coded
list that omitted all 12 engineered modules); (b) the pipeline
import fallback defines every module handle (incl. _hvdomains /
_quietnets) and a board WITH a markings sidecar whose engineered
modules failed to import fails LOUDLY instead of silently running
unmarked; (c) backend_client.submit uploads the sidecar as
markings_b64 and the server stores it next to the job's board
under the name the markings loader derives.
Run: python3 tests/test_markings_sidecar.py"""
import base64, json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ENG_MODULES = ('markings', 'tlcalc', 'rfcheck', 'powertree', 'powerplan', 'wcca',
               'kbclient', 'library_rules', 'placement_audit', 'lengthmatch',
               'hvdomains', 'quietnets')
SIDECAR_BYTES = json.dumps({'version': 1, 'nets': []}).encode()

def test_client_uploads_sidecar():
    d = tempfile.mkdtemp()
    board = os.path.join(d, 'b.kicad_pcb'); open(board, 'wb').write(b'board')
    open(os.path.join(d, 'b.ai-router-markings.json'), 'wb').write(SIDECAR_BYTES)
    sys.path.insert(0, os.path.join(ROOT, 'ai_router'))
    import backend_client as bc
    cap = {}
    orig = bc._req
    def fake(url, path, token='', method='GET', payload=None, timeout=120):
        cap.update(payload); return b'{"job_id": "j1"}'
    bc._req = fake
    try:
        assert bc.submit('http://x', 't', board, {}) == 'j1'
    finally:
        bc._req = orig
    assert 'markings_b64' in cap, 'client did not upload the markings sidecar'
    assert base64.b64decode(cap['markings_b64']) == SIDECAR_BYTES

def test_server_stores_sidecar():
    d = tempfile.mkdtemp()
    side = os.path.join(d, 'side.json'); open(side, 'wb').write(SIDECAR_BYTES)
    env = dict(os.environ, AIR_DATA_DIR=d)
    lines = [
        'import importlib.util, os',
        'sp = importlib.util.spec_from_file_location(%r, %r)' % ('srv', os.path.join(ROOT, 'docker', 'server.py')),
        'm = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)',
        'mb = open(%r, %r).read()' % (side, 'rb'),
        'jid, dup = m.submit_job(b%r, None, %r, {}, markings_bytes=mb)' % ('board', 'b.kicad_pcb'),
        'p = os.path.join(%r, %r, jid, %r)' % (d, 'runs', 'input.ai-router-markings.json'),
        'print(%r, os.path.exists(p) and open(p, %r).read() == mb)' % ('STORED', 'rb'),
    ]
    r = subprocess.run([sys.executable, '-c', chr(10).join(lines)], capture_output=True, text=True, env=env)
    assert 'STORED True' in r.stdout, (r.stdout, r.stderr[-400:])

def test_loud_failure_when_modules_missing():
    d = tempfile.mkdtemp()
    board = os.path.join(d, 'b.kicad_pcb'); open(board, 'wb').write(b'(kicad_pcb)')
    open(os.path.join(d, 'b.ai-router-markings.json'), 'wb').write(SIDECAR_BYTES)
    lines = [
        'import sys; sys.path.insert(0, %r)' % ROOT,
        'sys.modules[%r] = None' % 'ai_router.markings',
        'from ai_router.pipeline import Pipeline',
        'try:',
        '    Pipeline({%r: False, %r: %r}, %r, lambda m: None)' % ('strip_existing', 'tracemaker_path', '/nonexistent', board),
        '    print(%r)' % 'SILENT',
        'except RuntimeError as e:',
        '    print(%r, %r in str(e))' % ('LOUD', 'refusing to run unmarked'),
    ]
    r = subprocess.run([sys.executable, '-c', chr(10).join(lines)], capture_output=True, text=True)
    assert 'LOUD True' in r.stdout, (r.stdout[-300:], r.stderr[-400:])

def test_pcm_zip_ships_all_modules():
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(ROOT, 'build_package.py'), d)
    shutil.copytree(os.path.join(ROOT, 'ai_router'), os.path.join(d, 'ai_router'))
    r = subprocess.run([sys.executable, os.path.join(d, 'build_package.py')], capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout, r.stderr[-400:])
    import zipfile
    zips = [f for f in os.listdir(os.path.join(d, 'pcm')) if f.endswith('.zip')]
    assert zips, 'no zip built'
    names = set(zipfile.ZipFile(os.path.join(d, 'pcm', zips[0])).namelist())
    missing = [m for m in ENG_MODULES if 'plugins/ai_router/%s.py' % m not in names]
    assert not missing, 'PCM zip is missing engineered modules: %s' % missing

if __name__ == '__main__':
    test_client_uploads_sidecar(); print('client ok')
    test_server_stores_sidecar(); print('server ok')
    test_loud_failure_when_modules_missing(); print('loud ok')
    test_pcm_zip_ships_all_modules(); print('zip ok')
    print('PASS test_markings_sidecar')
