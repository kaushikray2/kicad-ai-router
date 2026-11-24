"""#69 integration: a pair with a known 10 mm delta is brought
within 0.5 mm by the gated meander post-pass; a confined member
that cannot fit reports its shortfall (gate rejects the bumps)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest

STACKUP = '''(stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (copper_finish "None") (edge_connector "no"))'''


def _nm(x):
    return int(round(x * 1e6))


def _build(path, dn_len=40.0, confined=False):
    import pcbnew
    def V(x, y):
        return pcbnew.VECTOR2I(_nm(x), _nm(y))
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(2)
    nets = {}
    for name in ('GND', 'DP', 'DN', 'BLK'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n
    for (x1, y1, x2, y2) in [(2, 2, 70, 2), (70, 2, 70, 38), (70, 38, 2, 38), (2, 38, 2, 2)]:
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(x1, y1)); s.SetEnd(V(x2, y2))
        s.SetLayer(pcbnew.Edge_Cuts)
        b.Add(s)
    def fp(ref, pads):
        f = pcbnew.FOOTPRINT(b)
        f.SetReference(ref)
        for (num, net, x, y) in pads:
            p = pcbnew.PAD(f)
            p.SetNumber(num); p.SetPosition(V(x, y))
            p.SetSize(V(1.0, 1.0))
            p.SetShape(pcbnew.PAD_SHAPE_RECT)
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu)
            p.SetLayerSet(ls)
            p.SetNetCode(nets[net].GetNetCode())
            f.Add(p)
        b.Add(f)
    fp('U1', [('1', 'DP', 10, 20), ('2', 'DN', 10, 24)])
    fp('U2', [('1', 'DP', 60, 20), ('2', 'DN', 10 + dn_len, 24)])
    def seg(net, x1, y1, x2, y2):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(x1, y1)); t.SetEnd(V(x2, y2))
        t.SetWidth(_nm(0.3)); t.SetLayer(pcbnew.F_Cu)
        t.SetNetCode(nets[net].GetNetCode())
        b.Add(t)
    seg('DP', 10, 20, 60, 20)
    seg('DN', 10, 24, 10 + dn_len, 24)
    if confined:
        # blocker tracks anchored by pads: an unanchored floating
        # track that foreign copper touches gets net-merged by
        # KiCad at save time (connectivity clusters), which would
        # hide the short from DRC -- real boards anchor their nets
        fp('U3', [('1', 'BLK', 5, 27), ('2', 'BLK', 25, 27),
                  ('3', 'BLK', 5, 21), ('4', 'BLK', 25, 21)])
        seg('BLK', 5, 27, 25, 27)
        seg('BLK', 5, 21, 25, 21)
    pcbnew.SaveBoard(path, b)
    src = open(path).read()
    i = src.find('(setup')
    src = src[:i + len('(setup')] + '\n    ' + STACKUP + src[i + len('(setup'):]
    open(path, 'w').write(src)


def _pipe(board):
    from ai_router import markings
    markings.save_markings(markings.markings_path_for(board), {
        'version': 1,
        'length_groups': [{'name': 'PAIR', 'nets': ['DP', 'DN'], 'tolerance_mm': 0.5}]})
    from ai_router.pipeline import Pipeline
    return Pipeline({'strip_existing': False, 'tracemaker_path': '/nonexistent'},
                    board, lambda m: None)


@pytest.mark.skipif(not os.path.exists('/usr/lib/python3/dist-packages/pcbnew.py'),
                    reason='pcbnew unavailable')
def test_meander_brings_pair_within_tolerance(tmp_path):
    board = str(tmp_path / 'lm.kicad_pcb')
    _build(board)
    pipe = _pipe(board)
    r = pipe.tool_length_match()
    assert r['status'] == 'ok', r
    g = pipe._eng_length_plan['groups'][0]
    assert g['members']['DN']['length_mm'] == 40.0
    assert g['members']['DP']['length_mm'] == 50.0
    after = g['after']['DN']
    assert after['within_tolerance'] is True, after
    assert abs(after['length_mm'] - 49.5) < 0.75, after
    assert g['members']['DN']['meander'].get('adopted') is True
    assert any('self-coupling' in a for a in g['assumptions'])


@pytest.mark.skipif(not os.path.exists('/usr/lib/python3/dist-packages/pcbnew.py'),
                    reason='pcbnew unavailable')
def test_confined_member_reports_shortfall(tmp_path):
    board = str(tmp_path / 'lm2.kicad_pcb')
    _build(board, dn_len=15.0, confined=True)
    pipe = _pipe(board)
    r = pipe.tool_length_match()
    assert r['status'] == 'ok', r
    g = pipe._eng_length_plan['groups'][0]
    dn = g['members']['DN']
    assert dn['meander'].get('adopted') is False  # gate rolled the bumps back
    assert dn.get('failed') is True
    assert dn['shortfall_mm'] > 30.0  # 50 - 15 - 0.5 tolerance, unmet
