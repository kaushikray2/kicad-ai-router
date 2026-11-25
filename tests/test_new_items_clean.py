"""Issue #75: _new_items_clean must test hole_to_hole against ALL
drills (it is a physical drill-separation rule -- same-net pairs
count) and must see Edge.Cuts drawn inside footprints, not only
board drawings. Both were pre-filter blind spots: items passed here
and faulted at the DRC gate, reverting whole legs.
Run: python3 tests/test_new_items_clean.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
try:
    import pcbnew
except ImportError:
    import pytest
    pcbnew = pytest.importorskip('pcbnew')  # v1.11.5 (#122): report a skip, not a collection error, outside KiCad
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

def NM(v): return int(v * 1e6)

b = pcbnew.BOARD()
net = pcbnew.NETINFO_ITEM(b, 'N1'); b.Add(net); code = net.GetNetCode()
ds = b.GetDesignSettings()
ds.m_HoleToHoleMin = NM(0.25); ds.m_HoleClearance = NM(0.25); ds.m_CopperEdgeClearance = NM(0.5)

# existing same-net via, drill 0.3 (r 0.15) at (50, 50)
v = pcbnew.PCB_VIA(b)
v.SetPosition(pcbnew.VECTOR2I(NM(50), NM(50))); v.SetDrill(NM(0.3)); v.SetWidth(NM(0.6))
v.SetNetCode(code); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); b.Add(v)

# footprint at the origin carrying an Edge.Cuts segment at y = 10.2
fp = pcbnew.FOOTPRINT(b)
sh = pcbnew.PCB_SHAPE(fp); sh.SetShape(pcbnew.SHAPE_T_SEGMENT)
sh.SetStart(pcbnew.VECTOR2I(NM(10), NM(10.2))); sh.SetEnd(pcbnew.VECTOR2I(NM(30), NM(10.2)))
sh.SetLayer(pcbnew.Edge_Cuts); fp.Add(sh); b.Add(fp)

# (a) new via 0.45 mm from the same-net via: 0.45 < 0.15+0.15+0.25 (h2h)
assert w._new_items_clean(b, 'N1', [], [(50.45, 50.0)], []) is False, 'same-net hole_to_hole pair passed the checker'
# control: 5 mm away is clean
assert w._new_items_clean(b, 'N1', [], [(55.0, 50.0)], []) is True, 'distant via rejected'
# (b) track 0.2 mm from the footprint Edge.Cuts line: 0.2 < 0.125+0.5
assert w._new_items_clean(b, 'N1', [(0, 10.0, 10.0, 30.0, 10.0)], [], [], w=0.25) is False, 'footprint Edge.Cuts graze passed the checker'
# control: 1.8 mm away is clean
assert w._new_items_clean(b, 'N1', [(0, 10.0, 12.0, 30.0, 12.0)], [], [], w=0.25) is True, 'distant track rejected'
# (c) curved Edge.Cuts (#118): a 3.2 mm circle at (50, 20); a short
# segment at 45 degrees, 0.10 mm from the circle, edge rule now
# 0.3 mm. The bounding-box model measured ~0.39 mm to the nearest
# box side and PASSED it; the tessellated outline must reject it.
ds.m_CopperEdgeClearance = NM(0.3)
circ = pcbnew.PCB_SHAPE(b); circ.SetShape(pcbnew.SHAPE_T_CIRCLE)
circ.SetCenter(pcbnew.VECTOR2I(NM(50), NM(20))); circ.SetRadius(NM(1.6))
circ.SetLayer(pcbnew.Edge_Cuts); b.Add(circ)
assert w._new_items_clean(b, 'N1', [(0, 51.195, 21.209, 51.209, 21.195)], [], [], w=0.1) is False, 'segment 0.1 mm from a curved cutout passed the checker (bbox model)'
# control: the same segment 1.5 mm from the circle is clean
assert w._new_items_clean(b, 'N1', [(0, 52.4, 22.9, 52.5, 22.8)], [], [], w=0.1) is True, 'distant segment near circle rejected'
ds.m_CopperEdgeClearance = NM(0.5)
print('new_items_clean hole_to_hole + footprint Edge.Cuts OK')
