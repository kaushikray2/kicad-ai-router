"""Issue #79 (c)/(d): cmd_auditgeom must not read a rect outline as
its diagonal, and cmd_audit_zones must expand KiCad's layer
wildcards ("*.Cu", "F&B.Cu") instead of silently creating zones
with an empty layer set.
Run: python3 tests/test_audit_geom.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pcbnew
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

def NM(v): return int(round(v * 1e6))

d = tempfile.mkdtemp(); path = os.path.join(d, 'g.kicad_pcb')
b = pcbnew.BOARD()
net = pcbnew.NETINFO_ITEM(b, 'GND'); b.Add(net)
rect = pcbnew.PCB_SHAPE(b); rect.SetShape(pcbnew.SHAPE_T_RECT)
rect.SetStart(pcbnew.VECTOR2I(NM(10), NM(10))); rect.SetEnd(pcbnew.VECTOR2I(NM(30), NM(20)))
rect.SetLayer(pcbnew.Edge_Cuts); rect.SetWidth(NM(0.1)); b.Add(rect)
pcbnew.SaveBoard(path, b)

facts = w.cmd_auditgeom(path)
def _norm(s):   # undirected, 0.1 mm tolerance (bbox carries the line width)
    return frozenset(((round(s[0]), round(s[1])), (round(s[2]), round(s[3]))))
segs = {_norm(s) for s in facts['outline_segments']}
want = {_norm(s) for s in ([10, 10, 30, 10], [30, 10, 30, 20], [30, 20, 10, 20], [10, 20, 10, 10])}
assert want <= segs, 'rect outline segments wrong: %s' % (segs,)
assert _norm([10, 10, 30, 20]) not in segs, 'rect diagonal still reported as an outline segment'

res = w.cmd_audit_zones(path, [{'ref': 'U1', 'layers': ['*.Cu'],
                                'keepout': {'tracks': True, 'vias': True, 'copperpour': False},
                                'outline': [[5, 5], [15, 5], [15, 15], [5, 15]]},
                               {'ref': 'U2', 'layers': ['F&B.Cu'],
                                'keepout': {'tracks': True, 'vias': False, 'copperpour': False},
                                'outline': [[20, 20], [25, 20], [25, 25], [20, 25]]}])
assert len(res['added']) == 2, res
b2 = pcbnew.LoadBoard(path)
zones = [z for z in b2.Zones() if z.GetIsRuleArea()]
assert len(zones) == 2, zones
names = sorted(tuple(sorted(b2.GetLayerName(l) for l in z.GetLayerSet().CuStack())) for z in zones)
assert names[0] == ('B.Cu', 'F.Cu') and names[1] == ('B.Cu', 'F.Cu'), names
print('audit geom OK (rect edges, wildcard layers)')
