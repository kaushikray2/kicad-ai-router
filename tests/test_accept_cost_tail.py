"""v1.11.4/#114: layer cost must reach the leg-acceptance decision.
v1.11.0 plumbed the factor into every search site but _accept_key
kept comparing only (faults, unconnected, splits), so a finisher
leg that displaced priced-layer copper was kept whenever the gate
numbers tied (NAS layer-cost run: engine passes placed In1.Cu
segments, the delivered board had none). The gate now derives
cost-weighted wirelength from the summary's per-layer track length
and the run's resolved spec, and _accept_key carries it as its
last term -- tie-break only. With no layer_cost configured the
term is a constant and acceptance is exactly what it was.
Run: python3 tests/test_accept_cost_tail.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
SUMM = {'nets': [], 'tracks_by_layer': {'In1.Cu': 1, 'F.Cu': 1},
        'tracks_by_layer_net': {}, 'pour_layers': {}, 'pour_nets': [],
        'layer_aliases': dict(ALIASES), 'copper_layers': list(LAYERS),
        'vias': 0, 'wirelength_mm': 0.0}

def _pipe(cfg, summ):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)  # bypasses __init__ on purpose (see #101)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    pipe.cfg = cfg; pipe.log = lambda *a: None
    pipe._copper_layers = list(LAYERS)
    pipe._gseq = 0; pipe.last_gate = None
    open(pipe.base, 'w').write('board')
    pipe._drc_measure = lambda board: (0, 0, {}, 0, [])
    pipe._worker = lambda cmd, *a, **k: dict(summ) if cmd == 'summary' else {}
    return pipe

def _gate(cfg, tll):
    s = dict(SUMM); s['track_length_by_layer'] = tll
    return _pipe(cfg, s).tool_gate()

def test_displacing_leg_rejected_at_equal_gate_numbers():
    cfg = {'layer_cost': 'In1.Cu=10'}
    keep = _gate(cfg, {'In1.Cu': 1.0, 'F.Cu': 5.0})  # weighted 1x10 + 5x1 = 15.0
    disp = _gate(cfg, {'F.Cu': 17.0})                 # priced copper displaced: 17x1 = 17.0
    # the numbers the key led with are identical on both boards...
    assert pl._accept_key(keep)[:3] == pl._accept_key(disp)[:3]
    # ...so the leg predicate (_accept_key(after) > kb -> revert) must
    # now fire on cost alone; before the fix the keys were equal and
    # the displacing board was kept.
    assert pl._accept_key(disp) > pl._accept_key(keep)
    assert keep['cost_weighted_wirelength_mm'] == 15.0, keep
    assert disp['cost_weighted_wirelength_mm'] == 17.0, disp

def test_uncosted_acceptance_unchanged():
    a = _gate({}, {'In1.Cu': 1.0})
    b = _gate({}, {'F.Cu': 99.0})
    assert a['cost_weighted_wirelength_mm'] is None
    assert b['cost_weighted_wirelength_mm'] is None
    assert pl._accept_key(a) == pl._accept_key(b)

if __name__ == '__main__':
    test_displacing_leg_rejected_at_equal_gate_numbers()
    test_uncosted_acceptance_unchanged()
    print('accept cost tail OK')
