"""v1.11.2/#107: the planner keepback stamps must model what the
exact checker enforces (#75): footprint-embedded Edge.Cuts in
_stamp_edge_keepback, and same-net drills in _stamp_hole_keepback's
via band (hole_to_hole binds same-net pairs; the track band stays
foreign-only because hole_clearance is a copper rule).
#113: existing VIA drills join both bands -- the #75 checker's
adrills/fdrills scan GetTracks; the stamp originally did not.
#117: the via hole-clearance band is stamped at the via PAD radius
(the checker measures copper-to-hole; the drill radius
under-reserved by half the annular ring) -- probed at a cell
between the two radii.
Run: python3 tests/test_planner_keepback_parity.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import numpy as np
try:
    import pcbnew
except ImportError:
    import pytest
    pcbnew = pytest.importorskip('pcbnew')  # v1.11.5 (#122): report a skip, not a collection error, outside KiCad
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

# --- hole stamp: existing VIA drills count too (#113)
sig = pcbnew.NETINFO_ITEM(b, 'SIG'); b.Add(sig); sig_code = sig.GetNetCode()
def _via(x, y, cd):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(NM(x), NM(y)))
    v.SetDrill(NM(0.3)); v.SetWidth(NM(0.6))
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNetCode(cd); b.Add(v)
_via(30, 30, sig_code)   # foreign via, drill 0.3
_via(10, 30, code)       # same-net (GND) via, drill 0.3
blocked3 = np.zeros((g.nl, g.NY, g.NX), dtype=bool); via_block3 = np.zeros((g.NY, g.NX), dtype=bool)
w._stamp_hole_keepback(b, g, blocked3, via_block3, 0.25, 0.15, net='GND')
j, i = cell(30.0, 30.0)
assert blocked3[0, j, i], 'foreign via drill did not stamp the track band (hole_clearance)'
assert via_block3[j, i], 'foreign via drill did not stamp the via band'
j, i = cell(10.0, 30.0)
assert via_block3[j, i], 'same-net via drill did not stamp the via band (hole_to_hole)'
assert not blocked3[0, j, i], 'same-net via drill stamped the track band -- hole_clearance is foreign-only'

# --- hole stamp: the via band uses the via PAD radius (#117)
# Foreign TH pad, drill 0.8 (dr 0.4) at (20, 20); hole_clr 0.25.
# Old band: 0.4 + 0.25 + vr_drill 0.15 = 0.80. Checker band:
# 0.4 + 0.25 + vr_pad 0.30 = 0.95. The cell at (20.9, 20) sits at
# 0.90 -- between the two radii: the old planner allowed a via
# centre there and the checker then rejected the finished leg.
hv = pcbnew.NETINFO_ITEM(b, 'HV'); b.Add(hv); hv_code = hv.GetNetCode()
fp3 = pcbnew.FOOTPRINT(b)
pad3 = pcbnew.PAD(fp3)
pad3.SetSize(pcbnew.VECTOR2I(NM(1.5), NM(1.5)))
pad3.SetDrillSize(pcbnew.VECTOR2I(NM(0.8), NM(0.8)))
pad3.SetPosition(pcbnew.VECTOR2I(NM(20), NM(20)))
pad3.SetNetCode(hv_code); fp3.Add(pad3); b.Add(fp3)
blocked4 = np.zeros((g.nl, g.NY, g.NX), dtype=bool); via_block4 = np.zeros((g.NY, g.NX), dtype=bool)
w._stamp_hole_keepback(b, g, blocked4, via_block4, 0.25, 0.15, net='GND', vr_pad=0.3)
j, i = cell(20.9, 20.0)
assert via_block4[j, i], 'cell between drill-radius and pad-radius bands not stamped -- planner still under-reserves (#117)'
j, i = cell(21.0, 20.0)
assert not via_block4[j, i], 'cell beyond the pad-radius band stamped -- band too wide'

# --- edge stamp: curved Edge.Cuts are tessellated, not boxed (#118)
# 3.2 mm circle at (32, 8). With edge_clr set to 0.3 and vr 0, the
# via band radius is 0.3. The grid point (33.2, 9.2) is 0.097 mm
# from the circle at 45 degrees -- inside the true band -- but
# 0.4 mm from the nearest bounding-box side, so the bbox model
# left it unstamped (the review's section-9 geometry).
ds.m_CopperEdgeClearance = NM(0.3)
circ2 = pcbnew.PCB_SHAPE(b); circ2.SetShape(pcbnew.SHAPE_T_CIRCLE)
circ2.SetCenter(pcbnew.VECTOR2I(NM(32), NM(8))); circ2.SetRadius(NM(1.6))
circ2.SetLayer(pcbnew.Edge_Cuts); b.Add(circ2)
blocked5 = np.zeros((g.nl, g.NY, g.NX), dtype=bool); via_block5 = np.zeros((g.NY, g.NX), dtype=bool)
w._stamp_edge_keepback(b, g, blocked5, via_block5, 0.25, 0.0)
j, i = cell(33.2, 9.2)
assert via_block5[j, i], 'point 0.097 mm from a curved cutout not stamped -- planner still uses the bbox (#118)'
j, i = cell(35.5, 11.5)
assert not via_block5[j, i], 'point 2.35 mm from the circle stamped -- tessellation band too wide'
ds.m_CopperEdgeClearance = NM(0.5)

print('planner keepback parity OK')
