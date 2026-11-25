"""Issue #78: _ViaSites (worker) -- (a) it crashed on any board with
 a rule-area zone (GetZoneRuleAreaSettings().GetNoVia() does not
exist in KiCad 10; the API is GetDoNotAllowVias); (b) ok() never
tested same-net hole_to_hole, so cmd_power_vias ring placements
landed 0.31 mm apart with 0.6 mm vias; (c) no-via areas were tested
by centre point only (no via-radius dilation) and board edges were
not tested at all.
Run: python3 tests/test_via_sites.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, math, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pcbnew
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

def NM(v): return int(round(v * 1e6))

def base_board():
    b = pcbnew.BOARD()
    net = pcbnew.NETINFO_ITEM(b, 'GND'); b.Add(net)
    return b

def rule_zone(b, x0, y0, x1, y1):
    z = pcbnew.ZONE(b); z.SetLayer(pcbnew.F_Cu); z.SetNetCode(0)
    z.SetIsRuleArea(True); z.SetDoNotAllowVias(True)
    ch = pcbnew.SHAPE_LINE_CHAIN()
    for (x, y) in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)): ch.Append(NM(x), NM(y))
    ch.SetClosed(True); z.Outline().AddOutline(ch); b.Add(z)

# (a)+(c1): rule-area board must construct; dilation by via radius
b = base_board(); rule_zone(b, 10, 10, 20, 20)
sites = w._ViaSites(b, 'GND', contain_net=None)
assert sites.ok(15, 15) is False, 'via centre inside a no-via area passed'
assert sites.ok(20.1, 15) is False, 'via barrel overlapping a no-via area passed (no dilation)'
assert sites.ok(25, 15) is True, 'clear site rejected'

# (c2): board edge distance
b = base_board()
sh = pcbnew.PCB_SHAPE(b); sh.SetShape(pcbnew.SHAPE_T_SEGMENT)
sh.SetStart(pcbnew.VECTOR2I(NM(0), NM(0))); sh.SetEnd(pcbnew.VECTOR2I(NM(40), NM(0)))
sh.SetLayer(pcbnew.Edge_Cuts); b.Add(sh)
sites = w._ViaSites(b, 'GND', contain_net=None)
assert sites.ok(20, 0.2) is False, 'via 0.2 mm from the board edge passed'
assert sites.ok(20, 5) is True, 'clear site rejected'

# (b): power-via rings must respect same-net hole_to_hole
b = base_board()
_av = pcbnew.PCB_VIA(b)   # anchor via on the net (also persists the net through save/reload)
_av.SetPosition(pcbnew.VECTOR2I(NM(30), NM(30))); _av.SetDrill(NM(0.3)); _av.SetWidth(NM(0.6))
_av.SetNetCode(1); _av.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); b.Add(_av)
d = tempfile.mkdtemp(); path = os.path.join(d, 'ring.kicad_pcb')
pcbnew.SaveBoard(path, b)
res = w.cmd_power_vias(path, [{'net': 'GND', 'x': 30.0, 'y': 30.0, 'add': 6}])
pts = [(p['x'], p['y']) for p in res['placed']]
assert len(pts) == 6, res
mind = min(math.hypot(x1 - x2, y1 - y2) for i, (x1, y1) in enumerate(pts) for (x2, y2) in pts[i + 1:])
assert mind >= 0.55 - 1e-9, 'placed vias %s: min pairwise %.3f mm < hole_to_hole bound 0.55' % (pts, mind)
print('via sites OK (rule areas, dilation, edge, ring hole_to_hole)')
