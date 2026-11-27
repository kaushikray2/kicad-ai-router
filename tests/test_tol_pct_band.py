# Copyright (C) 2026 SiliconBrane Inc.
#
# This file is part of KiCad AI Router.
#
# KiCad AI Router is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# KiCad AI Router is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with KiCad AI Router. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""#67 tol_pct wiring: the field was declared in the markings
schema but validated nowhere and consumed nowhere (#201 carried
it verbatim).  Now: the validator range-checks it (0, 50]; the
editor model parses it; and rfcheck GATE-13 grades every routed
layer's width against z0 * (1 +/- tol_pct/100) through the
pipeline's own per-layer stackup model.  GATE-10 stays a pure
geometric width check -- the transition case below passes it
(same width both layers) while failing the band on the inner
layer, which is exactly the gap GATE-13 closes."""
import math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import markings, markings_model, rfcheck, tlcalc


def _doc(tol):
    e = {'net': 'RF', 'z0_ohm': 50}
    if tol is not None:
        e['tol_pct'] = tol
    return {'version': 1, 'impedance': [e]}


def test_tol_pct_validation():
    for bad in (0, -5, 60, 'wide'):
        try:
            markings.validate(_doc(bad))
        except markings.MarkingsError:
            pass
        else:
            raise AssertionError('tol_pct %r accepted' % (bad,))
    for good in (0.5, 5, 10, 50):
        out = markings.validate(_doc(good))
        assert out['impedance'][0]['tol_pct'] == float(good)
    out = markings.validate(_doc(None))
    assert 'tol_pct' not in out['impedance'][0]
    assert markings.DEFAULT_TOL_PCT == 10.0


def test_tol_pct_model_roundtrip():
    e = markings_model.impedance_entry({'net': 'RF', 'z0': '50',
                                        'tol_pct': '7.5'})
    assert e['tol_pct'] == 7.5
    assert markings_model.impedance_form(e)['tol_pct'] == 7.5
    e2 = markings_model.impedance_entry({'net': 'RF', 'z0': '50'})
    assert 'tol_pct' not in e2


def _seg(x1, y1, x2, y2, w, layer):
    return {'layer': layer, 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
            'w': w, 'len': math.hypot(x2 - x1, y2 - y1)}


def _facts(**kw):
    f = {'segments': [], 'arcs': [], 'vias': [], 'pads': [],
         'return_vias': [], 'corridor': {}, 'foreign_under': [],
         'floating_islands': [], 'keepout_intrusions': [],
         'gnd_pad_connections': [], 'pi_presence': {}}
    f.update(kw)
    return f


_MS = lambda w: tlcalc.microstrip(w, 0.2, 0.035, 4.5)
_SL = lambda w: tlcalc.stripline(w, 0.435, 0.035, 4.5, a=0.2)
W50 = tlcalc.solve_width(lambda w: _MS(w).z0, 50.0)   # 0.3555 mm


def _transition_ctx(**over):
    ctx = {'band': 'MID', 'freq_hz': 2.44e9, 'stub_limit_mm': 2.75,
           'return_ceiling_mm': 2.54, 'width_mm': W50,
           'w_over_lambda_g': 0.005, 'est_loss_db': 0.1,
           'length_mm': 20.0,
           'refs': {'F.Cu': {'ref_layer': 'In1.Cu', 'pour_nets': ['GND']},
                    'In1.Cu': {'ref_layer': 'F.Cu', 'pour_nets': ['GND']}},
           'z0_ohm': 50.0, 'tol_pct': 10.0,
           'layer_z0': {'F.Cu': _MS, 'In1.Cu': _SL}}
    ctx.update(over)
    return ctx


def _transition_facts():
    segs = [_seg(0, 0, 10, 0, W50, 'F.Cu'),
            _seg(10, 0, 20, 0, W50, 'In1.Cu')]
    pads = [{'ref': 'U1', 'num': '1', 'x': 0, 'y': 0,
             'layers': ['F.Cu'], 'thru': False},
            {'ref': 'J1', 'num': '1', 'x': 20, 'y': 0,
             'layers': ['In1.Cu'], 'thru': False}]
    return _facts(segments=segs, pads=pads,
                  vias=[{'x': 10.0, 'y': 0.0}],
                  return_vias=[{'nearest_mm': 0.5, 'within': 1}])


def test_gate13_layer_transition_band():
    out = rfcheck.evaluate_net({}, _transition_ctx(), _transition_facts())
    # same width on both layers: the geometric width check passes
    assert not any(f['rule'] == 'GATE-10' for f in out), out
    band = [f for f in out if f['rule'] == 'GATE-13']
    assert len(band) == 1, out
    assert band[0]['severity'] == 'fail'
    assert 'In1.Cu' in band[0]['detail'], band[0]
    assert '30.7' in band[0]['detail'], band[0]   # stripline Z0 there
    # a +/-50 % band covers 30.7 ohm: no finding
    out = rfcheck.evaluate_net({}, _transition_ctx(tol_pct=50.0),
                               _transition_facts())
    assert not any(f['rule'] == 'GATE-13' for f in out), out
    # no band context (the pre-wiring shape): nothing reported
    ctx = _transition_ctx()
    del ctx['layer_z0'], ctx['z0_ohm'], ctx['tol_pct']
    out = rfcheck.evaluate_net({}, ctx, _transition_facts())
    assert not any(f['rule'] == 'GATE-13' for f in out), out


if __name__ == '__main__':
    test_tol_pct_validation()
    test_tol_pct_model_roundtrip()
    test_gate13_layer_transition_band()
    print('tol_pct band OK')
