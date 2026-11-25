"""Regression test for #143 (third review B17): _stackup_entries read
a multi-ply dielectric as only one ply (the addsublayer atoms were
ignored), so a two-ply 0.14 mm dielectric came back as 0.07 mm and a
50 ohm line was solved at 71 ohm.  Plies must be combined: thickness
sums, epsilon_r is the series mean sum(t)/sum(t/er)."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
try:
    import worker as w
except Exception:
    w = None

HDR = '(kicad_pcb (version 20240108) (generator "test")\n'

TWO_PLY = HDR + '''  (stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "prepreg") (thickness 0.07) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02)
      (addsublayer (thickness 0.07) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02)))
    (layer "B.Cu" (type "copper") (thickness 0.035))
    (copper_finish "None")))
'''

MIXED_PLY = HDR + '''  (stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.1) (material "FR4") (epsilon_r 4.0) (loss_tangent 0.01)
      (addsublayer (thickness 0.05) (material "FR4") (epsilon_r 5.0) (loss_tangent 0.03)))
    (layer "B.Cu" (type "copper") (thickness 0.035))
    (copper_finish "None")))
'''

ONE_PLY = HDR + '''  (stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.2) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
    (layer "B.Cu" (type "copper") (thickness 0.035))
    (copper_finish "None")))
'''


def _diel(text):
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 't.kicad_pcb')
        with open(p, 'w') as f:
            f.write(text)
        entries = w._stackup_entries(p)
    return [e for e in entries if e['type'] != 'copper'][0]


def test_two_ply_thickness_sums():
    if w is None: print('skip'); return
    e = _diel(TWO_PLY)
    assert abs(e['thickness_mm'] - 0.14) < 1e-9, e
    assert abs(e['er'] - 4.5) < 1e-9, e


def test_mixed_ply_series_mean_er():
    if w is None: print('skip'); return
    e = _diel(MIXED_PLY)
    assert abs(e['thickness_mm'] - 0.15) < 1e-9, e
    expect = 0.15 / (0.1 / 4.0 + 0.05 / 5.0)
    assert abs(e['er'] - expect) < 1e-6, (e, expect)
    # thickness-weighted loss tangent: (0.1*0.01 + 0.05*0.03) / 0.15
    assert abs(e['loss_tangent'] - (0.0025 / 0.15)) < 1e-9, e


def test_single_ply_unchanged():
    if w is None: print('skip'); return
    e = _diel(ONE_PLY)
    assert abs(e['thickness_mm'] - 0.2) < 1e-9, e
    assert abs(e['er'] - 4.4) < 1e-9, e
    assert abs(e['loss_tangent'] - 0.02) < 1e-9, e
