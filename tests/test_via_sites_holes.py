"""Issue #111 (residual of #78): _ViaSites.ok owed FOREIGN drills
only the copper tests.  An NPTH pad (net '', ~no copper annulus)
was tested by pad SDF at the netclass clearance (~0.20 mm), never
against m_HoleClearance (0.25 mm) or m_HoleToHoleMin -- a
candidate could pass ok(), fail DRC, and roll the whole
rf_vias/power_vias pass back at the gate.  Foreign drills (pad
and via) now get the hole rules; same-net behaviour is unchanged
(covered by test_via_sites.py).
Run: python3 tests/test_via_sites_holes.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pcbnew
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

def NM(v): return int(round(v * 1e6))
def mm(v): return v / 1e6

b = pcbnew.BOARD()
net = pcbnew.NETINFO_ITEM(b, 'GND'); b.Add(net)
ds = b.GetDesignSettings()
ds.m_HoleClearance = NM(0.25); ds.m_HoleToHoleMin = NM(0.25)
hole_clr = mm(ds.m_HoleClearance)

# NPTH mounting hole at (20, 20): 1.0 mm drill, no copper annulus
# beyond the drill itself (pad size == drill size).
fp = pcbnew.FOOTPRINT(b); fp.SetReference('MH1')
p = pcbnew.PAD(fp); p.SetNumber('1')
p.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
p.SetPosition(pcbnew.VECTOR2I(NM(20), NM(20)))
p.SetSize(pcbnew.VECTOR2I(NM(1.0), NM(1.0)))
p.SetDrillSize(pcbnew.VECTOR2I(NM(1.0), NM(1.0)))
p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
fp.Add(p); b.Add(fp)

sites = w._ViaSites(b, 'GND', contain_net=None)
fdr = 0.5                      # NPTH drill radius
r = sites.vw / 2               # candidate via copper radius
assert hole_clr > sites.clr, 'fixture cannot discriminate: hole_clr %s <= clr %s' % (hole_clr, sites.clr)
copper_bound = fdr + sites.clr + r        # pad-SDF copper test gives up here
hole_bound = fdr + hole_clr + r           # hole_clearance binds out to here
d = (copper_bound + hole_bound) / 2       # inside the hole band, outside the copper band
assert not sites.ok(20 + d, 20), 'via at %.3f mm from an NPTH drill passed: copper-clear but inside hole_clearance (bound %.3f)' % (d, hole_bound)
assert sites.ok(20 + hole_bound + 0.1, 20), 'via clear of the hole band was rejected'
print('via sites holes OK (NPTH hole_clearance band enforced: clr %.2f, hole_clr %.2f)' % (sites.clr, hole_clr))
