"""#68 Step 2 integration: power markings + sibling netlist on a
synthetic 2-layer board.  V5 feeds U2 (2 A) over a 40 mm, 0.3 mm
track on a GND-plane board; at a 10 C budget the segment must be
widened to ~1 mm by the gated post-pass, the report must carry
the design currents, the plane-return assumption and the
decoupling checklist (C1 sits 2.83 mm from U2's supply pin)."""
import json, os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest

STACKUP = '''(stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (copper_finish "None") (edge_connector "no"))'''

NETLIST = '''(export (version E)
 (components
  (comp (ref U1) (value REG) (libsource (lib Fixture) (part REG)) (sheetpath (names "/") (tstamps "x")))
  (comp (ref U2) (value MCU) (libsource (lib Fixture) (part MCU)) (sheetpath (names "/") (tstamps "x")))
  (comp (ref C1) (value 100n) (libsource (lib Fixture) (part CAP)) (sheetpath (names "/") (tstamps "x"))))
 (libparts
  (libpart (lib Fixture) (part REG) (pins (pin (num "1") (name g) (type power_in)) (pin (num "2") (name o) (type power_out))))
  (libpart (lib Fixture) (part MCU) (pins (pin (num "1") (name v) (type power_in)) (pin (num "2") (name g) (type power_in))))
  (libpart (lib Fixture) (part CAP) (pins (pin (num "1") (name a) (type passive)) (pin (num "2") (name b) (type passive)))))
 (nets
  (net (code 1) (name "V5") (node (ref U1) (pin "2")) (node (ref U2) (pin "1")) (node (ref C1) (pin "1")))
  (net (code 2) (name "GND") (node (ref U1) (pin "1")) (node (ref U2) (pin "2")) (node (ref C1) (pin "2")))))'''


def _nm(x):
    return int(round(x * 1e6))


def _build_board(path):
    import pcbnew
    def V(x, y):
        return pcbnew.VECTOR2I(_nm(x), _nm(y))
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(2)
    nets = {}
    for name in ('GND', 'V5'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n
    for (x1, y1, x2, y2) in [(2, 2, 58, 2), (58, 2, 58, 38), (58, 38, 2, 38), (2, 38, 2, 2)]:
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(x1, y1)); s.SetEnd(V(x2, y2))
        s.SetLayer(pcbnew.Edge_Cuts)
        b.Add(s)
    z = pcbnew.ZONE(b)
    ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu)
    z.SetLayerSet(ls)
    z.SetNetCode(nets['GND'].GetNetCode())
    z.SetLocalClearance(_nm(0.2))
    out = z.Outline(); out.NewOutline()
    chain = out.Outline(0)
    for (x, y) in [(2, 2), (58, 2), (58, 38), (2, 38)]:
        chain.Append(_nm(x), _nm(y))
    b.Add(z)
    def smd_fp(ref, pads):
        fp = pcbnew.FOOTPRINT(b)
        fp.SetReference(ref)
        for (num, net, x, y) in pads:
            p = pcbnew.PAD(fp)
            p.SetNumber(num); p.SetPosition(V(x, y))
            p.SetSize(V(1.0, 1.0))
            p.SetShape(pcbnew.PAD_SHAPE_RECT)
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            ls2 = pcbnew.LSET(); ls2.AddLayer(pcbnew.F_Cu)
            p.SetLayerSet(ls2)
            p.SetNetCode(nets[net].GetNetCode())
            fp.Add(p)
        b.Add(fp)
    smd_fp('U1', [('1', 'GND', 10, 24), ('2', 'V5', 10, 20)])
    smd_fp('U2', [('1', 'V5', 50, 20), ('2', 'GND', 50, 24)])
    smd_fp('C1', [('1', 'V5', 48, 22), ('2', 'GND', 48, 24)])
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(V(10, 20)); t.SetEnd(V(50, 20))
    t.SetWidth(_nm(0.3)); t.SetLayer(pcbnew.F_Cu)
    t.SetNetCode(nets['V5'].GetNetCode())
    b.Add(t)
    pcbnew.SaveBoard(path, b)
    src = open(path).read()
    i = src.find('(setup')
    src = src[:i + len('(setup')] + '\n    ' + STACKUP + src[i + len('(setup'):]
    open(path, 'w').write(src)


def _worker(board, cmd, *args):
    w = os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py')
    r = subprocess.run([sys.executable, w, cmd, '--board', board] + list(args),
                       capture_output=True, text=True, timeout=300)
    lines = [l for l in r.stdout.splitlines() if '{' in l]
    assert lines, (r.stdout[-500:], r.stderr[-500:])
    return json.loads(lines[-1][lines[-1].index('{'):])


@pytest.mark.skipif(not os.path.exists('/usr/lib/python3/dist-packages/pcbnew.py')
                    and not os.path.exists('/usr/lib/python3/dist-packages/pcbnew'),
                    reason='pcbnew unavailable')
def test_power_stage_end_to_end(tmp_path):
    board = str(tmp_path / 'pw.kicad_pcb')
    _build_board(board)
    open(str(tmp_path / 'pw.net'), 'w').write(NETLIST)
    from ai_router import markings
    markings.save_markings(markings.markings_path_for(board), {
        'version': 1,
        'power': [{'net': 'V5',
                   'voltage': {'nominal_v': 5, 'min_v': 4.75, 'max_v': 5.25},
                   'loads': {'U2': {'peak_a': 2.0, 'sustained_a': 2.0}}}],
        'power_corners': {'delta_t_c': 10, 'drop_budget_v': 0.5,
                          'decoupling_budget_mm': 5}})
    from ai_router.pipeline import Pipeline
    logs = []
    pipe = Pipeline({'strip_existing': False, 'tracemaker_path': '/nonexistent'},
                    board, logs.append)
    r = pipe.tool_power()
    assert r['status'] == 'ok', r
    plan = pipe._eng_power_plan
    rail = plan['rails']['V5']
    assert rail['design']['peak_a']['design_a'] == 2.0
    seg = rail['segments'][0]
    assert seg['i_peak_a'] == 2.0 and seg['required_width_mm'] > 0.9, seg
    assert seg['ok'] is False and 'V5' in plan['widen']
    assert r['applied']['widen'].get('adopted') is True, r['applied']
    facts = _worker(board if not os.path.exists(pipe.current) else pipe.current,
                    'powergeom', '--nets', 'V5')
    w = facts['V5']['tracks'][0]['w']
    assert w > 0.9, w  # the widening actually landed on the working board
    drops = {d['ref']: d for d in rail['loop_drops']}
    assert drops['U2']['return'] == 'plane' and drops['U2']['ok'] is True
    assert any('solid-plane return' in a for a in plan['assumptions'])
    dec = {d['ref']: d for d in rail['decoupling']}
    assert dec['U2']['nearest_cap'] == 'C1' and dec['U2']['distance_mm'] == 2.83
    assert dec['U2']['within_budget'] is True
    rep = pipe.engineered_report(pipe.current)
    assert rep['power']['rails']['V5']['complete'] is True
