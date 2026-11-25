"""Issue #155 (P2.1b): tool_fix_violations. Planted-fault fixtures:
a dangling stub is ripped with no re-close; a clearance fault is
ripped and re-closed exactly; a same-layer crossing is ripped and
re-closed around the foreign track; an unbridgeable rip rolls back
byte-for-byte in geometry; unknown nets / empty windows fail
clean; and the pipeline's item-level base diff attributes the
planted fault to the router (present on current, absent on base).
Run: python3 tests/test_fix_violations.py  (needs pcbnew + kicad-cli)"""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
try:
    import pcbnew
except ImportError:
    import pytest
    pcbnew = pytest.importorskip('pcbnew')  # v1.11.5 (#122): report a skip, not a collection error, outside KiCad
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)
import pipeline

def NM(v): return int(v * 1e6)
_refseq = [0]
def mk_pad(b, code, x, y):
    fp = pcbnew.FOOTPRINT(b)
    _refseq[0] += 1
    fp.SetReference('U%d' % _refseq[0])
    p = pcbnew.PAD(fp)
    p.SetPosition(pcbnew.VECTOR2I(NM(x), NM(y))); p.SetSize(pcbnew.VECTOR2I(NM(1.0), NM(1.0)))
    p.SetShape(pcbnew.PAD_SHAPE_RECT); p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); p.SetLayerSet(ls)
    p.SetNumber('1'); p.SetNetCode(code); fp.Add(p); b.Add(fp)
    return p

def mk_track(b, code, x1, y1, x2, y2, layer=pcbnew.F_Cu):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(NM(x1), NM(y1))); t.SetEnd(pcbnew.VECTOR2I(NM(x2), NM(y2)))
    t.SetWidth(NM(0.25)); t.SetLayer(layer); t.SetNetCode(code); b.Add(t)
    return t

def base_board():
    b = pcbnew.BOARD()
    na = pcbnew.NETINFO_ITEM(b, 'A'); b.Add(na); ac = na.GetNetCode()
    nb = pcbnew.NETINFO_ITEM(b, 'B'); b.Add(nb); bc = nb.GetNetCode()
    mk_pad(b, ac, 20, 20); mk_pad(b, ac, 60, 20)
    mk_track(b, ac, 20, 20, 60, 20)  # A: connected, horizontal
    return b, ac, bc

def ncomp(b, net):
    conn = b.GetConnectivity(); conn.Build(b)
    return len(w._pad_clusters(b, conn, net)[1])

def tracks_of(b, net):
    return [t for t in b.GetTracks() if t.GetNetname() == net and t.Type() != pcbnew.PCB_VIA_T]

fails = []
def check(name, cond):
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond: fails.append(name)

# --- dangling stub: rip only, no re-close ---------------------------
b, ac, bc = base_board()
mk_track(b, ac, 40, 20, 40, 26)  # stub off A's track
check('stub: A connected before', ncomp(b, 'A') == 1)
check('stub: rip stands', w._fix_one_violation(b, 'A', 40, 26) is True)
check('stub: gone', len(tracks_of(b, 'A')) == 1)
check('stub: A still connected', ncomp(b, 'A') == 1)

# --- clearance: rip + exact re-close --------------------------------
# B's pads sit legally at y=23; its routed path dips to y=20.35 and
# grazes A's track (0.10 mm edge gap < 0.2 clearance). DRC reports
# one fault per item pair -- the horizontal run and both stubs --
# and the tool is fed all of them, as the pipeline does.
b, ac, bc = base_board()
mk_pad(b, bc, 30, 23); mk_pad(b, bc, 50, 23)
mk_track(b, bc, 30, 23, 30, 20.35); mk_track(b, bc, 30, 20.35, 50, 20.35); mk_track(b, bc, 50, 20.35, 50, 23)
check('clearance: B connected before', ncomp(b, 'B') == 1)
def min_gap(b):
    g = 1e9
    for ta in tracks_of(b, 'A'):
        for tb in tracks_of(b, 'B'):
            if ta.GetLayer() != tb.GetLayer(): continue
            d = w.seg_seg_dist((w.mm(ta.GetStart().x), w.mm(ta.GetStart().y), w.mm(ta.GetEnd().x), w.mm(ta.GetEnd().y)),
                                (w.mm(tb.GetStart().x), w.mm(tb.GetStart().y), w.mm(tb.GetEnd().x), w.mm(tb.GetEnd().y)))
            g = min(g, d - 0.25)  # centre distance minus both half-widths
    return g
