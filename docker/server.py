#!/usr/bin/env python3
"""AI Router backend service: job API + config web page + TraceMaker manager.
Stdlib only. Data dir (env AIR_DATA_DIR, default /data):
  config/config.json  tracemaker/<ver>/...  runs/<jobid>/  selftest/baselines.json
"""
import base64, hashlib, io, json, os, re, secrets, shutil, socket, subprocess
import tarfile, threading, time, urllib.request, uuid, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BACKEND_VERSION = '1.10.1'
SCHEMA_VERSION = 1
DATA = os.environ.get('AIR_DATA_DIR', '/data')
PORT = int(os.environ.get('AIR_PORT', '8731'))
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_BODY = 200 * 1024 * 1024  # stated cap: whole-board uploads, LAN v1
_lock = threading.Lock()

def _atomic_write(path, data_bytes):
    tmp = path + '.tmp-%d' % os.getpid()
    with open(tmp, 'wb') as f: f.write(data_bytes)
    os.replace(tmp, path)

def cfg_path(): return os.path.join(DATA, 'config', 'config.json')

def default_config():
    return {'schema_version': SCHEMA_VERSION,
            'auth_token': secrets.token_hex(24),
            'allowed_hosts': [],
            'llm': {'endpoint': 'http://127.0.0.1:8080/v1', 'model': 'local-model', 'api_key': ''},
            'routing': {'mode': 'auto', 'max_passes': 4, 'work': 20000000,
                        'no_tracks_on': 'In1.Cu', 'strip_existing': True},
            'max_concurrent_jobs': 1,
            'selftest_tolerance': {'unconnected': 2, 'split_nets': 0},
            'tracemaker': {'url': os.environ.get('TRACEMAKER_URL', ''), 'selected_version': '', 'sha256': {}},
            'retention': {'keep_runs': 200}}

def load_config():
    p = cfg_path()
    if not os.path.exists(p):
        cfg = default_config()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        save_config(cfg)
        return cfg
    with open(p) as f: cfg = json.load(f)
    if cfg.get('schema_version') != SCHEMA_VERSION:
        shutil.copy(p, p + '.bak-%s' % time.strftime('%Y%m%d-%H%M%S'))
        base = default_config()
        for k, v in base.items():
            if k not in cfg: cfg[k] = v
        cfg['schema_version'] = SCHEMA_VERSION
        save_config(cfg)
    return cfg

def save_config(cfg):
    _atomic_write(cfg_path(), json.dumps(cfg, indent=1).encode())

def local_ips():
    ips = {'127.0.0.1', '::1', 'localhost'}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ips.add(info[4][0])
    except Exception: pass
    return ips

# ------------------------------------------------------------- tracemaker
def tm_root(): return os.path.join(DATA, 'tracemaker')

def tm_versions():
    out = []
    root = tm_root()
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            exe = os.path.join(root, name, 'bin', 'tracemaker')
            if os.path.isfile(exe):
                out.append(name)
    return out

def tm_exe(version):
    return os.path.join(tm_root(), version, 'bin', 'tracemaker')

def tm_smoke(exe):
    p = subprocess.run([exe, 'version'], capture_output=True, text=True, timeout=60)
    m = re.search(r'(\d+\.\d+\.\d+)', p.stdout + p.stderr)
    if p.returncode != 0 or not m: raise RuntimeError('tracemaker version failed: %s' % (p.stdout + p.stderr)[:200])
    return m.group(1)

def tm_install_from_archive(archive_path, sha, expected=None):
    staging = os.path.join(tm_root(), '.staging-%s' % uuid.uuid4().hex[:8])
    os.makedirs(staging)
    try:
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as z: z.extractall(staging)
        else:
            with tarfile.open(archive_path) as t: t.extractall(staging, filter='data')  # v1.8.2 (#19): no ../ or link escapes
        exe = None
        for dirpath, _dirs, files in os.walk(staging):
            if 'tracemaker' in files and os.path.basename(dirpath) == 'bin':
                exe = os.path.join(dirpath, 'tracemaker'); break
        if not exe: raise RuntimeError('archive contains no bin/tracemaker')
        os.chmod(exe, 0o755)
        version = tm_smoke(exe)
        # v1.10.1 (#44): the binary must agree with the layout version
        # that the pre-install TOFU check used -- before, the version
        # came from attacker-controlled file names for the check but
        # from the binary for the install, and the install (which
        # executes the binary and replaces the trusted tree) happened
        # BEFORE the hash comparison for the reported version.
        if expected and version != expected:
            raise RuntimeError('archive layout version %s != binary version %s -- refused before install' % (expected, version))
        root_dir = os.path.dirname(os.path.dirname(exe))
        dest = os.path.join(tm_root(), version)
        # v1.10.1 (#44): never delete the trusted install -- move it
        # aside for rollback; the new tree takes its place only after
        # every check above has passed.
        if os.path.exists(dest):
            bak = dest + '.bak-' + time.strftime('%Y%m%d-%H%M%S')
            shutil.move(dest, bak)
        shutil.move(root_dir, dest)
        return version
    finally:
        shutil.rmtree(staging, ignore_errors=True)

