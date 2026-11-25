"""#70 integration: IPC-2221B requirements vs measured clearance
and surface creepage on a slotted fixture board, with a planted
violation caught and located."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pcbnew
from ai_router.pipeline import Pipeline

V = pcbnew.VECTOR2I
NM = 1000000


def _build(path, planted=True):
    b = pcbnew.BOARD()
    nets = {}
    for name in ('HV1', 'HV2', 'LV1', 'LV2'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n
    for (x1, y1, x2, y2) in [(2, 2, 70, 2), (70, 2, 70, 38),
                             (70, 38, 2, 38), (2, 38, 2, 2),
                             (28, 12, 32, 12), (32, 12, 32, 28),
                             (32, 28, 28, 28), (28, 28, 28, 12)]:
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
    fp('U1', [('1', 'HV1', 15, 20)])
    fp('U2', [('1', 'LV1', 45, 20)])
    if planted:
        fp('U3', [('1', 'HV2', 15, 34)])
        fp('U4', [('1', 'LV2', 19, 34)])
    txt = open(path.replace('.kicad_pcb', '.kicad_pro'), 'w')
    txt.write('{}\n')
    txt.close()
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
        {'name': 'HV', 'nets': ['HV1', 'HV2'], 'voltage_v': 400},
        {'name': 'LV', 'nets': ['LV1', 'LV2'], 'voltage_v': 0}]}
    with open(path.replace('.kicad_pcb', '.ai-router-markings.json'), 'w') as f:
        json.dump(mk, f)


def test_hv_pair_passes_with_slot_detour(tmp_path):
    board = str(tmp_path / 'hv.kicad_pcb')
    _build(board, planted=False)
    pipe = Pipeline({'strip_existing': False, 'tracemaker_path': '/nonexistent'},
                    board, lambda m: None)
    rep = pipe.tool_hv_domains()
    assert rep and rep['pairs'], rep
    pair = rep['pairs'][0]
    assert pair['delta_v'] == 400
    fc = [e for e in pair['layers'] if e['layer'] == 'F.Cu'][0]
    assert fc['required_mm'] == 2.5 and fc['column'] == 'B2'
    assert abs(fc['clearance_mm'] - 28.0) < 0.01
    # creepage detours around the slot: longer than the air path
    assert fc['creepage_mm'] > fc['clearance_mm'] + 2.0
    assert pair['violations'] == []
    assert rep['assumptions'][0]['voltage_v'] == 400


def test_hv_planted_violation_caught_and_located(tmp_path):
    board = str(tmp_path / 'hv.kicad_pcb')
    _build(board, planted=True)
    pipe = Pipeline({'strip_existing': False, 'tracemaker_path': '/nonexistent'},
                    board, lambda m: None)
    rep = pipe.tool_hv_domains()
    pair = rep['pairs'][0]
    fc = [e for e in pair['layers'] if e['layer'] == 'F.Cu'][0]
    # planted pads: centres 4 mm apart, 2x2 mm -> 2.0 mm gap < 2.5
    assert abs(fc['clearance_mm'] - 2.0) < 0.01
    kinds = {(v['kind'], v['layer']) for v in pair['violations']}
    assert ('clearance', 'F.Cu') in kinds
    v = [v for v in pair['violations'] if v['kind'] == 'clearance'][0]
    assert v['required_mm'] == 2.5 and abs(v['actual_mm'] - 2.0) < 0.01
    assert abs(v['at'][0] - 15.0) < 1.5 and abs(v['at'][1] - 34.0) < 0.6
