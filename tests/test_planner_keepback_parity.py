"""v1.11.2/#107: the planner keepback stamps must model what the
exact checker enforces (#75): footprint-embedded Edge.Cuts in
_stamp_edge_keepback, and same-net drills in _stamp_hole_keepback's
via band (hole_to_hole binds same-net pairs; the track band stays
foreign-only because hole_clearance is a copper rule).
Run: python3 tests/test_planner_keepback_parity.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import numpy as np
import pcbnew
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

def NM(v): return int(v * 1e6)

b = pcbnew.BOARD(); b.SetCopperLayerCount(4)
net = pcbnew.NETINFO_ITEM(b, 'GND'); b.Add(net); code = net.GetNetCode()
ds = b.GetDesignSettings()
ds.m_HoleToHoleMin = NM(0.25); ds.m_HoleClearance = NM(0.25); ds.m_CopperEdgeClearance = NM(0.5)

# footprint carrying an Edge.Cuts segment at y = 10.2, x 10..30
fp = pcbnew.FOOTPRINT(b)
sh = pcbnew.PCB_SHAPE(fp); sh.SetShape(pcbnew.SHAPE_T_SEGMENT)
sh.SetStart(pcbnew.VECTOR2I(NM(10), NM(10.2))); sh.SetEnd(pcbnew.VECTOR2I(NM(30), NM(10.2)))
sh.SetLayer(pcbnew.Edge_Cuts); fp.Add(sh); b.Add(fp)

# footprint with a same-net TH pad (drill 0.8) at (20, 30)
fp2 = pcbnew.FOOTPRINT(b)
pad = pcbnew.PAD(fp2)
pad.SetSize(pcbnew.VECTOR2I(NM(1.5), NM(1.5)))
pad.SetDrillSize(pcbnew.VECTOR2I(NM(0.8), NM(0.8)))
pad.SetPosition(pcbnew.VECTOR2I(NM(20), NM(30)))
pad.SetNetCode(code); fp2.Add(pad); b.Add(fp2)

g = w.Grid(b, (0.0, 40.0, 0.0, 40.0))

def cell(x, y):
    i, j = g.cell(x, y); return j, i  # masks are [layer, j, i] / [j, i]

# --- edge stamp sees the footprint's Edge.Cuts
blocked = np.zeros((g.nl, g.NY, g.NX), dtype=bool); via_block = np.zeros((g.NY, g.NX), dtype=bool)
w._stamp_edge_keepback(b, g, blocked, via_block, 0.25, 0.3)
j, i = cell(20.0, 10.2)
assert blocked[0, j, i], 'footprint Edge.Cuts segment did not stamp the track band'
j, i = cell(20.0, 20.0)
assert not blocked[0, j, i], 'edge band stamped 9.8 mm away from the cut'

# --- hole stamp: same-net drill enters the via band (h2h), not the track band
blocked2 = np.zeros((g.nl, g.NY, g.NX), dtype=bool); via_block2 = np.zeros((g.NY, g.NX), dtype=bool)
w._stamp_hole_keepback(b, g, blocked2, via_block2, 0.25, 0.15, net='GND')
j, i = cell(20.0, 30.0)
assert via_block2[j, i], 'same-net TH pad did not stamp the via band (hole_to_hole)'
assert not blocked2[0, j, i], 'same-net pad stamped the track band -- hole_clearance is foreign-only'

print('planner keepback parity OK')
