# Copyright (c) 2026 SiliconBrane Inc. - see ai_router/__init__.py.
"""Item 8a live acceptance: rfcheck pour gates on a 4-layer board.

In1.Cu carries the GND pour, In2.Cu a +3V3 pour.  The broken
board has a 2.5 mm void in the In1 pour under RF_TX (GATE-1), a
thermal-relief GND pad on RF component U1 (GATE-8) and RF_AUX
routed on B.Cu whose reference pour is +3V3, not ground
(GATE-12).  Healing all three (single In1 zone, full pad
connection, In2 poured with GND) flips every gate to pass."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pcbnew  # noqa: E402

STACKUP = '''(stackup
      (layer "F.SilkS" (type "Top Silk Screen"))
      (layer "F.Paste" (type "Top Solder Paste"))
      (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 0.2) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "In1.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 2" (type "core") (thickness 1.0) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "In2.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 3" (type "core") (thickness 0.2) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
      (layer "B.Paste" (type "Bottom Solder Paste"))
      (layer "B.SilkS" (type "Bottom Silk Screen"))
      (copper_finish "None")
      (dielectric_constraints no)
    )'''


def _nm(v):
    return int(round(v * 1e6))


def V(x, y):
    return pcbnew.VECTOR2I(_nm(x), _nm(y))


def _board(path, healed):
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(4)
    nets = {}
    for nm_ in ('RF_TX', 'RF_AUX', 'GND', '+3V3'):
        n = pcbnew.NETINFO_ITEM(b, nm_)
        b.Add(n)
        nets[nm_] = n

    def zone(layer, net, x0, x1):
        z = pcbnew.ZONE(b)
        ls = pcbnew.LSET(); ls.AddLayer(layer)
        z.SetLayerSet(ls)
        z.SetNetCode(nets[net].GetNetCode())
        out = z.Outline()
        out.NewOutline()
        chain = out.Outline(0)
        for (x, y) in ((x0, 2), (x1, 2), (x1, 58), (x0, 58)):
            chain.Append(_nm(x), _nm(y))
        z.SetLocalClearance(_nm(0.2))
        b.Add(z)

    zone(pcbnew.F_Cu, 'GND', 2, 58)
    if healed:
        zone(pcbnew.In1_Cu, 'GND', 2, 58)
    else:
        zone(pcbnew.In1_Cu, 'GND', 2, 28.75)     # 2.5 mm void
        zone(pcbnew.In1_Cu, 'GND', 31.25, 58)    # under RF_TX
    zone(pcbnew.In2_Cu, 'GND' if healed else '+3V3', 2, 58)

    def smd_fp(ref, pads, layer=pcbnew.F_Cu):
        fp = pcbnew.FOOTPRINT(b)
        fp.SetReference(ref)
        for (num, net, x, y) in pads:
            p = pcbnew.PAD(fp)
            p.SetNumber(num)
            p.SetPosition(V(x, y))
            p.SetSize(V(1.0, 1.0))
            p.SetShape(pcbnew.PAD_SHAPE_RECT)
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            ls = pcbnew.LSET(); ls.AddLayer(layer)
            p.SetLayerSet(ls)
            p.SetNetCode(nets[net].GetNetCode())
            fp.Add(p)
        b.Add(fp)
        return fp

    u1 = smd_fp('U1', [('1', 'RF_TX', 10, 20), ('2', 'GND', 10, 23)])
    smd_fp('J1', [('1', 'RF_TX', 50, 20)])
    smd_fp('U2', [('1', 'RF_AUX', 10, 40), ('2', 'GND', 10, 43)],
           layer=pcbnew.B_Cu)
    smd_fp('J2', [('1', 'RF_AUX', 50, 40)], layer=pcbnew.B_Cu)
    for p in u1.Pads():
        if p.GetNumber() == '2':
            # explicit either way: a zone's default pad connection
            # is thermal relief, so "healed" must say FULL out loud
            p.SetLocalZoneConnection(
                pcbnew.ZONE_CONNECTION_FULL if healed
                else pcbnew.ZONE_CONNECTION_THERMAL)

    def seg(layer, net, x1, y1, x2, y2):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(x1, y1)); t.SetEnd(V(x2, y2))
        t.SetWidth(_nm(0.356)); t.SetLayer(layer)
        t.SetNetCode(nets[net].GetNetCode())
        b.Add(t)

    seg(pcbnew.F_Cu, 'RF_TX', 10, 20, 50, 20)
    seg(pcbnew.B_Cu, 'RF_AUX', 10, 40, 50, 40)
    pcbnew.SaveBoard(path, b)
    src = open(path).read()
    i = src.find('(setup')
    src = src[:i + len('(setup')] + '\n    ' + STACKUP + src[i + len('(setup'):]
    open(path, 'w').write(src)


def _report(tmp, healed):
    # compose exactly as engineered_report does (pipeline spec ->
    # rfgeom worker facts -> rfcheck.evaluate_net per marked net)
    from ai_router import rfcheck
    from ai_router import pipeline as _pmod
    board = os.path.join(tmp, 'pour.kicad_pcb')
    _board(board, healed)
    mk = {'version': 1, 'impedance': [
        {'net': 'RF_TX', 'z0_ohm': 50.0, 'freq_hz': 2.44e9, 'components': ['U1', 'J1']},
        {'net': 'RF_AUX', 'z0_ohm': 50.0, 'freq_hz': 2.44e9, 'components': ['U2', 'J2']}]}
    with open(os.path.join(tmp, 'pour.ai-router-markings.json'), 'w') as fh:
        json.dump(mk, fh)
    pipe = _pmod.Pipeline({'strip_existing': False,
                           'tracemaker_path': '/bin/true'},
                          board, lambda m: None)
    spec, ctxs = pipe._eng_rf_spec(board)
    sp = os.path.join(tmp, 'rfgeom_spec.json')
    with open(sp, 'w') as fh:
        json.dump(spec, fh)
    facts = pipe._worker('rfgeom', '--board', board, '--spec', sp)
    out = {}
    for e in mk['impedance']:
        net = e['net']
        out[net] = {f['rule']: f['severity']
                    for f in rfcheck.evaluate_net(e, ctxs[net],
                                                  facts[net])}
    return out


def test_pour_gates_fail_then_heal(tmp_path):
    tmp = str(tmp_path)
    os.makedirs(os.path.join(tmp, 'b'), exist_ok=True)
    os.makedirs(os.path.join(tmp, 'h'), exist_ok=True)
    broken = _report(os.path.join(tmp, 'b'), False)
    healed = _report(os.path.join(tmp, 'h'), True)
    tx_b, tx_h = broken['RF_TX'], healed['RF_TX']
    aux_b, aux_h = broken['RF_AUX'], healed['RF_AUX']
    assert tx_b.get('GATE-1') == 'fail', tx_b      # 2.5 mm In1 void
    assert tx_b.get('GATE-8') == 'fail', tx_b      # thermal GND pad
    assert aux_b.get('GATE-12') == 'fail', aux_b   # +3V3 reference
    # these gates report only on failure: healed = no finding
    assert tx_h.get('GATE-1') in (None, 'pass'), tx_h
    assert tx_h.get('GATE-8') in (None, 'pass'), tx_h
    assert aux_h.get('GATE-12') in (None, 'pass'), aux_h


if __name__ == '__main__':
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        test_pour_gates_fail_then_heal(t)
    print('rfcheck pour fixture OK')