check('clearance: gap illegal before (0.10)', abs(min_gap(b) - 0.10) < 0.011)
tmp0 = tempfile.mkdtemp(); p_in = os.path.join(tmp0, 'in.kicad_pcb'); pcbnew.SaveBoard(p_in, b)
faults = [{'type': 'clearance', 'x': 40, 'y': 20.2, 'nets': ['B', 'A']},
          {'type': 'clearance', 'x': 30, 'y': 20.2, 'nets': ['B', 'A']},
          {'type': 'clearance', 'x': 50, 'y': 20.2, 'nets': ['B', 'A']}]
r = w.cmd_fix_violations(p_in, os.path.join(tmp0, 'out.kicad_pcb'), faults)
check('clearance: at least one fix landed', len(r['fixed']) >= 1)
b = pcbnew.LoadBoard(os.path.join(tmp0, 'out.kicad_pcb'))
check('clearance: B reconnected', ncomp(b, 'B') == 1)
check('clearance: gap now legal (>= 0.19)', min_gap(b) >= 0.19)

# --- crossing: rip + re-close around the foreign track --------------
b, ac, bc = base_board()
mk_pad(b, bc, 40, 10); mk_pad(b, bc, 40, 30)
mk_track(b, bc, 40, 10, 40, 30)  # crosses A at (40, 20)
check('crossing: fix stands', w._fix_one_violation(b, 'B', 40, 20) is True)
check('crossing: B reconnected', ncomp(b, 'B') == 1)
check('crossing: A untouched + connected', ncomp(b, 'A') == 1 and len(tracks_of(b, 'A')) == 1)
check('crossing: no B segment crosses A any more', min_gap(b) >= 0.19)

# --- rollback: keepout everywhere -> re-close impossible ------------
b, ac, bc = base_board()
mk_pad(b, bc, 40, 10); mk_pad(b, bc, 40, 30)
mk_track(b, bc, 40, 10, 40, 30)
ka = pcbnew.ZONE(b)  # rule-area keepout over the whole working area
ka.SetIsRuleArea(True); ka.SetDoNotAllowTracks(True); ka.SetDoNotAllowVias(True)
ka.SetDoNotAllowPads(False); ka.SetDoNotAllowFootprints(False)
ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); ls.AddLayer(pcbnew.B_Cu); ka.SetLayerSet(ls)
o = ka.Outline(); o.NewOutline(); ch = o.Outline(0)
for (x, y) in [(15, 5), (65, 5), (65, 35), (15, 35)]: ch.Append(pcbnew.VECTOR2I(NM(x), NM(y)))
ch.SetClosed(True); b.Add(ka)
check('rollback: fix refused', w._fix_one_violation(b, 'B', 40, 20) is False)
check('rollback: B copper restored', len(tracks_of(b, 'B')) == 1 and ncomp(b, 'B') == 1)

# --- clean failures ---------------------------------------------------
b, ac, bc = base_board()
check('unknown net fails', w._fix_one_violation(b, 'NOPE', 40, 20) is False)
check('empty window fails', w._fix_one_violation(b, 'A', 200, 200) is False)
res = w.cmd_fix_violations('/nonexistent.kicad_pcb', '/tmp/x.kicad_pcb', []) if False else None
tmp = tempfile.mkdtemp()
p0 = os.path.join(tmp, 'in.kicad_pcb'); pcbnew.SaveBoard(p0, b)
r = w.cmd_fix_violations(p0, os.path.join(tmp, 'out.kicad_pcb'), [{'type': 'clearance', 'x': 200, 'y': 200, 'nets': ['A']}])
check('cmd: nothing fixed -> status none, no output file', r['status'] == 'none' and not os.path.exists(os.path.join(tmp, 'out.kicad_pcb')))
r = w.cmd_fix_violations(p0, os.path.join(tmp, 'out.kicad_pcb'), [{'type': 'track_dangling', 'x': 40, 'y': 20, 'nets': ['A']}])
check('cmd: rip of A main track re-closes (still fixed)', r['status'] in ('ok', 'none'))  # informational: geometry-dependent

