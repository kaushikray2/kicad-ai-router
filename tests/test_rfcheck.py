"""#67 Step 2: pure rule evaluation (rfcheck) -- annex GATE/WARN rules
against synthetic geometry facts, no pcbnew needed."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import rfcheck

def _seg(x1, y1, x2, y2, w=0.35, layer='F.Cu'):
    import math
    return {'layer': layer, 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'w': w,
            'len': math.hypot(x2 - x1, y2 - y1)}

def _pad(ref, num, x, y, layers=('F.Cu',), thru=False):
    return {'ref': ref, 'num': num, 'x': x, 'y': y, 'layers': list(layers), 'thru': thru}

def _facts(**kw):
    f = {'segments': [], 'arcs': [], 'vias': [], 'pads': [], 'return_vias': [],
         'corridor': {}, 'foreign_under': [], 'floating_islands': [],
         'keepout_intrusions': [], 'gnd_pad_connections': [], 'pi_presence': {}}
    f.update(kw)
    return f

def _rules(findings):
    return {(f['rule'], f['severity']) for f in findings}

# A straight 20 mm feed, pad to pad, no findings expected.
BASE_SEGS = [_seg(0, 0, 20, 0)]
BASE_PADS = [_pad('U1', '1', 0, 0), _pad('J1', '1', 20, 0)]
BASE_CTX = {'band': 'MID', 'freq_hz': 2.44e9, 'stub_limit_mm': 2.75,
            'return_ceiling_mm': 2.54, 'width_mm': 0.35,
            'w_over_lambda_g': 0.005, 'est_loss_db': 0.1, 'length_mm': 20.0,
            'refs': {'F.Cu': {'ref_layer': 'In1.Cu', 'pour_nets': ['GND']}}}

def test_clean_feed_has_no_findings():
    out = rfcheck.evaluate_net({}, BASE_CTX, _facts(segments=BASE_SEGS, pads=BASE_PADS))
    assert out == [], out

def test_stub_over_limit_fails_gate3():
    segs = BASE_SEGS + [_seg(10, 0, 10, 4.0)]   # 4 mm dangling stub
    out = rfcheck.evaluate_net({}, BASE_CTX, _facts(segments=segs, pads=BASE_PADS))
    assert ('GATE-3', 'fail') in _rules(out), out
    short = BASE_SEGS + [_seg(10, 0, 10, 2.0)]  # 2 mm < 2.75 limit
    out = rfcheck.evaluate_net({}, BASE_CTX, _facts(segments=short, pads=BASE_PADS))
    assert ('GATE-3', 'fail') not in _rules(out), out

def test_tx_second_harmonic_warn8():
    ctx = dict(BASE_CTX, band='HIGH', stub_limit_mm=1.4)
    segs = BASE_SEGS + [_seg(10, 0, 10, 1.0)]   # between limit/2 and limit
    out = rfcheck.evaluate_net({'role': 'tx'}, ctx, _facts(segments=segs, pads=BASE_PADS))
    assert ('WARN-8', 'warn') in _rules(out), out
    out = rfcheck.evaluate_net({'role': 'rx'}, ctx, _facts(segments=segs, pads=BASE_PADS))
    assert ('WARN-8', 'warn') not in _rules(out), out

def test_acute_angle_and_width_step_fail_gate10():
    # 60 deg interior joint (the path folds back on itself)
    segs = [_seg(0, 0, 10, 0), _seg(10, 0, 5, 8.66)]
    pads = [_pad('U1', '1', 0, 0), _pad('J1', '1', 5, 8.66)]
    out = rfcheck.evaluate_net({}, BASE_CTX, _facts(segments=segs, pads=pads))
    assert ('GATE-10', 'fail') in _rules(out), out
    # width step
    segs = [_seg(0, 0, 10, 0), _seg(10, 0, 20, 0, w=0.20)]
    out = rfcheck.evaluate_net({}, BASE_CTX, _facts(segments=segs, pads=BASE_PADS))
    assert any(f['rule'] == 'GATE-10' and 'width' in f['detail'] for f in out), out

def test_right_corner_warn5_only_past_onset():
    segs = [_seg(0, 0, 10, 0), _seg(10, 0, 10, 10)]
    pads = [_pad('U1', '1', 0, 0), _pad('J1', '1', 10, 10)]
    ctx = dict(BASE_CTX, w_over_lambda_g=0.08)
    out = rfcheck.evaluate_net({}, ctx, _facts(segments=segs, pads=pads))
    assert ('WARN-5', 'warn') in _rules(out), out
    assert ('GATE-10', 'fail') not in _rules(out), out  # square corners never fail
    ctx2 = dict(BASE_CTX, w_over_lambda_g=0.01)
    out = rfcheck.evaluate_net({}, ctx2, _facts(segments=segs, pads=pads))
    assert ('WARN-5', 'warn') not in _rules(out), out

def test_return_via_distance_gates():
    vias = [{'x': 10.0, 'y': 0.0}]
    segs = [_seg(0, 0, 10, 0, layer='F.Cu'), _seg(10, 0, 20, 0, layer='B.Cu')]
    pads = [_pad('U1', '1', 0, 0), _pad('J1', '1', 20, 0, layers=('B.Cu',))]
    far = _facts(segments=segs, pads=pads, vias=vias,
                 return_vias=[{'nearest_mm': 3.1, 'within': 0}])
    out = rfcheck.evaluate_net({}, BASE_CTX, far)
    assert ('GATE-5', 'fail') in _rules(out), out
    none = _facts(segments=segs, pads=pads, vias=vias,
                  return_vias=[{'nearest_mm': None, 'within': 0}])
    out = rfcheck.evaluate_net({}, BASE_CTX, none)
    assert ('GATE-4', 'fail') in _rules(out), out
    near = _facts(segments=segs, pads=pads, vias=vias,
                  return_vias=[{'nearest_mm': 1.2, 'within': 1}])
    out = rfcheck.evaluate_net({}, BASE_CTX, near)
    assert ('GATE-4', 'fail') not in _rules(out) and ('GATE-5', 'fail') not in _rules(out), out

def test_corridor_severance_gate1():
    cor = {'F.Cu': {'severed': [{'x': 5.0, 'y': 0.0, 'len_mm': 1.1}], 'centre_gaps': []}}
    out = rfcheck.evaluate_net({}, BASE_CTX, _facts(segments=BASE_SEGS, pads=BASE_PADS, corridor=cor))
    assert ('GATE-1', 'fail') in _rules(out), out
    cor = {'F.Cu': {'severed': [], 'centre_gaps': [{'x': 5.0, 'y': 0.0, 'len_mm': 1.2}]}}
    out = rfcheck.evaluate_net({}, BASE_CTX, _facts(segments=BASE_SEGS, pads=BASE_PADS, corridor=cor))
    assert ('GATE-1', 'warn') in _rules(out) and ('GATE-1', 'fail') not in _rules(out), out
    cor = {'F.Cu': {'severed': [], 'centre_gaps': [{'x': 5.0, 'y': 0.0, 'len_mm': 3.5}]}}
    out = rfcheck.evaluate_net({}, BASE_CTX, _facts(segments=BASE_SEGS, pads=BASE_PADS, corridor=cor))
    assert ('GATE-1', 'fail') in _rules(out), out

def test_foreign_floating_keepout_thermal():
    f = _facts(segments=BASE_SEGS, pads=BASE_PADS,
               foreign_under=[{'net': 'SPI_CLK', 'layer': 'In1.Cu', 'kind': 'track', 'x': 5, 'y': 0}],
               floating_islands=[{'net': '', 'layer': 'F.Cu', 'x': 3, 'y': 1}],
               keepout_intrusions=[{'net': 'LED', 'kind': 'track', 'x': 19, 'y': 1, 'footprint': 'J1'}],
               gnd_pad_connections=[{'ref': 'U1', 'num': '2', 'connection': 'thermal'}])
    out = rfcheck.evaluate_net({}, BASE_CTX, f)
    r = _rules(out)
    assert ('GATE-11', 'fail') in r and ('GATE-6', 'fail') in r, out
    assert ('GATE-2', 'fail') in r and ('GATE-8', 'fail') in r, out

def test_gate12_reference_must_be_ground():
    ctx = dict(BASE_CTX, refs={'F.Cu': {'ref_layer': 'In1.Cu', 'pour_nets': ['+3V3']}})
    out = rfcheck.evaluate_net({}, ctx, _facts(segments=BASE_SEGS, pads=BASE_PADS))
    assert ('GATE-12', 'fail') in _rules(out), out
    ctx = dict(BASE_CTX, refs={'F.Cu': {'ref_layer': 'In1.Cu', 'pour_nets': []}})
    out = rfcheck.evaluate_net({}, ctx, _facts(segments=BASE_SEGS, pads=BASE_PADS))
    assert ('GATE-12', 'fail') in _rules(out), out

def test_gate9_mask_mismatch():
    ctx = dict(BASE_CTX, mask={'intent': 'open', 'correction_applied': True, 'h_mm': 0.2})
    out = rfcheck.evaluate_net({}, ctx, _facts(segments=BASE_SEGS, pads=BASE_PADS))
    assert ('GATE-9', 'fail') in _rules(out), out
    ctx = dict(BASE_CTX, mask={'intent': 'covered', 'correction_applied': False, 'h_mm': 0.2})
    out = rfcheck.evaluate_net({}, ctx, _facts(segments=BASE_SEGS, pads=BASE_PADS))
    assert ('GATE-9', 'fail') in _rules(out), out
    ctx = dict(BASE_CTX, mask={'intent': None, 'correction_applied': True, 'h_mm': 0.2})
    out = rfcheck.evaluate_net({}, ctx, _facts(segments=BASE_SEGS, pads=BASE_PADS))
    assert ('GATE-9', 'fail') not in _rules(out), out

def test_pi_presence_and_segment_gate7():
    marking = {'pi': {'radio': ['L1'], 'antenna': ['L2'], 'radio_ref': 'U1'}}
    segs = [_seg(0, 0, 8, 0), _seg(8, 0, 20, 0)]
    pads = [_pad('U1', '1', 0, 0), _pad('L1', '1', 8, 0), _pad('L1', '2', 8, 0),
            _pad('L2', '1', 14, 0), _pad('J1', '1', 20, 0)]
    pres = {'L1': {'exists': True, 'on_net': True}, 'L2': {'exists': True, 'on_net': True}}
    out = rfcheck.evaluate_net(marking, BASE_CTX, _facts(segments=segs, pads=pads, pi_presence=pres))
    assert ('GATE-7', 'fail') not in _rules(out), out
    pres2 = {'L1': {'exists': False, 'on_net': False}, 'L2': {'exists': True, 'on_net': True}}
    out = rfcheck.evaluate_net(marking, BASE_CTX, _facts(segments=segs, pads=pads, pi_presence=pres2))
    assert ('GATE-7', 'fail') in _rules(out), out

def test_loss_and_enig_warnings():
    ctx = dict(BASE_CTX, est_loss_db=0.35)
    out = rfcheck.evaluate_net({}, ctx, _facts(segments=BASE_SEGS, pads=BASE_PADS))
    assert ('WARN-1', 'warn') in _rules(out), out
    ctx = dict(BASE_CTX, est_loss_db=0.25)
    out = rfcheck.evaluate_net({'role': 'gnss_lna'}, ctx, _facts(segments=BASE_SEGS, pads=BASE_PADS))
    assert ('WARN-1', 'warn') in _rules(out), out
    ctx = dict(BASE_CTX, est_loss_db=0.7)
    out = rfcheck.evaluate_net({}, ctx, _facts(segments=BASE_SEGS, pads=BASE_PADS))
    assert any(f['rule'] == 'WARN-1' and 'budget' in f['detail'] for f in out), out
    ctx = dict(BASE_CTX, finish='ENIG', length_mm=30.0)
    out = rfcheck.evaluate_net({}, ctx, _facts(segments=BASE_SEGS, pads=BASE_PADS))
    assert ('WARN-2', 'warn') in _rules(out), out
    ctx = dict(BASE_CTX, finish='ENIG', band='SUB', length_mm=30.0)
    out = rfcheck.evaluate_net({}, ctx, _facts(segments=BASE_SEGS, pads=BASE_PADS))
    assert ('WARN-2', 'warn') not in _rules(out), out

def test_unrouted_net_is_na():
    out = rfcheck.evaluate_net({}, BASE_CTX, _facts())
    assert len(out) == 1 and out[0]['severity'] == 'na', out


def test_isolated_fragment_reported_once():
    # #95: a two-segment isolated fragment has two dangling ends;
    # stubs() must report it once, not once per end.
    segs = [{'layer': 'F.Cu', 'x1': 0, 'y1': 0, 'x2': 5, 'y2': 0, 'w': 0.3, 'len': 5.0},
            {'layer': 'F.Cu', 'x1': 5, 'y1': 0, 'x2': 5, 'y2': 5, 'w': 0.3, 'len': 5.0}]
    g = rfcheck.PathGraph(segs, [], [], [])
    st = g.stubs()
    assert len(st) == 1, st
    assert st[0]['kind'] == 'isolated' and abs(st[0]['len'] - 10.0) < 1e-9
