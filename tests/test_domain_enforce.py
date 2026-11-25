"""#80 regression: net-selective domain enforcement in the finisher
Grid.  The marked (HV) net must reach its own pads -- the retired
pre-route rule areas sealed them, because KiCad rule areas are not
net-selective -- while a foreign net's leg is held at the required
domain spacing by the Grid stamp.  The post-route checker catching
a planted violation is covered by test_engineered_hv.py."""
import json, math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pcbnew
from ai_router.pipeline import Pipeline

V = pcbnew.VECTOR2I
NM = 1000000


def _build(path):
    b = pcbnew.BOARD()
    nets = {}
    for name in ('HV1', 'LV1'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n
    for (x1, y1, x2, y2) in [(2, 2, 70, 2), (70, 2, 70, 38),
                             (70, 38, 2, 38), (2, 38, 2, 2)]:
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(int(x1 * NM), int(y1 * NM)))
        s.SetEnd(V(int(x2 * NM), int(y2 * NM)))
        s.SetLayer(pcbnew.Edge_Cuts)
        b.Add(s)
    def fp(ref, pads):
        f = pcbnew.FOOTPRINT(b)
        f.SetReference(ref)
        for (num, net, x, y) in pads:
            p = pcbnew.PAD(f)
            p.SetNumber(num)
            p.SetPosition(V(int(x * NM), int(y * NM)))
            p.SetSize(V(int(2 * NM), int(2 * NM)))
            p.SetShape(pcbnew.PAD_SHAPE_RECT)
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            ls = pcbnew.LSET()
            ls.AddLayer(pcbnew.F_Cu)
            p.SetLayerSet(ls)
            p.SetNet(nets[net])
            f.Add(p)
        b.Add(f)
    # HV1's pads sit inside the HV domain rects (half-size 3.5 mm:
    # 1 mm pad radius + 2.5 mm required spacing at 400 V).  LV1's
    # straight path between its pads crosses the first rect dead
    # centre.
    fp('U1', [('1', 'HV1', 20, 20), ('2', 'HV1', 20, 32)])
    fp('U2', [('1', 'LV1', 10, 20), ('2', 'LV1', 30, 20)])
    with open(path.replace('.kicad_pcb', '.kicad_pro'), 'w') as f:
        f.write('{}\n')
    stack = '''(stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (copper_finish "None") (edge_connector "no"))'''
    pcbnew.SaveBoard(path, b)
    t = open(path).read()
    i = t.index('(setup')
    t = t[:i + len('(setup')] + '\n    ' + stack + t[i + len('(setup'):]
    open(path, 'w').write(t)
    mk = {'version': 1, 'hv_domains': [
        {'name': 'HV', 'nets': ['HV1'], 'voltage_v': 400,
         'materialise': True},
        {'name': 'LV', 'nets': ['LV1'], 'voltage_v': 0}]}
    with open(path.replace('.kicad_pcb', '.ai-router-markings.json'), 'w') as f:
        json.dump(mk, f)


def _pipe(board):
    return Pipeline({'strip_existing': False,
                     'tracemaker_path': '/nonexistent'},
                    board, lambda m: None)


def _seg_pt(px, py, x1, y1, x2, y2):
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((px - x1) * vx + (py - y1) * vy) / L2))
    return math.hypot(px - (x1 + t * vx), py - (y1 + t * vy))


def test_marked_net_reaches_its_own_pads(tmp_path):
    board = str(tmp_path / 'd.kicad_pcb')
    _build(board)
    pipe = _pipe(board)
    b = pcbnew.LoadBoard(pipe.current)
    assert not [z for z in b.Zones() if z.GetIsRuleArea()]
    r = pipe.tool_finish_leg('HV1')
    assert r['status'] == 'routed', r


def test_foreign_leg_keeps_domain_spacing(tmp_path):
    board = str(tmp_path / 'd.kicad_pcb')
    _build(board)
    pipe = _pipe(board)
    r = pipe.tool_finish_leg('LV1')
    assert r['status'] == 'routed', r
    b = pcbnew.LoadBoard(pipe.current)
    segs = [t for t in b.GetTracks()
            if t.GetNetname() == 'LV1' and t.Type() != pcbnew.PCB_VIA_T]
    assert segs
    for t in segs:
        d = _seg_pt(20, 20, t.GetStart().x / NM, t.GetStart().y / NM,
                    t.GetEnd().x / NM, t.GetEnd().y / NM)
        # rect half-size is 3.5 mm around the pad centre; the drawn
        # centreline must stay outside it (0.2 mm grid slack allowed)
        assert d >= 3.3, (d, t.GetStart().x, t.GetEnd().x)
    for t in b.GetTracks():
        if t.GetNetname() == 'LV1' and t.Type() == pcbnew.PCB_VIA_T:
            d = math.hypot(t.GetPosition().x / NM - 20,
                           t.GetPosition().y / NM - 20)
            assert d >= 3.3, d
