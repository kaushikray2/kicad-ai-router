"""v1.11.4/#115: pass/variant selection must carry the same layer-
cost tail as leg acceptance, and a layer-cost run's ledger must
record what the policy did. Before: the orchestrator's _final_key
was (unconnected, splits, faults) with the pass loop duplicating
that shape inline, and gate records carried an unweighted
wirelength total plus per-layer segment counts only -- both NAS
layer-cost runs had to be censused by hand from the output boards.
Now _final_key appends the cost tail and the pass loop calls it,
the worker summary reports per-layer track length, and every gate
record carries track_length_by_layer + cost_weighted_wirelength_mm
(null when no layer_cost is in force).
Run: python3 tests/test_selection_cost_ledger.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl
import orchestrator as orc
import pcbnew

ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
SUMM = {'nets': [], 'tracks_by_layer': {'In1.Cu': 1, 'F.Cu': 1},
        'tracks_by_layer_net': {}, 'pour_layers': {}, 'pour_nets': [],
        'layer_aliases': dict(ALIASES), 'copper_layers': list(LAYERS),
        'vias': 0, 'wirelength_mm': 0.0,
        'track_length_by_layer': {'In1.Cu': 1.0, 'F.Cu': 5.0}}

def _g(wgt):
    return {'unconnected_total': 3, 'split_nets': ['A'], 'classes': {},
            'banned_layer_tracks': 0, 'cost_weighted_wirelength_mm': wgt}

def test_final_key_prefers_cheaper_at_equal_gate_numbers():
    expensive, cheap = _g(50.0), _g(20.0)
    assert orc._final_key(expensive)[:3] == orc._final_key(cheap)[:3]
    # pass-loop best tracking: a later, cheaper pass must replace best;
    # before the fix the keys were equal and the first board stood.
    best = (orc._final_key(expensive), 'expensive')
    assert orc._final_key(cheap) < best[0]
    # uncosted gates carry no weighted figure: the tail is a constant.
    plain = {'unconnected_total': 3, 'split_nets': ['A'], 'classes': {},
             'banned_layer_tracks': 0}
    assert orc._final_key(plain)[:3] == orc._final_key(cheap)[:3]
    assert pl.cost_tail(plain) == 0.0

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

def test_gate_record_carries_cost_fields():
    pipe = _pipe({'layer_cost': 'In1.Cu=10'}, SUMM)
    pipe.tool_gate()
    recs = [e['data'] for e in pipe.ledger if e['step'] == 'gate']
    assert len(recs) == 1, pipe.ledger
    assert recs[0]['track_length_by_layer'] == {'In1.Cu': 1.0, 'F.Cu': 5.0}, recs[0]
    assert recs[0]['cost_weighted_wirelength_mm'] == 15.0, recs[0]

def _nm(v): return int(round(v * 1e6))

def test_worker_summary_reports_track_length_by_layer():
    d = tempfile.mkdtemp()
    b = pcbnew.BOARD(); b.SetCopperLayerCount(4)
    def track(x1, y1, x2, y2, layer):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(_nm(x1), _nm(y1)))
        t.SetEnd(pcbnew.VECTOR2I(_nm(x2), _nm(y2)))
        t.SetLayer(layer); t.SetWidth(_nm(0.25)); b.Add(t)
    track(0, 0, 10, 0, pcbnew.F_Cu)
    track(0, 5, 2, 5, pcbnew.In1_Cu)
    track(0, 10, 0, 13, pcbnew.B_Cu)
    path = os.path.join(d, 't.kicad_pcb'); pcbnew.SaveBoard(path, b)
    s = w.cmd_summary(path, fast=True)
    assert s['track_length_by_layer'] == {'F.Cu': 10.0, 'In1.Cu': 2.0, 'B.Cu': 3.0}, s.get('track_length_by_layer')

if __name__ == '__main__':
    test_final_key_prefers_cheaper_at_equal_gate_numbers()
    test_gate_record_carries_cost_fields()
    test_worker_summary_reports_track_length_by_layer()
    print('selection cost + ledger OK')
