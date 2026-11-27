# Copyright (c) 2026 SiliconBrane Inc. - see ai_router/__init__.py.
"""Item 8b live acceptance: a three-edge power chain.

VIN_12V -> F1 (protective, rating 3.0 A) -> VIN_FUSED -> R_SNS
(conduit, 20 mohm, rating 4.0 A) -> VIN_BUCK -> U_BUCK (buck,
eta 0.85) -> +3V3 -> U_LOAD (2.0 A).  The buck's reflected
input is 3.3*2.0/(0.85*12) = 0.65 A, so the 3.0 A protective
floor -- not the load -- sizes VIN_12V and the F1-adjacent
VIN_FUSED segments, while +3V3 sizes for the 2.0 A load.  A
second buck feeds +1V8 with a declared-UNKNOWN load: that rail
stays resistances-only in the same run."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


STACKUP = '''(stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (copper_finish "None") (edge_connector "no"))'''

NETLIST = '''(export (version E)
 (components
  (comp (ref J1) (value IN) (libsource (lib Fixture) (part CONN)) (sheetpath (names "/") (tstamps "x")))
  (comp (ref F1) (value FUSE) (libsource (lib Fixture) (part FUSE)) (sheetpath (names "/") (tstamps "x")))
  (comp (ref R_SNS) (value SENSE) (libsource (lib Fixture) (part SENSE)) (sheetpath (names "/") (tstamps "x")))
  (comp (ref U_BUCK) (value BUCK) (libsource (lib Fixture) (part BUCK)) (sheetpath (names "/") (tstamps "x")))
  (comp (ref U_BUCK2) (value BUCK) (libsource (lib Fixture) (part BUCK)) (sheetpath (names "/") (tstamps "x")))
  (comp (ref U_LOAD) (value LOAD) (libsource (lib Fixture) (part LOAD)) (sheetpath (names "/") (tstamps "x")))
  (comp (ref U_LOAD2) (value LOAD) (libsource (lib Fixture) (part LOAD)) (sheetpath (names "/") (tstamps "x"))))
 (libparts
  (libpart (lib Fixture) (part CONN) (pins (pin (num "1") (name o) (type power_out)) (pin (num "2") (name g) (type power_in))))
  (libpart (lib Fixture) (part FUSE) (pins (pin (num "1") (name i) (type power_in)) (pin (num "2") (name o) (type power_out))))
  (libpart (lib Fixture) (part SENSE) (pins (pin (num "1") (name i) (type power_in)) (pin (num "2") (name o) (type power_out))))
  (libpart (lib Fixture) (part BUCK) (pins (pin (num "1") (name i) (type power_in)) (pin (num "2") (name o) (type power_out)) (pin (num "3") (name g) (type power_in))))
  (libpart (lib Fixture) (part LOAD) (pins (pin (num "1") (name v) (type power_in)) (pin (num "2") (name g) (type power_in)))))
 (nets
  (net (code 1) (name "VIN_12V") (node (ref J1) (pin "1")) (node (ref F1) (pin "1")))
  (net (code 2) (name "VIN_FUSED") (node (ref F1) (pin "2")) (node (ref R_SNS) (pin "1")))
  (net (code 3) (name "VIN_BUCK") (node (ref R_SNS) (pin "2")) (node (ref U_BUCK) (pin "1")) (node (ref U_BUCK2) (pin "1")))
  (net (code 4) (name "+3V3") (node (ref U_BUCK) (pin "2")) (node (ref U_LOAD) (pin "1")))
  (net (code 5) (name "+1V8") (node (ref U_BUCK2) (pin "2")) (node (ref U_LOAD2) (pin "1")))
  (net (code 6) (name "GND") (node (ref J1) (pin "2")) (node (ref U_BUCK) (pin "3")) (node (ref U_BUCK2) (pin "3")) (node (ref U_LOAD) (pin "2")) (node (ref U_LOAD2) (pin "2")))))'''

V12 = {'nominal_v': 12, 'min_v': 12, 'max_v': 12}


def _nm(x):
    return int(round(x * 1e6))


def _build_board(path):
    import pcbnew

    def V(x, y):
        return pcbnew.VECTOR2I(_nm(x), _nm(y))
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(2)
    nets = {}
    for name in ('GND', 'VIN_12V', 'VIN_FUSED', 'VIN_BUCK', '+3V3', '+1V8'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n
    z = pcbnew.ZONE(b)
    ls = pcbnew.LSET(); ls.AddLayer(pcbnew.B_Cu)
    z.SetLayerSet(ls)
    z.SetNetCode(nets['GND'].GetNetCode())
    z.SetLocalClearance(_nm(0.2))
    out = z.Outline(); out.NewOutline()
    chain = out.Outline(0)
    for (x, y) in [(2, 2), (78, 2), (78, 44), (2, 44)]:
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
    smd_fp('J1', [('1', 'VIN_12V', 5, 20), ('2', 'GND', 5, 24)])
    smd_fp('F1', [('1', 'VIN_12V', 15, 20), ('2', 'VIN_FUSED', 20, 20)])
    smd_fp('R_SNS', [('1', 'VIN_FUSED', 30, 20), ('2', 'VIN_BUCK', 35, 20)])
    smd_fp('U_BUCK', [('1', 'VIN_BUCK', 45, 20), ('2', '+3V3', 50, 20),
                      ('3', 'GND', 45, 24)])
    smd_fp('U_BUCK2', [('1', 'VIN_BUCK', 45, 32), ('2', '+1V8', 50, 32),
                       ('3', 'GND', 45, 36)])
    smd_fp('U_LOAD', [('1', '+3V3', 70, 20), ('2', 'GND', 70, 24)])
    smd_fp('U_LOAD2', [('1', '+1V8', 70, 32), ('2', 'GND', 70, 36)])

    def seg(net, x1, y1, x2, y2):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(x1, y1)); t.SetEnd(V(x2, y2))
        t.SetWidth(_nm(0.3)); t.SetLayer(pcbnew.F_Cu)
        t.SetNetCode(nets[net].GetNetCode())
        b.Add(t)
    seg('VIN_12V', 5, 20, 15, 20)
    seg('VIN_FUSED', 20, 20, 30, 20)
    seg('VIN_BUCK', 35, 20, 45, 20)
    seg('VIN_BUCK', 40, 20, 40, 32)      # branch down to U_BUCK2
    seg('VIN_BUCK', 40, 32, 45, 32)
    seg('+3V3', 50, 20, 70, 20)
    seg('+1V8', 50, 32, 70, 32)
    pcbnew.SaveBoard(path, b)
    src = open(path).read()
    i = src.find('(setup')
    src = src[:i + len('(setup')] + '\n    ' + STACKUP + src[i + len('(setup'):]
    open(path, 'w').write(src)


def test_power_chain_three_edge_types(tmp_path):
    if not os.path.exists('/usr/lib/python3/dist-packages/pcbnew.py'):
        return
    from pathlib import Path
    tmp_path = Path(tmp_path)
    board = str(tmp_path / 'chain.kicad_pcb')
    _build_board(board)
    open(str(tmp_path / 'chain.net'), 'w').write(NETLIST)
    from ai_router import markings
    markings.save_markings(markings.markings_path_for(board), {
        'version': 1,
        'power': [
            {'net': 'VIN_12V', 'voltage': V12, 'root': {'ref': 'J1'}},
            {'net': 'VIN_FUSED', 'voltage': V12,
             'protective': {'F1': {'rating_a': 3.0}}},
            {'net': 'VIN_BUCK', 'voltage': V12,
             'conduits': {'R_SNS': {'r_mohm': 20, 'rating_a': 4.0}}},
            {'net': '+3V3',
             'voltage': {'nominal_v': 3.3, 'min_v': 3.3, 'max_v': 3.3},
             'regulators': {'U_BUCK': {'type': 'buck', 'eta_min': 0.85}},
             'loads': {'U_LOAD': {'peak_a': 2.0, 'sustained_a': 2.0}}},
            {'net': '+1V8',
             'voltage': {'nominal_v': 1.8, 'min_v': 1.8, 'max_v': 1.8},
             'regulators': {'U_BUCK2': {'type': 'buck', 'eta_min': 0.85}},
             'loads': {'U_LOAD2': {'unknown': True}}}],
        'power_corners': {'delta_t_c': 10, 'drop_budget_v': 0.5,
                          'decoupling_budget_mm': 5}})
    from ai_router.pipeline import Pipeline
    pipe = Pipeline({'strip_existing': False, 'tracemaker_path': '/nonexistent'},
                    board, lambda m: None)
    r = pipe.tool_power()
    assert r['status'] == 'ok', r
    plan = pipe._eng_power_plan
    rails = plan['rails']
    # the load rail designs for the load itself
    assert rails['+3V3']['design']['peak_a']['design_a'] == 2.0
    assert rails['+3V3']['complete'] is True
    seg3 = rails['+3V3']['segments'][0]
    assert seg3['i_peak_a'] == 2.0 and seg3['required_width_mm'] > 0.9, seg3
    # the protective floor (3.0 A) beats the buck's reflected
    # input (0.65 A) on the input rail...
    assert rails['VIN_12V']['design']['peak_a']['design_a'] == 3.0, \
        rails['VIN_12V']['design']
    segv = rails['VIN_12V']['segments'][0]
    assert segv['i_peak_a'] == 3.0 and segv['required_width_mm'] > 1.2, segv
    # ...and on the segments incident to F1's downstream pins
    segf = rails['VIN_FUSED']['segments'][0]
    assert segf['i_peak_a'] == 3.0, segf
    # the conduit passes the reflected demand through (no floor)
    assert abs(rails['VIN_BUCK']['design']['peak_a']['design_a']
               - 3.3 * 2.0 / (0.85 * 12)) < 0.01, rails['VIN_BUCK']['design']
    # widening lands for the floored and loaded rails
    assert 'VIN_12V' in plan['widen'] and '+3V3' in plan['widen'], plan['widen']
    assert r['applied']['widen'].get('adopted') is True, r['applied']
    # the UNKNOWN rail stays resistances-only in the same run
    r18 = rails['+1V8']
    assert r18['complete'] is False, r18.get('complete')
    assert '+1V8' not in plan['widen'], plan['widen']
    rep = pipe.engineered_report(pipe.current)
    assert rep['power']['rails']['+1V8']['complete'] is False
    assert rep['power']['rails']['+3V3']['complete'] is True


if __name__ == '__main__':
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        test_power_chain_three_edge_types(t)
    print('power chain fixture OK')
