# Copyright (c) 2026 SiliconBrane Inc. - see ai_router/__init__.py.
"""Item 8c live acceptance: the upgraded meander tunes a pair
into tolerance with zero added DRC faults, and the report row
grades the POST-meander delta (#217 behaviour).

Board: the test_engineered_length pair (DP 50 mm, DN 40 mm,
group tolerance 0.5 mm).  KiCad DRC (kicad-cli, which refills
zones as part of the run; this board carries none) is compared
before and after tool_length_match: identical violation and
unconnected counts.  The markings report row for the group must
read PASS with the tuned delta."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

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




def _drc(board, out_json):
    subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json',
                    '--output', out_json, board],
                   capture_output=True, text=True, timeout=600)
    rep = json.load(open(out_json))
    return (len(rep.get('violations') or []),
            len(rep.get('unconnected_items') or []))


def test_meander_acceptance_drc_and_report(tmp_path):
    if not os.path.exists('/usr/lib/python3/dist-packages/pcbnew.py'):
        return
    tmp = str(tmp_path)
    board = os.path.join(tmp, 'lm.kicad_pcb')
    _build(board)
    before = _drc(board, os.path.join(tmp, 'drc_in.json'))
    pipe = _pipe(board)
    r = pipe.tool_length_match()
    assert r['status'] == 'ok', r
    g = pipe._eng_length_plan['groups'][0]
    after_m = g['after']['DN']
    assert after_m['within_tolerance'] is True, after_m
    assert g['members']['DN']['meander'].get('adopted') is True
    after = _drc(pipe.current, os.path.join(tmp, 'drc_out.json'))
    assert after == before, (before, after)   # 0 added DRC faults
    from ai_router.markings_model import summarize_report
    rep = pipe.engineered_report(pipe.current)
    _s = summarize_report(rep)
    rows = _s['rows'] if isinstance(_s, dict) else _s
    text = ' | '.join(('%s %s %s' % (r.get('marking', ''), r.get('verdict', ''), r.get('detail', ''))) if isinstance(r, dict) else str(r) for r in rows)
    assert 'PASS' in text and 'tuned' in text, text


if __name__ == '__main__':
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        test_meander_acceptance_drc_and_report(t)
    print('meander acceptance OK')