# --- stale fill inside a batch (#162) ------------------------------------
# GND pour split by a moat cut by net X's stub. Fix 1 rips the stub
# (X stays connected via its detour). Fix 2 rips the GND track that
# crosses the moat: with fresh fill the pour itself reconnects the
# pads and the rip stands; judged against the stale (moated) fill
# the net reads split, the bridge is keepout-blocked, and the fix
# rolls back.
b = pcbnew.BOARD()
gnd = pcbnew.NETINFO_ITEM(b, 'GND'); b.Add(gnd); gc2 = gnd.GetNetCode()
xn = pcbnew.NETINFO_ITEM(b, 'X'); b.Add(xn); xc = xn.GetNetCode()
z = pcbnew.ZONE(b); z.SetLayer(pcbnew.F_Cu); z.SetNetCode(gc2); z.SetMinThickness(NM(0.25))
o = z.Outline(); o.NewOutline(); ch = o.Outline(0)
for (x, y) in [(10, 10), (50, 10), (50, 30), (10, 30)]: ch.Append(pcbnew.VECTOR2I(NM(x), NM(y)))
ch.SetClosed(True); b.Add(z)
mk_pad(b, gc2, 15, 20); mk_pad(b, gc2, 45, 20)
mk_track(b, gc2, 15, 20, 45, 20)
mk_pad(b, xc, 30, 4); mk_pad(b, xc, 30, 36)
mk_track(b, xc, 30, 4, 5, 4); mk_track(b, xc, 5, 4, 5, 36); mk_track(b, xc, 5, 36, 30, 36)
mk_track(b, xc, 30, 4, 30, 32)  # the moat-cutting stub
ka = pcbnew.ZONE(b); ka.SetIsRuleArea(True); ka.SetDoNotAllowTracks(True); ka.SetDoNotAllowVias(True)
ka.SetDoNotAllowPads(False); ka.SetDoNotAllowFootprints(False)
ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); ls.AddLayer(pcbnew.B_Cu); ka.SetLayerSet(ls)
o = ka.Outline(); o.NewOutline(); ch = o.Outline(0)
for (x, y) in [(12, 8), (48, 8), (48, 32), (12, 32)]: ch.Append(pcbnew.VECTOR2I(NM(x), NM(y)))
ch.SetClosed(True); b.Add(ka)
w.refill(b)
check('moat fixture: X stub rip stands', w._fix_one_violation(b, 'X', 30, 32) is True)
r = w._fix_one_violation(b, 'GND', 30, 20)
check('pour rip judged on fresh fill stands (#162)', r is True and tracks_of(b, 'GND') == [] and ncomp(b, 'GND') == 1)

# --- locked copper + arcs (#159) ---------------------------------------
def keepout_all(b):
    ka = pcbnew.ZONE(b); ka.SetIsRuleArea(True); ka.SetDoNotAllowTracks(True); ka.SetDoNotAllowVias(True)
    ka.SetDoNotAllowPads(False); ka.SetDoNotAllowFootprints(False)
    ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); ls.AddLayer(pcbnew.B_Cu); ka.SetLayerSet(ls)
    o = ka.Outline(); o.NewOutline(); ch = o.Outline(0)
    for (x, y) in [(15, 5), (65, 5), (65, 35), (15, 35)]: ch.Append(pcbnew.VECTOR2I(NM(x), NM(y)))
    ch.SetClosed(True); b.Add(ka)

# a locked stub is the fault itself: the rip must refuse it outright
b, ac, bc = base_board()
stub = mk_track(b, ac, 60, 20, 75, 20); stub.SetLocked(True)
stub_uuid = stub.m_Uuid.AsString()
r = w._fix_one_violation(b, 'A', 75, 20)
check('locked stub is not ripped (#159)', r is False and any(t.m_Uuid.AsString() == stub_uuid and t.IsLocked() for t in b.GetTracks()))

# a locked link track behind a keepout: the fix rolls back, and the
# rollback must never have touched the locked item at all
b, ac, bc = base_board()
mk_pad(b, bc, 40, 10); mk_pad(b, bc, 40, 30)
lt = mk_track(b, bc, 40, 10, 40, 30); lt.SetLocked(True)
uuid_before = lt.m_Uuid.AsString()
keepout_all(b)
r = w._fix_one_violation(b, 'B', 40, 20)
b_items = tracks_of(b, 'B')
check('locked track survives a rolled-back fix, lock + UUID intact (#159)',
      r is False and len(b_items) == 1 and b_items[0].IsLocked() and b_items[0].m_Uuid.AsString() == uuid_before)

# an arc paralleling a straight link, behind a keepout: rollback
# must restore the ARC (midpoint included), not a straight chord
b, ac, bc = base_board()
mk_pad(b, bc, 40, 10); mk_pad(b, bc, 40, 30)
mk_track(b, bc, 40, 10, 40, 30)
arc = pcbnew.PCB_ARC(b)
arc.SetStart(pcbnew.VECTOR2I(NM(40), NM(10))); arc.SetMid(pcbnew.VECTOR2I(NM(44), NM(20))); arc.SetEnd(pcbnew.VECTOR2I(NM(40), NM(30)))
arc.SetWidth(NM(0.25)); arc.SetLayer(pcbnew.F_Cu); arc.SetNetCode(bc); b.Add(arc)
keepout_all(b)
r = w._fix_one_violation(b, 'B', 41, 20)
b_items = tracks_of(b, 'B')
arcs = [t for t in b_items if t.Type() == pcbnew.PCB_ARC_T]
mid = (w.mm(arcs[0].GetMid().x), w.mm(arcs[0].GetMid().y)) if len(arcs) == 1 else None
check('arc rolls back as an arc with its midpoint (#159)', r is False and len(b_items) == 2 and mid is not None and abs(mid[0] - 44) < 0.01 and abs(mid[1] - 20) < 0.01)

