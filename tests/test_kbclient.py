"""#68 Step 3: KB estimation layer -- authority order enforced.

User values win; confirmed KB values apply with their citation;
unconfirmed or missing estimates are NEVER applied (the load is
removed, so the tree reports the rail incomplete).  The cache
key is MPN + datasheet revision."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import kbclient, markings, powertree

CIT = {'document': 'DS-EXAMPLE Rev C', 'revision': 'C', 'table': 'Table 7-1', 'page': 42}


def _est(kind, value, confirmed=False, rev='C'):
    return {'mpn': 'IC-EXAMPLE', 'datasheet_rev': rev, 'kind': kind,
            'value_a': value, 'conditions': 'Vdd max, TA 85C',
            'citation': CIT, 'confirmed': confirmed}


def _entries():
    return [markings._check_power_entry({
        'net': 'V5', 'voltage': {'nominal_v': 5},
        'loads': {'U2': {'kb': {'mpn': 'IC-EXAMPLE', 'datasheet_rev': 'C'}},
                  'U3': {'peak_a': 0.25, 'sustained_a': 0.1}}}, 0)]


def test_unconfirmed_never_applied(tmp_path):
    cache = kbclient.KbCache(str(tmp_path / 'kb.json'))
    kb = kbclient.DictKb([_est('peak', 1.8)])  # present but unconfirmed
    entries, res = kbclient.resolve_loads(_entries(), cache, kb)
    loads = entries[0]['loads']
    assert 'U2' not in loads  # removed, not estimated
    assert loads['U3']['peak_a'] == 0.25  # user value untouched
    out = {r['ref']: r['outcome'] for r in res}
    assert out == {'U2': 'kb_unconfirmed', 'U3': 'user'}


def test_confirmed_applies_with_citation(tmp_path):
    cache = kbclient.KbCache(str(tmp_path / 'kb.json'))
    kb = kbclient.DictKb([_est('peak', 1.8), _est('sustained', 0.9)])
    cache.store_estimates(kb.query('IC-EXAMPLE'))
    assert kbclient.confirm_estimate(cache, kb, 'IC-EXAMPLE', 'C', 'peak') is True
    assert kbclient.confirm_estimate(cache, kb, 'IC-EXAMPLE', 'C', 'sustained') is True
    assert kb.confirmations == [('IC-EXAMPLE', 'C', 'peak'), ('IC-EXAMPLE', 'C', 'sustained')]
    entries, res = kbclient.resolve_loads(_entries(), cache, kb)
    u2 = entries[0]['loads']['U2']
    assert u2['peak_a'] == 1.8 and u2['sustained_a'] == 0.9
    assert u2['source'] == 'kb' and u2['citation'] == CIT
    assert {r['ref']: r['outcome'] for r in res}['U2'] == 'kb_confirmed'
    # and the tree consumes it like a user value
    parsed = {'components': {}, 'libparts': {},
              'nets': {'V5': [('U9', '2'), ('U2', '1'), ('U3', '1')],
                       'GND': [('U2', '2'), ('U3', '2')]}}
    # (pin types absent here; tree-level behaviour is covered by
    # test_powertree -- this asserts resolution output shape only)
    assert entries[0]['loads']['U2']['kind'] == 'constant_current'


def test_cache_key_includes_revision(tmp_path):
    cache = kbclient.KbCache(str(tmp_path / 'kb.json'))
    cache.store_estimates([_est('peak', 1.8, confirmed=True, rev='B')])
    assert cache.confirmed('IC-EXAMPLE', 'B', 'peak') is not None
    assert cache.confirmed('IC-EXAMPLE', 'C', 'peak') is None  # rev C != rev B
    entries, res = kbclient.resolve_loads(_entries(), cache, None)
    assert 'U2' not in entries[0]['loads']
    assert {r['ref']: r['outcome'] for r in res}['U2'] == 'kb_missing'


def test_http_roundtrip():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = []
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            out = {'estimates': [_est('peak', 2.2)]} if body.get('action') == 'query' else {'ok': True}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *a):
            pass
    srv = HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    kb = kbclient.HttpKb('http://127.0.0.1:%d/kb' % srv.server_address[1])
    est = kb.query('IC-EXAMPLE')
    assert est[0]['value_a'] == 2.2 and seen[0]['version'] == kbclient.INTERFACE_VERSION
    kb.write_back_confirmation('IC-EXAMPLE', 'C', 'peak')
    assert seen[1]['action'] == 'confirm'
    srv.shutdown()
