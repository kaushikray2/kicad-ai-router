"""Issue #110 (residual of #80): DOMAINS Grid enforcement had two
holes.  (a) domains.json carries BOARD layer names (cmd_hvgeom
facts use b.GetLayerName -- 'GND'/'3V3' on boards with renamed
inner layers), but Grid.build resolved rect layers with the
canonical-only _layer_id_from_name and silently skipped whatever
did not resolve, so the track block never stamped on renamed
layers.  (b) Grid.build_owners never stamped DOMAINS at all, so
_plan_attempts' cutset fallback planned through HV/quiet rects
and proposed victim sets the finisher then rejected (no_plan).
Run: python3 tests/test_domain_layers.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, io, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pcbnew
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

def renamed_board():
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(4)
    b.SetLayerName(pcbnew.In1_Cu, 'GND')
    b.SetLayerName(pcbnew.In2_Cu, '3V3')
    assert b.GetLayerName(pcbnew.In1_Cu) == 'GND'
    assert b.GetLayerName(pcbnew.In2_Cu) == '3V3'
    return b

b = renamed_board()
g = w.Grid(b, (0, 40, 0, 40))
li_gnd = g.lidx[pcbnew.In1_Cu]
ci = int(20 / w.PITCH)   # rect centre cell (window origin 0,0)

# resolution contract: board name, positional canonical name
# (any case) resolve; a bogus name resolves to nothing AND warns.
assert g._domain_lidx(['GND']) == [li_gnd]
assert g._domain_lidx(['in1.cu']) == [li_gnd]
_err = io.StringIO(); _old = sys.stderr; sys.stderr = _err
try: assert g._domain_lidx(['Nowhere.Cu']) == []
finally: sys.stderr = _old
assert 'Nowhere.Cu' in _err.getvalue(), 'unresolvable domains layer dropped silently'

# (a) a rect named by its BOARD layer name stamps in build();
# the entry's own net stays exempt.
w.DOMAINS = [{'kind': 'hv', 'name': 'HV', 'nets': ['HV1'],
              'rects': [{'rect': [16, 16, 24, 24], 'layers': ['GND']}]}]
blocked, _vb = g.build(set(), target='LV1')
assert blocked[li_gnd, ci, ci], 'rect on renamed layer GND never stamped in build()'
blocked_x, _vb2 = g.build(set(), target='HV1')
assert not blocked_x[li_gnd, ci, ci], 'exempt (own) net was stamped'
# positional canonical name still stamps (pre-fix behaviour kept)
w.DOMAINS = [{'kind': 'hv', 'name': 'HV', 'nets': ['HV1'],
              'rects': [{'rect': [16, 16, 24, 24], 'layers': ['In1.Cu']}]}]
blocked_c, _vb3 = g.build(set(), target='LV1')
assert blocked_c[li_gnd, ci, ci], 'canonical In1.Cu rect no longer stamps'

# (b) build_owners stamps too, and a plan on the owners masks can
# no longer cross a full-height domain wall on every layer.
w.DOMAINS = [{'kind': 'hv', 'name': 'HV', 'nets': ['HV1'],
              'rects': [{'rect': [16, 16, 24, 24], 'layers': ['GND']}]}]
hb, _hvb, _o, _vo, _n = g.build_owners(set(), target='LV1')
assert hb[li_gnd, ci, ci], 'build_owners never stamped the domain rect'
w.DOMAINS = [{'kind': 'hv', 'name': 'HV', 'nets': ['HV1'],
              'rects': [{'rect': [19, 0, 21, 40],
                         'layers': ['F.Cu', 'GND', '3V3', 'B.Cu']}]}]
hb2, hvb2, _o2, _vo2, _n2 = g.build_owners(set(), target='LV1')
path, _cost = g.astar(hb2, hvb2, (10, 20), (30, 20), (0,), (0,))
assert path is None, 'cutset-side plan crossed a domain wall: %s' % (path,)
w.DOMAINS = []
print('domain layers OK (board-name resolution, owners stamp, wall plan)')