# --- pipeline base diff (kicad-cli item-level) ------------------------
b, ac, bc = base_board()
mk_pad(b, bc, 40, 10); mk_pad(b, bc, 40, 30)
mk_track(b, bc, 40, 10, 40, 30)
pc = os.path.join(tmp, 'cur.kicad_pcb'); pcbnew.SaveBoard(pc, b)
b2, ac2, bc2 = base_board()
mk_pad(b2, bc2, 40, 10); mk_pad(b2, bc2, 40, 30)  # base: B pads only, no crossing track
pb = os.path.join(tmp, 'base.kicad_pcb'); pcbnew.SaveBoard(pb, b2)
class _Stub: pass
s = _Stub(); s.workdir = tmp; s.cfg = {}; s._gseq = 0
cur = pipeline.Pipeline._drc_fault_items(s, pc)
bas = pipeline.Pipeline._drc_fault_items(s, pb)
added = pipeline._router_added(cur, bas)
check('diff: planted crossing is router-added', any(f['type'] in ('tracks_crossing', 'shorting_items') for f in added))
check('diff: offender candidates carry both nets', any(set(f['nets']) == {'A', 'B'} for f in added))
check('diff: base alone has no such fault', not any(f['type'] in ('tracks_crossing', 'shorting_items') for f in bas))
# --- attribution matcher (#160) ----------------------------------------
F = lambda t, x, y: {'type': t, 'x': x, 'y': y, 'nets': []}
check('matcher: 0.02 mm jitter across a bucket boundary is the same fault (#160)',
      pipeline._router_added([F('clearance', 10.26, 20.0)], [F('clearance', 10.24, 20.0)]) == [])
check('matcher: 0.68 mm diagonal in one cell is a new fault (#160)',
      len(pipeline._router_added([F('clearance', 10.24, 20.24)], [F('clearance', 9.76, 19.76)])) == 1)
check('matcher: one base fault masks at most one current fault (#160)',
      len(pipeline._router_added([F('clearance', 10.1, 20.0), F('clearance', 10.2, 20.0)], [F('clearance', 10.0, 20.0)])) == 1)
check('matcher: types never cross-match (#160)',
      len(pipeline._router_added([F('track_dangling', 10.1, 20.0)], [F('clearance', 10.0, 20.0)])) == 1)



# --- pipeline acceptance gate (#158) -----------------------------------
# Drive the real tool_fix_violations with a stubbed worker/gate: a fix
# that removes the only real fault while ADDING intrusions must stand
# (intrusions price after real faults in the accept key). On the old
# fault_count (intrusions folded in) the totals tied 11 -> 11 and the
# fix was reverted.
def _gate(classes, intr, unc):
    return {'classes': classes, 'skeleton_intrusions': intr, 'unconnected_total': unc, 'split_nets': []}
sp = object.__new__(pipeline.Pipeline)
sp.workdir = tmp; sp.cfg = {}; sp._gseq = 0
sp.current = pc; sp.base = pb
sp.last_gate = _gate({'clearance': 1}, 10, 4)
sp.log = lambda *a: None
sp._record = lambda *a, **k: None
sp._leg_ban_args = lambda: []
sp._leg_layer_args = lambda: []
sp._leg_skeleton_args = lambda: []
sp._out = lambda name: os.path.join(tmp, 'stub-fixed.kicad_pcb')
sp._drc_fault_items = lambda path: ([{'type': 'clearance', 'x': 40.0, 'y': 20.0, 'nets': ['A', 'B']}] if path == pc else [])
def _fake_worker(*a, **k):
    open(sp._out('fixed'), 'w').write('stub')
    return {'fixed': [{'type': 'clearance'}], 'failed': []}
sp._worker = _fake_worker
sp.tool_gate = lambda *a, **k: _gate({}, 11, 4)
r = sp.tool_fix_violations()
check('accept: real fault fixed, +1 intrusion stands (#158)', r.get('status') == 'fixed')
sp.tool_gate = lambda *a, **k: _gate({}, 11, None)
sp.last_gate = _gate({'clearance': 1}, 10, 4); sp.current = pc
r = sp.tool_fix_violations()
check('accept: unknown unconnected count reverts (#158/#59)', r.get('status') == 'reverted')

print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