def tm_archive_version(archive_path):
    # v1.8.2 (#19): the version from the archive LAYOUT (the release
    # tarballs unpack to tracemaker-<x.y.z>-<platform>/bin/tracemaker),
    # readable WITHOUT executing anything in the archive.
    try:
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as z: names = z.namelist()
        else:
            with tarfile.open(archive_path) as t: names = t.getnames()
    except Exception:
        return None
    for n in names:
        m = re.search(r'(\d+\.\d+\.\d+)', n)
        if m and 'tracemaker' in n: return m.group(1)
    return None

def tm_update(url):
    if not url: raise RuntimeError('no TraceMaker URL configured')
    tmp = os.path.join(tm_root(), '.download-%s' % uuid.uuid4().hex[:8])
    if url.startswith('file://'):
        shutil.copy(urlparse(url).path, tmp)
    elif url.startswith('/'):
        shutil.copy(url, tmp)
    else:
        req = urllib.request.Request(url, headers={'User-Agent': 'ai-router-backend/1.0'})
        with urllib.request.urlopen(req, timeout=600) as r, open(tmp, 'wb') as f:
            shutil.copyfileobj(r, f)
    try:
        sha = hashlib.sha256(open(tmp, 'rb').read()).hexdigest()
        # v1.8.2 (#19): the TOFU comparison happens BEFORE the archive
        # is extracted, executed or installed. Before, tm_install ran
        # the downloaded binary (version smoke) and replaced the
        # trusted install first, and the mismatch error came after --
        # with no rollback. First use of a NEW version remains TOFU
        # by definition; a KNOWN version with a different hash is
        # refused here, untouched.
        ver_hint = tm_archive_version(tmp)
        if not ver_hint: raise RuntimeError('archive layout names no TraceMaker version -- refused (TOFU: the version must be readable from the layout, without executing the archive)')  # v1.10.1 (#44)
        known0 = (load_config().get('tracemaker') or {}).get('sha256') or {}
        if ver_hint and ver_hint in known0 and known0[ver_hint] != sha:
            raise RuntimeError('TOFU mismatch for %s: recorded %s, downloaded %s -- refused before install' % (ver_hint, known0[ver_hint][:12], sha[:12]))
        version = tm_install_from_archive(tmp, sha, ver_hint)
        with _lock:
            cfg = load_config()
            known = cfg.setdefault('tracemaker', {}).setdefault('sha256', {})
            if version in known and known[version] != sha:
                raise RuntimeError('TOFU mismatch for %s: recorded %s, downloaded %s' % (version, known[version][:12], sha[:12]))
            known[version] = sha
            if not cfg['tracemaker'].get('selected_version'): cfg['tracemaker']['selected_version'] = version
            cfg['tracemaker']['url'] = url
            save_config(cfg)
        return {'version': version, 'sha256': sha}
    finally:
        if os.path.exists(tmp): os.remove(tmp)

# ------------------------------------------------------------- jobs
import queue
_jobs = {}          # id -> dict (status record)
_jobs_lock = threading.Lock()  # v1.9 (M12): _jobs is shared by the HTTP threads, the worker threads and the retention pruner; unsynchronized iteration during a concurrent insert/pop raised RuntimeError under the 16-job grid load

def _jobs_snapshot():
    with _jobs_lock: return list(_jobs.values())
_job_queue = queue.Queue()
_procs = {}         # id -> Popen

def runs_root(): return os.path.join(DATA, 'runs')

def job_dir(jid): return os.path.join(runs_root(), jid)

def _write_status(jid):
    rec = _jobs.get(jid)
    if rec:
        _atomic_write(os.path.join(job_dir(jid), 'status.json'), json.dumps(rec, indent=1).encode())

