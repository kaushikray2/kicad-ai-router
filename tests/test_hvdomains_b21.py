"""#146 (B21) regressions: real pad geometry, tessellated arcs,
board-polygon outlines, an implicit rest-of-board domain,
mistyped nets and unmeasurable creepage reported honestly,
the creepage pitch reported and budgeted."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import hvdomains as H
from ai_router.pipeline import Pipeline


def test_rect_pads_measured_corner_to_corner():
    # the reviewer's repro: two 2x2 mm pads offset 3.5 mm in x and
    # y are 2.12 mm apart corner-to-corner; the inscribed-disc
    # model said 2.95 mm and passed them at 400 V (2.5 required)
    pa = {'x': 0.0, 'y': 0.0, 'w': 2.0, 'h': 2.0,
          'skind': 'rect', 'sx': 1.0, 'sy': 1.0, 'rot': 0.0}
    pb = {'x': 3.5, 'y': 3.5, 'w': 2.0, 'h': 2.0,
          'skind': 'rect', 'sx': 1.0, 'sy': 1.0, 'rot': 0.0}
    gap, _a, _b = H.min_clearance(Pipeline._pad_shape_items(pa),
                                  Pipeline._pad_shape_items(pb))
    assert abs(gap - 2.121) < 0.01, gap
    disc_gap, _a, _b = H.min_clearance(
        [('disc', 0.0, 0.0, 1.0)], [('disc', 3.5, 3.5, 1.0)])
    assert disc_gap > 2.9  # the old model's answer, for the record


def test_creepage_reports_enlarged_pitch():
    a = [('disc', 10.0, 85.0, 1.0)]
    b = [('disc', 160.0, 85.0, 1.0)]
    outline = [(0.0, 0.0), (170.0, 0.0), (170.0, 170.0), (0.0, 170.0)]
    cr, pitch = H.creepage_path(a, b, a + b, outline, [], pitch=0.25)
    # 170x170 mm at 0.25 pitch exceeds the cell cap: pitch grows
    assert pitch > 0.25, pitch
    assert cr is not None and abs(cr - 148.0) < 3.0, cr


def test_creepage_budget_returns_not_verified():
    a = [('disc', 0.0, 0.0, 1.0)]
    b = [('disc', 30.0, 0.0, 1.0)]
    cr, pitch = H.creepage_path(a, b, a + b, [], [], pitch=0.25,
                                budget_s=0.0)
    assert cr is None and pitch == 0.25


def _board(tmp_path, markings):
    import test_engineered_hv as hv
    board = str(tmp_path / 'hv.kicad_pcb')
    hv._build(board, planted=False)
    side = board.replace('.kicad_pcb', '.ai-router-markings.json')
    with open(side, 'w') as f:
        json.dump(markings, f)
    return board


def test_single_declared_domain_checked_against_rest(tmp_path):
    # the fixture board carries copper on HV1 (HV2 is a declared
    # name with no copper anywhere -- used by the mistyped test)
    board = _board(tmp_path, {'version': 1, 'hv_domains': [
        {'name': 'HV', 'nets': ['HV1'], 'voltage_v': 400}]})
    pipe = Pipeline({'strip_existing': False,
                     'tracemaker_path': '/nonexistent'}, board, lambda m: None)
    rep = pipe.tool_hv_domains()
    names = {(p['a'], p['b']) for p in rep['pairs']}
    assert ('HV', '(rest of board)') in names, names
    pair = [p for p in rep['pairs'] if p['b'] == '(rest of board)'][0]
    assert pair['layers'], 'the HV/rest pair evaluated no layer'
    assert rep['status'] in ('ok', 'violations'), rep['status']
    assert rep['missing_nets'] == []


def test_mistyped_net_is_an_error_not_a_pass(tmp_path):
    board = _board(tmp_path, {'version': 1, 'hv_domains': [
        {'name': 'HV', 'nets': ['HV1', 'HV2X'], 'voltage_v': 400},
        {'name': 'LV', 'nets': ['LV1', 'LV2'], 'voltage_v': 0}]})
    pipe = Pipeline({'strip_existing': False,
                     'tracemaker_path': '/nonexistent'}, board, lambda m: None)
    rep = pipe.tool_hv_domains()
    assert rep['status'] == 'error', rep['status']
    assert {'domain': 'HV', 'net': 'HV2X'} in rep['missing_nets']


def test_hvgeom_tessellates_arcs(tmp_path):
    import pcbnew
    from ai_router import worker
    b = pcbnew.BOARD()
    net = pcbnew.NETINFO_ITEM(b, 'A')
    b.Add(net)
    arc = pcbnew.PCB_ARC(b)
    arc.SetLayer(pcbnew.F_Cu)
    arc.SetWidth(pcbnew.FromMM(0.25))
    arc.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(15), pcbnew.FromMM(10)))
    arc.SetMid(pcbnew.VECTOR2I(pcbnew.FromMM(13.54), pcbnew.FromMM(13.54)))
    arc.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(15)))
    arc.SetNet(net)
    b.Add(arc)
    path = str(tmp_path / 'arc.kicad_pcb')
    pcbnew.SaveBoard(path, b)
    facts = worker.cmd_hvgeom(path)
    tracks = facts['nets']['A']['tracks']
    assert len(tracks) >= 4, tracks
    # the tessellation bulges ~1.46 mm beyond the chord x+y=25
    bulge = max((t['x1'] + t['y1']) for t in tracks) - 25.0
    assert bulge > 1.2, bulge
