"""#71 integration: crystal quiet gates on a real board through
hvgeom facts -- clean passes, a planted track + a via inside
the land pattern are caught and located, and opt-in
materialisation produces a domain spec (#80: no zones)."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pcbnew
from ai_router.pipeline import Pipeline

V = pcbnew.VECTOR2I
NM = 1000000
STACKUP = '''(stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.2) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "In1.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 2" (type "core") (thickness 1.11) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "In2.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 3" (type "prepreg") (thickness 0.2) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (copper_finish "None") (edge_connector "no"))'''


def _via(b, nets, net, x, y, d=0.6, drill=0.3):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(V(int(x * NM), int(y * NM)))
    v.SetWidth(int(d * NM))
    v.SetDrill(int(drill * NM))
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNet(nets[net])
    b.Add(v)


def _build(path, planted=False, materialise=False):
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(4)
    nets = {}
    for name in ('XTAL1', 'XTAL2', 'GND', 'DIG', 'GUARD'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n
    for (x1, y1, x2, y2) in [(2, 2, 60, 2), (60, 2, 60, 40),
                             (60, 40, 2, 40), (2, 40, 2, 2)]:
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
            p.SetSize(V(int(1.6 * NM), int(1.6 * NM)))
            p.SetShape(pcbnew.PAD_SHAPE_RECT)
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            ls = pcbnew.LSET()
            ls.AddLayer(pcbnew.F_Cu)
            p.SetLayerSet(ls)
            p.SetNet(nets[net])
            f.Add(p)
        b.Add(f)
    fp('X1', [('1', 'XTAL1', 18, 20), ('2', 'XTAL2', 22, 20),
              ('3', 'GND', 20, 23)])
    fp('C1', [('1', 'XTAL1', 14, 19), ('2', 'GND', 14, 21.5)])
    fp('C2', [('1', 'XTAL2', 26, 19), ('2', 'GND', 26, 21.5)])
    def seg(net, x1, y1, x2, y2, layer=pcbnew.F_Cu, w=0.3):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(int(x1 * NM), int(y1 * NM)))
        t.SetEnd(V(int(x2 * NM), int(y2 * NM)))
        t.SetWidth(int(w * NM))
        t.SetLayer(layer)
        t.SetNet(nets[net])
        b.Add(t)
    seg('XTAL1', 14, 20, 18, 20)
    seg('XTAL2', 22, 20, 26, 20)
    seg('DIG', 5, 35, 55, 35)
    # guard ring segment tied to GND at both ends by net-tie parts
    seg('GUARD', 10, 12, 30, 12)
    fp('NT1', [('1', 'GUARD', 10, 12), ('2', 'GND', 10, 13.2)])
    fp('NT2', [('1', 'GUARD', 30, 12), ('2', 'GND', 30, 13.2)])
    _via(b, nets, 'GND', 14, 23)
    _via(b, nets, 'GND', 26, 23)
    if planted:
        seg('DIG', 5, 20.8, 55, 20.8)
        _via(b, nets, 'DIG', 20, 20.5)
    # GND pour on In1.Cu
    z = pcbnew.ZONE(b)
    ls = pcbnew.LSET()
    ls.AddLayer(pcbnew.In1_Cu)
    z.SetLayerSet(ls)
    z.SetNet(nets['GND'])
    out = z.Outline()
    out.NewOutline()
    ch = out.Outline(0)
    for (x, y) in [(3, 3), (59, 3), (59, 39), (3, 39)]:
        ch.Append(int(x * NM), int(y * NM))
    b.Add(z)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    with open(path.replace('.kicad_pcb', '.kicad_pro'), 'w') as f:
        f.write('{}\n')
    pcbnew.SaveBoard(path, b)
    t = open(path).read()
    i = t.index('(setup')
    t = t[:i + len('(setup')] + '\n    ' + STACKUP + t[i + len('(setup'):]
    open(path, 'w').write(t)
    mk = {'version': 1, 'quiet': [{'ref': 'X1', 'profile': 'crystal',
                                   'guard_net': 'GUARD',
                                   'materialise': materialise}]}
    with open(path.replace('.kicad_pcb', '.ai-router-markings.json'), 'w') as f:
        json.dump(mk, f)


def _pipe(board):
    return Pipeline({'strip_existing': False,
                     'tracemaker_path': '/nonexistent'},
                    board, lambda m: None)


def test_quiet_clean_board_passes(tmp_path):
    board = str(tmp_path / 'q.kicad_pcb')
    _build(board)
    rep = _pipe(board).tool_quiet_nets()
    r = rep['results'][0]
    assert r['pass'], r
    assert r['intrusions'] == []
    assert r['guard']['ok']
    assert all(c['ground_return'] for c in r['load_caps'])


def test_quiet_planted_intrusions_caught(tmp_path):
    board = str(tmp_path / 'q.kicad_pcb')
    _build(board, planted=True)
    rep = _pipe(board).tool_quiet_nets()
    r = rep['results'][0]
    assert not r['pass']
    kinds = {(i['kind'], i['net']) for i in r['intrusions']}
    assert ('corridor', 'DIG') in kinds
    assert ('land_pattern', 'DIG') in kinds
    v = [i for i in r['intrusions'] if i['kind'] == 'corridor'][0]
    assert abs(v['at'][1] - 20.8) < 0.6


def test_quiet_materialise_enforces_via_domains_not_zones(tmp_path):
    # #80: no rule areas (they sealed the quiet nets' own pads too);
    # the isolation geometry goes to domains.json, exempting exactly
    # the nets the checker allows (own + allow_nets + guard).
    board = str(tmp_path / 'q.kicad_pcb')
    _build(board, materialise=True)
    pipe = _pipe(board)
    assert not [e for e in pipe.ledger
                if e.get('tool') == 'quiet_materialise']
    entries = [e for e in pipe.ledger if e.get('tool') == 'domain_enforce']
    assert entries and entries[0]['entries'] == 1, pipe.ledger
    b = pcbnew.LoadBoard(pipe.current)
    assert not [z for z in b.Zones() if z.GetIsRuleArea()]
    spec = json.load(open(os.path.join(pipe.workdir, 'domains.json')))
    q = [e for e in spec if e['kind'] == 'quiet'][0]
    assert set(q['nets']) == {'XTAL1', 'XTAL2', 'GND', 'GUARD'}
    assert q['rects'] and all(len(r['layers']) == 4 for r in q['rects'])


def test_quiet_report_echoes_materialise_state(tmp_path):
    # #219: evaluate echoes the entry's materialise flag and the
    # pipeline stamps whether the keepout actually landed in
    # domains.json enforcement.
    board = str(tmp_path / 'q.kicad_pcb')
    _build(board, materialise=True)
    r = _pipe(board).tool_quiet_nets()['results'][0]
    assert r['materialise'] is True, r
    assert r['enforced_in_domains'] is True, r
    board2 = str(tmp_path / 'q2.kicad_pcb')
    _build(board2, materialise=False)
    r2 = _pipe(board2).tool_quiet_nets()['results'][0]
    assert r2['materialise'] is False, r2
    assert r2['enforced_in_domains'] is False, r2