def _load_existing_jobs():
    root = runs_root(); os.makedirs(root, exist_ok=True)
    for name in os.listdir(root):
        sp = os.path.join(root, name, 'status.json')
        if os.path.isfile(sp):
            try:
                rec = json.load(open(sp))
                if rec.get('status') in ('queued', 'running'):
                    rec['status'] = 'interrupted'
                _jobs[name] = rec
            except Exception: pass

def _prune_retention():
    keep = load_config().get('retention', {}).get('keep_runs', 200)  # v1.9 (L11): fallback was 20 -- the value that silently pruned grid runs in Nov 2026; match the config-template default
    with _jobs_lock:
        done = sorted((r for r in _jobs.values() if r.get('status') in ('done', 'error', 'cancelled', 'interrupted')),
                      key=lambda r: r.get('created', ''))
    for rec in done[:max(0, len(done) - keep)]:
        shutil.rmtree(job_dir(rec['id']), ignore_errors=True)
        with _jobs_lock: _jobs.pop(rec['id'], None)

def _worker_loop():
    while True:
        jid = _job_queue.get()
        rec = _jobs.get(jid)
        if not rec or rec.get('status') == 'cancelled':
            _job_queue.task_done(); continue
        rec['status'] = 'running'; rec['started'] = time.strftime('%Y-%m-%d %H:%M:%S'); _write_status(jid)
        jd = job_dir(jid)
        logf = open(os.path.join(jd, 'job.log'), 'ab')
        proc = subprocess.Popen(['python3', os.path.join(HERE, 'run_job.py'), '--data', DATA, '--job-dir', jd],
                                stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        _procs[jid] = proc
        proc.wait(); logf.close()
        _procs.pop(jid, None)
        # run_job.py writes the terminal status into status.json; adopt it
        try:
            final = json.load(open(os.path.join(jd, 'status.json')))
            if final.get('status') in ('done', 'error'): rec.update(final)
            elif rec.get('status') != 'cancelled': rec['status'] = 'error'; rec['error'] = 'runner exited %d without a result' % proc.returncode
        except Exception:
            if rec.get('status') != 'cancelled': rec['status'] = 'error'; rec['error'] = 'runner produced no status'
        rec['finished'] = time.strftime('%Y-%m-%d %H:%M:%S'); _write_status(jid)
        _prune_retention()
        _job_queue.task_done()

def submit_job(board_bytes, pro_bytes, board_name, overrides, kind='route', dru_bytes=None):
    digest = hashlib.sha256(board_bytes + json.dumps(overrides or {}, sort_keys=True).encode()).hexdigest()
    with _jobs_lock:
        for rec in _jobs.values():
            if rec.get('digest') == digest and rec.get('status') in ('queued', 'running') and rec.get('kind') == kind:
                return rec['id'], True
    jid = uuid.uuid4().hex
    jd = job_dir(jid); os.makedirs(jd)
    with open(os.path.join(jd, 'input.kicad_pcb'), 'wb') as f: f.write(board_bytes)
    if pro_bytes:
        with open(os.path.join(jd, 'input.kicad_pro'), 'wb') as f: f.write(pro_bytes)
    if dru_bytes:
        with open(os.path.join(jd, 'input.kicad_dru'), 'wb') as f: f.write(dru_bytes)
    rec = {'id': jid, 'kind': kind, 'status': 'queued', 'board_name': board_name,
           'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'digest': digest,
           'overrides': overrides or {}}
    with _jobs_lock: _jobs[jid] = rec
    _write_status(jid)
    _job_queue.put(jid)
    return jid, False

# ------------------------------------------------------------- self-test
def selftest_dir():
    d = os.path.join(HERE, 'selftest')
    return d if os.path.isdir(d) else os.path.join(DATA, 'selftest')

def selftest_baselines():
    p = os.path.join(DATA, 'selftest', 'baselines.json')
    return json.load(open(p)) if os.path.exists(p) else {}

def selftest_status():
    boards = sorted(f for f in os.listdir(selftest_dir()) if f.endswith('.kicad_pcb')) if os.path.isdir(selftest_dir()) else []
    base = selftest_baselines()
    last = {}
    for rec in _jobs_snapshot():
        if rec.get('kind') == 'selftest' and rec.get('status') == 'done':
            prev = last.get(rec['board_name'])
            if not prev or rec['created'] > prev['created']: last[rec['board_name']] = rec
    out = []
    for b in boards:
        row = {'board': b, 'baseline': base.get(b), 'last': None, 'verdict': 'NO BASELINE' if b not in base else 'NOT RUN'}
        if b in last:
            g = (last[b].get('gate') or {})
            cur = {'unconnected_total': g.get('unconnected_total'), 'split_nets': len(g.get('split_nets') or []), 'banned_layer_tracks': g.get('banned_layer_tracks'), 'copper_violations': g.get('copper_violations')}  # v1.9 (M11): copper joins the recorded baseline
            row['last'] = cur
            try:  # v1.10.1 (#43): flag self-test jobs whose recorded cfg_effective lacks the shipped defaults (pre-v1.10.1 backends ran self-tests without the engine portfolio)
                _sj = json.load(open(os.path.join(DATA, 'runs', last[b]['id'], 'status.json')))
                _ce = _sj.get('cfg_effective') or {}
                if _ce and 'engine_portfolio' not in _ce:
                    row['defaults_warning'] = 'job ran without the plugin defaults (no engine_portfolio in cfg_effective)'
            except Exception:
                pass
            if b in base:
                # TraceMaker is nondeterministic (seeded variant racing),
                # so exact equality is the wrong test for variance-prone
                # boards: the test board reproduced 5/65, 5/65, 6/91 on an
                # identical stack. PASS within the tolerance band.
                tol = load_config().get('selftest_tolerance', {})
                tu = int(tol.get('unconnected', 2)); ts = int(tol.get('split_nets', 0))
                # v1.9 (M11): the verdict used to ignore copper entirely --
                # a board could regress to hundreds of copper faults and
                # still PASS. Copper is variance-prone under variant
                # racing (an identical stack reproduced 65 then 91 total
                # violations), so the band is coarse: baseline + max(10,
                # 25%), or an explicit selftest_tolerance.copper. A
                # baseline recorded before v1.9 has no copper field; the
                # check is then skipped and flagged, and the next
                # /api/selftest/record stores it.
                bc = base[b].get('copper_violations')
                if bc is None or cur['copper_violations'] is None:
                    copper_ok = True; row['copper_checked'] = False
                else:
                    tc = tol.get('copper')
                    allow = int(tc) if tc is not None else max(10, int(0.25 * bc))
                    copper_ok = cur['copper_violations'] <= bc + allow; row['copper_checked'] = True
                row['verdict'] = 'PASS' if (cur['unconnected_total'] is not None and base[b]['unconnected_total'] is not None
                                            and cur['unconnected_total'] <= base[b]['unconnected_total'] + tu
                                            and cur['split_nets'] <= base[b]['split_nets'] + ts
                                            and copper_ok
                                            and not (cur['banned_layer_tracks'] or 0)) else 'DRIFT'
        out.append(row)
    return out

# ------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a): pass

    def _cfg(self): return load_config()

    def _host_ok(self):
        host = (self.headers.get('Host') or '').split(':')[0].strip('[]')
        return host in local_ips() or host in (self._cfg().get('allowed_hosts') or [])

    def _authed(self):
        # v1.9 (S5): constant-time compares, exact cookie-pair match,
        # fail closed when no token is configured (the old substring
        # test degenerated to accepting any 'air_token=' cookie when
        # the configured token was empty).
        tok = self._cfg().get('auth_token', '')
        if not tok: return False
        auth = self.headers.get('Authorization') or ''
        if auth.startswith('Bearer ') and secrets.compare_digest(auth[7:], tok): return True
        for part in (self.headers.get('Cookie') or '').split(';'):
            part = part.strip()
            if part.startswith('air_token=') and secrets.compare_digest(part[10:], tok): return True
        return False

    def _send(self, code, body, ctype='application/json'):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        if isinstance(body, str): body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY: raise RuntimeError('body over the %d MB cap' % (MAX_BODY // 1048576))
        return self.rfile.read(n) if n else b''

    def _json_body(self):
        return json.loads(self._body() or b'{}')

    def _guard(self, need_auth=True):
        if not self._host_ok():
            self._send(403, {'error': 'host not allowed'}); return False
        if need_auth and not self._authed():
            self._send(401, {'error': 'unauthorized'}); return False
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == '/health':
                if not self._guard(need_auth=False): return
                kc = subprocess.run(['kicad-cli', '--version'], capture_output=True, text=True, timeout=30).stdout.strip()
                cfg = self._cfg()
                self._send(200, {'ok': True, 'backend_version': BACKEND_VERSION, 'kicad_cli': kc,
                                 'schema_version': SCHEMA_VERSION,
                                 'tracemaker_versions': tm_versions(),
                                 'tracemaker_selected': cfg.get('tracemaker', {}).get('selected_version', ''),
                                 'queue_depth': _job_queue.qsize(),
                                 'jobs_running': sum(1 for r in _jobs_snapshot() if r.get('status') == 'running')})
                return
            if path == '/':
                if not self._host_ok(): self._send(403, {'error': 'host not allowed'}); return
                page = open(os.path.join(HERE, 'config_page.html'), 'rb').read()
                self._send(200, page, 'text/html; charset=utf-8'); return
            if not self._guard(): return
            if path == '/api/config':
                cfg = json.loads(json.dumps(self._cfg()))
                key = cfg.get('llm', {}).get('api_key', '')
                cfg['llm']['api_key'] = ''
                cfg['llm']['api_key_set'] = bool(key)
                cfg.pop('auth_token', None)
                self._send(200, cfg); return
            if path == '/api/tracemaker':
                cfg = self._cfg(); tm = cfg.get('tracemaker', {})
                self._send(200, {'installed': [{'version': v, 'sha256': (tm.get('sha256') or {}).get(v, ''),
                                                'selected': v == tm.get('selected_version')} for v in tm_versions()],
                                 'url': tm.get('url', '')}); return
            if path == '/api/runs':
                rows = sorted(_jobs_snapshot(), key=lambda r: r.get('created', ''), reverse=True)
                self._send(200, [{k: r.get(k) for k in ('id', 'kind', 'status', 'board_name', 'created', 'started', 'finished', 'gate', 'error')} for r in rows]); return
            if path.startswith('/api/runs/'):
                parts = path.split('/')
                jid = parts[3] if len(parts) > 3 else ''
                rec = _jobs.get(jid)
                if not rec: self._send(404, {'error': 'no such job'}); return
                if len(parts) > 4 and parts[4] == 'result':
                    rp = os.path.join(job_dir(jid), 'result.kicad_pcb')
                    if not os.path.exists(rp): self._send(404, {'error': 'no result'}); return
                    self._send(200, open(rp, 'rb').read(), 'application/octet-stream'); return
                if len(parts) > 4 and parts[4] in ('result.pro', 'result.dru'):
                    rp = os.path.join(job_dir(jid), 'result.kicad_pro' if parts[4] == 'result.pro' else 'result.kicad_dru')
                    if not os.path.exists(rp): self._send(404, {'error': 'no result sidecar'}); return
                    self._send(200, open(rp, 'rb').read(), 'application/octet-stream'); return
                out = dict(rec)
                led = os.path.join(job_dir(jid))
                _root_led = os.path.join(led, 'ledger.json')
                if os.path.isfile(_root_led):
                    try: out['ledger'] = json.load(open(_root_led))
                    except Exception: pass
                else:
                    ledgers = [os.path.join(dp, f) for dp, _dn, fn in os.walk(led) for f in fn if f == 'ledger.json']
                    if ledgers:
                        try: out['ledger'] = json.load(open(sorted(ledgers)[-1]))
                        except Exception: pass
                logp = os.path.join(led, 'job.log')
                if os.path.exists(logp): out['log_tail'] = open(logp, errors='replace').read()[-2000:]
                self._send(200, out); return
            if path == '/api/selftest':
                self._send(200, selftest_status()); return
            self._send(404, {'error': 'not found'})
        except Exception as e:
            self._send(500, {'error': str(e)[:400]})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == '/login':
                if not self._host_ok(): self._send(403, {'error': 'host not allowed'}); return
                tok = (self._json_body() or {}).get('token', '')
                if secrets.compare_digest(tok, self._cfg().get('auth_token', '')):
                    body = json.dumps({'ok': True}).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Set-Cookie', 'air_token=%s; Path=/; HttpOnly; SameSite=Strict' % tok)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers(); self.wfile.write(body)
                else: self._send(401, {'error': 'bad token'})
                return
            if not self._guard(): return
            if path == '/api/config':
                upd = self._json_body(); cfg = self._cfg()
                llm = upd.get('llm') or {}
                if 'endpoint' in llm: cfg['llm']['endpoint'] = llm['endpoint']
                if 'model' in llm: cfg['llm']['model'] = llm['model']
                if llm.get('api_key'): cfg['llm']['api_key'] = llm['api_key']
                if llm.get('clear_api_key'): cfg['llm']['api_key'] = ''
                rt = upd.get('routing') or {}
                for k in ('mode', 'max_passes', 'work', 'no_tracks_on', 'strip_existing', 'engine_portfolio', 'layer_law_policy', 'reroute_window_cap', 'reroute_accept', 'reroute_margin', 'pass_advice', 'advice_regions', 'first_nets_policy', 'finish_signal_cap', 'finish_pour_cap', 'long_finish_cap', 'ilp_repair_cap', 'attempt_select'):  # v1.9.6 (#40): Phase 1 keys were silently dropped
                    if k in rt: cfg['routing'][k] = rt[k]
                if 'allowed_hosts' in upd: cfg['allowed_hosts'] = upd['allowed_hosts']
                if 'retention' in upd: cfg['retention'] = upd['retention']
                save_config(cfg); self._send(200, {'ok': True}); return
            if path == '/api/tracemaker/select':
                ver = (self._json_body() or {}).get('version', '')
                if ver not in tm_versions(): self._send(400, {'error': 'version not installed'}); return
                cfg = self._cfg(); cfg['tracemaker']['selected_version'] = ver; save_config(cfg)
                self._send(200, {'ok': True, 'selected': ver}); return
            if path == '/api/tracemaker/update':
                url = (self._json_body() or {}).get('url') or self._cfg().get('tracemaker', {}).get('url', '')
                res = tm_update(url); self._send(200, res); return
            if path == '/api/runs':
                req = self._json_body()
                board = base64.b64decode(req.get('board_b64') or '')
                if not board: self._send(400, {'error': 'board_b64 required'}); return
                pro = base64.b64decode(req['pro_b64']) if req.get('pro_b64') else None
                dru = base64.b64decode(req['dru_b64']) if req.get('dru_b64') else None
                jid, dup = submit_job(board, pro, req.get('board_name', 'board.kicad_pcb'), req.get('overrides') or {}, dru_bytes=dru)
                self._send(200, {'job_id': jid, 'duplicate': dup}); return
            if path.startswith('/api/runs/') and path.endswith('/cancel'):
                jid = path.split('/')[3]; rec = _jobs.get(jid)
                if not rec: self._send(404, {'error': 'no such job'}); return
                rec['status'] = 'cancelled'; _write_status(jid)
                proc = _procs.get(jid)
                if proc:
                    try: os.killpg(os.getpgid(proc.pid), 15)
                    except Exception: pass
                self._send(200, {'ok': True}); return
            if path == '/api/selftest/run':
                ids = []
                for f in sorted(os.listdir(selftest_dir())):
                    if f.endswith('.kicad_pcb'):
                        b = open(os.path.join(selftest_dir(), f), 'rb').read()
                        pro = None
                        pp = os.path.join(selftest_dir(), f.replace('.kicad_pcb', '.kicad_pro'))
                        if os.path.exists(pp): pro = open(pp, 'rb').read()
                        jid, _d = submit_job(b, pro, f, {'max_passes': 2}, kind='selftest')
                        ids.append(jid)
                self._send(200, {'job_ids': ids}); return
            if path == '/api/selftest/record':
                base = {}
                for row in selftest_status():
                    if row.get('last'): base[row['board']] = row['last']
                _atomic_write(os.path.join(DATA, 'selftest', 'baselines.json'), json.dumps(base, indent=1).encode())
                self._send(200, {'ok': True, 'baselines': base}); return
            self._send(404, {'error': 'not found'})
        except Exception as e:
            self._send(500, {'error': str(e)[:400]})

def main():
    os.makedirs(os.path.join(DATA, 'config'), exist_ok=True)
    os.makedirs(tm_root(), exist_ok=True)
    os.makedirs(runs_root(), exist_ok=True)
    os.makedirs(os.path.join(DATA, 'selftest'), exist_ok=True)
    cfg = load_config()
    _load_existing_jobs()
    if not tm_versions():
        url = cfg.get('tracemaker', {}).get('url') or os.environ.get('TRACEMAKER_URL', '')
        if url:
            try:
                r = tm_update(url); print('TraceMaker bootstrap: installed %s' % r['version'], flush=True)
            except Exception as e:
                print('TraceMaker bootstrap failed: %s' % e, flush=True)
    nworkers = max(1, min(64, int(cfg.get('max_concurrent_jobs', 1))))  # ceiling 64 (was 8, 2026-11-22): config is the knob
    for _ in range(nworkers):
        threading.Thread(target=_worker_loop, daemon=True).start()
    print('job workers: %d' % nworkers, flush=True)
    srv = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print('ai-router backend %s on :%d (data %s)' % (BACKEND_VERSION, PORT, DATA), flush=True)
    srv.serve_forever()

if __name__ == '__main__':
    main()
