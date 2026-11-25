"""#145 (B20, verified rows) regressions: ground net resolved
from the markings/pours (not hardcoded 'GND'), a neighbouring
signal layer is not a reference plane, one unsolvable coplanar
geometry fails only its own net, unknown/>6 GHz bands are
refused, the loss budget reaches rfcheck, the tool's own ground
vias are not 'foreign', and NaN targets never validate."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import markings
from ai_router.pipeline import Pipeline


def _su(pour):
    return {'copper': [
        {'name': 'F.Cu', 'class': 'surface_top', 'thickness_mm': 0.035,
         'diel_below': {'thickness_mm': 0.2, 'er': 4.2,
                        'loss_tangent': 0.02}, 'pour_nets': []},
        {'name': 'In1.Cu', 'class': 'inner', 'thickness_mm': 0.035,
         'diel_above': {'thickness_mm': 0.2, 'er': 4.2},
         'diel_below': {'thickness_mm': 1.0, 'er': 4.2},
         'pour_nets': pour},
        {'name': 'B.Cu', 'class': 'surface_bottom', 'thickness_mm': 0.035,
         'diel_above': {'thickness_mm': 1.0, 'er': 4.2}, 'pour_nets': []}],
        'nets': {'RF1': {'track_layers': {'F.Cu': {}}, 'length_mm': 12.0}},
        'copper_finish': None}


def _pipe(su, entry):
    p = Pipeline.__new__(Pipeline)
    p.log = lambda *a: None
    p._eng = {'entries': {'RF1': entry},
              'nets': {'RF1': {'primary_layer': 'F.Cu', 'width_mm': 0.3,
                               'per_layer': {'F.Cu': {'eps_eff': 3.5,
                                                      'loss_db_per_mm': 0.01}}}}}
    p._worker = lambda *a, **k: su
    return p


_ENTRY = {'net': 'RF1', 'z0_ohm': 50, 'freq_hz': 2.4e9,
          'loss_budget_db': 1.25}


def test_ground_net_from_pour_and_loss_budget_passed():
    spec, ctxs = _pipe(_su(['AGND']), dict(_ENTRY))._eng_rf_spec('b.kicad_pcb')
    assert spec['nets']['RF1']['gnd_net'] == 'AGND'
    assert ctxs['RF1']['gnd_net'] == 'AGND'
    assert ctxs['RF1']['refs']['F.Cu']['ref_layer'] == 'In1.Cu'
    assert ctxs['RF1']['loss_budget_db'] == 1.25


def test_signal_neighbour_is_not_a_reference_plane():
    spec, ctxs = _pipe(_su([]), dict(_ENTRY))._eng_rf_spec('b.kicad_pcb')
    assert spec['nets']['RF1']['refs'].get('F.Cu') in (None, [])
    assert ctxs['RF1']['refs'] == {}


def test_above_6ghz_is_refused_not_sized_at_3ghz():
    e = dict(_ENTRY); e['freq_hz'] = 9.0e9
    spec, ctxs = _pipe(_su(['AGND']), e)._eng_rf_spec('b.kicad_pcb')
    assert 'RF1' not in spec['nets']
    assert 'RF1' in spec['refused']


def test_coplanar_impossible_gap_fails_only_its_net():
    p = Pipeline.__new__(Pipeline)
    p.log = lambda *a: None
    entry = {'z0_ohm': 50, 'freq_hz': 1.0e9, 'model': 'coplanar',
             'gap_mm': 0.1}
    cu = {'class': 'surface_top', 'thickness_mm': 0.035,
          'diel_below': {'thickness_mm': 0.2, 'er': 4.2,
                         'loss_tangent': 0.0}}
    r = p._eng_layer_width(entry, cu, 2)
    assert 'error' in r and 'unsolvable' in r['error'], r


def test_nan_target_never_validates(tmp_path):
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('(kicad_pcb)')
    side = tmp_path / 'b.ai-router-markings.json'
    side.write_text('{"version": 1, "impedance": '
                    '[{"net": "RF1", "z0_ohm": NaN}]}')
    try:
        markings.load_markings(str(board))
    except markings.MarkingsError:
        return
    raise AssertionError('NaN z0_ohm validated')


def _nm(x):
    return int(round(x * 1e6))


def test_gate11_exempts_own_ground_vias(tmp_path):
    import pcbnew
    from ai_router import worker
    b = pcbnew.BOARD(); b.SetCopperLayerCount(2)
    nets = {}
    for name in ('RF', 'GND', 'SIG'):
        nets[name] = pcbnew.NETINFO_ITEM(b, name); b.Add(nets[name])
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(_nm(10), _nm(10)))
    t.SetEnd(pcbnew.VECTOR2I(_nm(40), _nm(10)))
    t.SetWidth(_nm(0.3)); t.SetLayer(pcbnew.F_Cu)
    t.SetNetCode(nets['RF'].GetNetCode()); b.Add(t)
    for name, y in (('GND', 12), ('SIG', 8)):
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(pcbnew.VECTOR2I(_nm(25), _nm(y)))
        v.SetWidth(_nm(0.6)); v.SetDrill(_nm(0.3))
        v.SetNetCode(nets[name].GetNetCode()); b.Add(v)
    path = str(tmp_path / 'g.kicad_pcb')
    pcbnew.SaveBoard(path, b)
    spec = {'nets': {'RF': {'gnd_net': 'GND', 'ceiling_mm': 2.54,
                            'refs': {'F.Cu': [{'ref_layer': 'B.Cu',
                                               'h_mm': 1.5}]}}}}
    out = worker.cmd_rfgeom(path, spec)
    foreign = out['RF']['foreign_under']
    assert not any(f['net'] == 'GND' for f in foreign), foreign
    assert any(f['net'] == 'SIG' and f['kind'] == 'via' for f in foreign), foreign
