"""v1.11.2/#102: tool_stitch must forward --layer-cost like every
other routing leg. The v1.11.0 shipment excluded stitch as
'vias only', but cmd_stitch's _bridge_island fallback draws real
PCB_TRACK bridges on the worker Grid -- unpriced, its astar travelled
priced layers at 1x.
Run: python3 tests/test_stitch_layer_cost.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
GATE = {'violations_total': 0, 'unconnected_total': 1, 'classes': {},
        'split_nets': [], 'banned_layer_tracks': 0}

def _bare_pipe(cfg):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    pipe.cfg = cfg; pipe.log = lambda *a: None
    pipe._copper_layers = list(LAYERS)
    pipe.last_gate = dict(GATE)
    seen = []
    def recording_worker(cmd, *a, **k):
        seen.append((cmd, list(a)))
        if cmd == 'summary':
            return {'copper_layers': list(LAYERS), 'layer_aliases': dict(ALIASES)}
        return {}
    pipe._worker = recording_worker
    return pipe, seen

def test_stitch_carries_layer_cost():
    pipe, seen = _bare_pipe({'no_tracks_on': 'In1.Cu', 'layer_cost': 'In2.Cu=10'})
    pipe.tool_stitch()
    stitch = [a for c, a in seen if c == 'stitch']
    assert len(stitch) == 1, seen
    args = stitch[0]
    assert args[args.index('--layer-cost') + 1] == 'In2.Cu=10', args

def test_stitch_unset_unchanged():
    pipe, seen = _bare_pipe({'no_tracks_on': 'In1.Cu'})
    pipe.tool_stitch()
    args = [a for c, a in seen if c == 'stitch'][0]
    assert '--layer-cost' not in args, args

if __name__ == '__main__':
    test_stitch_carries_layer_cost(); test_stitch_unset_unchanged()
    print('stitch layer cost OK')
