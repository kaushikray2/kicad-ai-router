"""Issue #63: pour-skeleton reservation. The skeleton computation
(pad spanning tree per zone/layer, pads confined to the pour
region, width = zone min-thickness + slack), the Grid stamp
(corridors hard for foreign nets, open for the pour's own), the
intrusion census (foreign copper in a corridor), and the pipeline
wiring (fault_count counts intrusions; legs carry --skeleton only
when the config turns the reservation on).
Run: python3 tests/test_pour_skeleton.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pcbnew
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)
import pipeline

def NM(v): return int(v * 1e6)

_refseq = [0]
def mk_pad(b, net, code, x, y):
    fp = pcbnew.FOOTPRINT(b)
    _refseq[0] += 1
    fp.SetReference('U%d' % _refseq[0])  # _pad_clusters keys pads by Ref.Number
    p = pcbnew.PAD(fp)
    p.SetPosition(pcbnew.VECTOR2I(NM(x), NM(y))); p.SetSize(pcbnew.VECTOR2I(NM(1.0), NM(1.0)))
    p.SetShape(pcbnew.PAD_SHAPE_RECT); p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); p.SetLayerSet(ls)
    p.SetNumber('1'); p.SetNetCode(code); fp.Add(p); b.Add(fp)
    return p

def mk_board(with_track=False):
    b = pcbnew.BOARD()
    gnd = pcbnew.NETINFO_ITEM(b, 'GND'); b.Add(gnd); gc = gnd.GetNetCode()
    sig = pcbnew.NETINFO_ITEM(b, 'SIG'); b.Add(sig); sc = sig.GetNetCode()
    z = pcbnew.ZONE(b)
    z.SetLayer(pcbnew.F_Cu); z.SetNetCode(gc); z.SetMinThickness(NM(0.25))
    o = z.Outline(); o.NewOutline()
    ch = o.Outline(0)
    for (x, y) in [(10, 10), (50, 10), (50, 40), (10, 40)]:
        ch.Append(pcbnew.VECTOR2I(NM(x), NM(y)))
    ch.SetClosed(True)
    b.Add(z)
    for (x, y) in [(15, 15), (45, 15), (45, 35), (15, 35)]:
        mk_pad(b, gnd, gc, x, y)
    mk_pad(b, gnd, gc, 90, 90)  # same net, OUTSIDE the pour region
    mk_pad(b, sig, sc, 30, 2)
    if with_track:
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(NM(30), NM(5))); t.SetEnd(pcbnew.VECTOR2I(NM(30), NM(45)))
        t.SetWidth(NM(0.25)); t.SetLayer(pcbnew.F_Cu); t.SetNetCode(sc); b.Add(t)
    return b

fails = []
def check(name, cond):
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond: fails.append(name)

# --- skeleton computation -------------------------------------------
b = mk_board()
sk = w.pour_skeletons(b)
check('one skeleton for the single pour/layer', len(sk) == 1)
s0 = sk[0]
check('skeleton net/layer', s0['net'] == 'GND' and s0['layer'] == pcbnew.F_Cu)
check('pads confined to the pour region (4, not 5)', len(s0['pads']) == 4)
check('MST edges = pads - 1', len(s0['edges']) == 3)
check('width = zone min thickness + slack', abs(s0['width'] - 0.35) < 1e-9)

# --- intrusion census ------------------------------------------------
check('no tracks -> no intrusions', w.skeleton_intrusions(mk_board()) == (0, 0.0, {}))
b2 = mk_board(with_track=True)
items, mm, per = w.skeleton_intrusions(b2)
check('crossing track intrudes', items >= 1 and mm > 0 and per.get('GND', 0) >= 1)

# --- Grid stamp -------------------------------------------------------
w.SKELETON_ON = True
g = w.Grid(mk_board(), (5, 55, 5, 45))
check('grid collected the skeleton', len(g.skeletons) == 1)
blocked, _vb = g.build(set(), target='SIG')
i, j = g.cell(30, 15)  # midpoint of the (15,15)-(45,15) tree edge
check('corridor hard for a foreign net', bool(blocked[g.skeletons[0]['layer_idx'], j, i]))
blocked2, _vb2 = g.build(set(), target='GND')
check('corridor open for the pour itself', not blocked2[g.skeletons[0]['layer_idx'], j, i])
hard, _vh, _own, _vown, _nets = g.build_owners({'SIG'}, 'SIG')
check('corridor hard in the owners grid (no cut-set through a pour)', bool(hard[g.skeletons[0]['layer_idx'], j, i]))
w.SKELETON_ON = False
g2 = w.Grid(mk_board(), (5, 55, 5, 45))
blocked3, _vb3 = g2.build(set(), target='SIG')
check('reservation off -> corridor not stamped', not blocked3[0, j, i] and not g2.skeletons)

# --- worker CLI: summary census keys follow the flag ------------------
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, 'fx.kicad_pcb')
pcbnew.SaveBoard(path, mk_board(with_track=True))
worker = os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py')
def run_worker(*args):
    out = subprocess.run([sys.executable, worker] + list(args), capture_output=True, text=True)
    line = [l for l in out.stdout.splitlines() if l.startswith('AIR_RESULT ')]
    assert line, out.stdout + out.stderr
    return json.loads(line[0][len('AIR_RESULT '):])
s_on = run_worker('summary', '--board', path, '--fast', '--skeleton')
check('summary --skeleton carries the census', s_on.get('skeleton_intrusions', 0) >= 1)
s_off = run_worker('summary', '--board', path, '--fast')
check('summary without the flag has no census keys', 'skeleton_intrusions' not in s_off)
sk_cli = run_worker('skeleton', '--board', path)
check('skeleton command reports the severed edge', sk_cli['skeletons'] and sk_cli['skeletons'][0]['severed_edges'] >= 1)

# --- pipeline wiring ---------------------------------------------------
check('fault_count counts intrusions', pipeline.fault_count({'classes': {}, 'skeleton_intrusions': 3}) == 3)
check('fault_count unchanged for old gate dicts', pipeline.fault_count({'classes': {}}) == 0)
p = object.__new__(pipeline.Pipeline)
p.cfg = {'pour_skeleton': True}
check('legs carry --skeleton when on', p._leg_skeleton_args() == ['--skeleton'])
p.cfg = {'pour_skeleton': False}
check('legs carry nothing when off (pre-feature command lines)', p._leg_skeleton_args() == [])
p.cfg = {}
check('reservation defaults on', p._leg_skeleton_args() == ['--skeleton'])

# --- accept-key ordering: intrusions price after connectivity --------
import orchestrator
def G(faults, unc, intr):
    return {'classes': {'clearance': faults}, 'unconnected_total': unc, 'split_nets': [], 'skeleton_intrusions': intr}
check('a connection beats intrusions', pipeline._accept_key(G(5, 2, 7)) < pipeline._accept_key(G(5, 3, 0)))
check('a real fault beats intrusion-freedom', pipeline._accept_key(G(5, 2, 7)) < pipeline._accept_key(G(6, 2, 0)))
check('intrusions break ties', pipeline._accept_key(G(5, 2, 3)) < pipeline._accept_key(G(5, 2, 7)))
check('final key mirrors the order', orchestrator._final_key(G(5, 2, 7)) < orchestrator._final_key(G(6, 2, 0)) and orchestrator._final_key(G(5, 2, 7)) < orchestrator._final_key(G(5, 3, 0)))
check('fault_count still counts intrusions in full', pipeline.fault_count(G(5, 2, 7)) == 12)

# --- yield: a leg that can only connect through a corridor ------------
# GND pour band across the whole board; its skeleton edge spans the
# board at y=20. SIG's pads sit above/below the band: every path
# crosses the corridor, so the reserved plan fails and the command
# must yield (route anyway, intrusion priced by the gate). SIG2's
# pads are both above the band: routed without yielding.
def mk_yield_board():
    b = pcbnew.BOARD()
    gnd = pcbnew.NETINFO_ITEM(b, 'GND'); b.Add(gnd); gc = gnd.GetNetCode()
    sig = pcbnew.NETINFO_ITEM(b, 'SIG'); b.Add(sig); sc = sig.GetNetCode()
    sg2 = pcbnew.NETINFO_ITEM(b, 'SIG2'); b.Add(sg2); s2 = sg2.GetNetCode()
    z = pcbnew.ZONE(b)
    _zls = pcbnew.LSET(); _zls.AddLayer(pcbnew.F_Cu); _zls.AddLayer(pcbnew.B_Cu)
    z.SetLayerSet(_zls); z.SetNetCode(gc); z.SetMinThickness(NM(0.25))
    o = z.Outline(); o.NewOutline()
    ch = o.Outline(0)
    for (x, y) in [(0, 10), (100, 10), (100, 30), (0, 30)]:
        ch.Append(pcbnew.VECTOR2I(NM(x), NM(y)))
    ch.SetClosed(True)
    b.Add(z)
    # TH pads: the pour's connection points exist on BOTH layers, so
    # both layers carry the reservation (an SMD-only pour would
    # leave the other layer legitimately crossable -- a track there
    # does not sever this layer's fill).
    for (x, y) in [(5, 20), (95, 20)]:
        fp = pcbnew.FOOTPRINT(b)
        _refseq[0] += 1; fp.SetReference('U%d' % _refseq[0])
        p = pcbnew.PAD(fp)
        p.SetPosition(pcbnew.VECTOR2I(NM(x), NM(y))); p.SetSize(pcbnew.VECTOR2I(NM(1.6), NM(1.6)))
        p.SetShape(pcbnew.PAD_SHAPE_CIRCLE); p.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        _pls = pcbnew.LSET(); _pls.AddLayer(pcbnew.F_Cu); _pls.AddLayer(pcbnew.B_Cu)
        p.SetLayerSet(_pls); p.SetDrillSize(pcbnew.VECTOR2I(NM(0.8), NM(0.8)))
        p.SetNumber('1'); p.SetNetCode(gc); fp.Add(p); b.Add(fp)
    mk_pad(b, sig, sc, 50, 5); mk_pad(b, sig, sc, 50, 35)
    mk_pad(b, sg2, s2, 20, 5); mk_pad(b, sg2, s2, 80, 5)
    return b
yp = os.path.join(tmp, 'yield.kicad_pcb')
pcbnew.SaveBoard(yp, mk_yield_board())
yout = os.path.join(tmp, 'yield-out.kicad_pcb')
res = run_worker('finish_leg', '--board', yp, '--out', yout, '--net', 'SIG', '--skeleton')
check('corridor-only leg routes via the yield', res.get('status') == 'routed' and res.get('skeleton_yield') is True)
ys = run_worker('summary', '--board', yout, '--fast', '--skeleton')
check('the yielded leg pays the census', (ys.get('skeleton_intrusions') or 0) >= 1)
res2 = run_worker('finish_leg', '--board', yp, '--out', yout, '--net', 'SIG2', '--skeleton')
check('unblocked leg routes without yielding', res2.get('status') == 'routed' and 'skeleton_yield' not in res2)
res3 = run_worker('finish_leg', '--board', yp, '--out', yout, '--net', 'SIG')
check('no reservation -> no yield marker', res3.get('status') == 'routed' and 'skeleton_yield' not in res3)

print('FAILURES: %s' % fails if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
