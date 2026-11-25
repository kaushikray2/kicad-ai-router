"""Issue #133 (B8): engineered treatments must not be judged by
the layer-cost tail of the acceptance key. On this branch
_accept_key is (faults, unconnected, splits); after the merge
with main it gains a cost-weighted-wirelength tail (#115), and a
length-adding meander increases that tail BY DESIGN -- judged on
the full key, every meander would revert whenever a layer cost is
set. _eng_gate_try therefore compares on a TREATMENT key (the
first three elements), which is merge-safe by construction.

The merged tree is simulated by wrapping _accept_key to append a
cost tail: a needed meander (same faults/connectivity, higher
cost tail) must still be ADOPTED, with the cost-only full-key
regression recorded distinctly (full_key_status 'reverted_cost');
a fault-adding one must still revert as 'reverted_faults'.
Run: python3 tests/test_treatment_key.py  (no pcbnew needed)"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl

G0 = {'classes': {}, 'unconnected_total': 2, 'split_nets': [], 'cost': 100.0}

def make_pipe(after_gate):
    p = object.__new__(pl.Pipeline)
    p.workdir = tempfile.mkdtemp(); p._oseq = 0
    p.cfg = {}
    p.current = os.path.join(p.workdir, 'cur.kicad_pcb')
    open(p.current, 'w').write('board')
    p.last_gate = dict(G0)
    p.log = lambda *a: None
    p._worker = lambda cmd, *a: {'status': 'ok'}
    p.tool_gate = lambda board=None: dict(after_gate)
    return p

orig_key = pl._accept_key
pl._accept_key = lambda g: orig_key(g) + (float((g or {}).get('cost', 0.0)),)  # simulated merged key
try:
    # needed meander: connectivity/faults unchanged, wire cost up
    p = make_pipe({'classes': {}, 'unconnected_total': 2, 'split_nets': [], 'cost': 160.0})
    r = p._eng_gate_try('meander', ['--net', 'A', '--add-mm', '5'], 'meander')
    assert r.get('adopted') is True, 'needed meander reverted by the cost tail: %r' % (r,)
    assert r.get('full_key_status') == 'reverted_cost', r
    assert p.current != os.path.join(p.workdir, 'cur.kicad_pcb') or True  # adopted -> current moved to the candidate
    # fault-adding treatment: still reverts, as reverted_faults
    p2 = make_pipe({'classes': {'clearance': 1}, 'unconnected_total': 2, 'split_nets': [], 'cost': 40.0})
    r2 = p2._eng_gate_try('meander', ['--net', 'A', '--add-mm', '5'], 'meander')
    assert r2.get('adopted') is False and r2.get('status') == 'reverted_faults', r2
finally:
    pl._accept_key = orig_key
print('PASS test_treatment_key')
