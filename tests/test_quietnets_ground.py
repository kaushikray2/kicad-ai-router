"""Regression tests for #147 (third review B22): a component quiet
entry used to make EVERY net on the part -- ground and supply
included -- quiet board-wide, and ground was the literal name
'GND'.  Now: only signal nets are quiet, only near the part;
ground is a set (common names + pour nets + per-entry ground_nets);
and 'no load capacitors found' is a finding, not a pass."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import quietnets as Q

LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
ENTRY = {'ref': 'X1', 'profile': 'crystal'}


def _pads(gnd='GND', extra=()):
    pads = [
        {'ref': 'X1', 'net': 'XTAL1', 'x': 18.0, 'y': 20.0, 'w': 1.6, 'h': 1.6, 'layers': ['F.Cu']},
        {'ref': 'X1', 'net': 'XTAL2', 'x': 22.0, 'y': 20.0, 'w': 1.6, 'h': 1.6, 'layers': ['F.Cu']},
        {'ref': 'X1', 'net': gnd, 'x': 20.0, 'y': 23.0, 'w': 1.6, 'h': 1.6, 'layers': ['F.Cu']},
        {'ref': 'C1', 'net': 'XTAL1', 'x': 14.0, 'y': 19.0, 'w': 1.0, 'h': 1.0, 'layers': ['F.Cu']},
        {'ref': 'C1', 'net': gnd, 'x': 14.0, 'y': 21.5, 'w': 1.0, 'h': 1.0, 'layers': ['F.Cu']},
        {'ref': 'C2', 'net': 'XTAL2', 'x': 26.0, 'y': 19.0, 'w': 1.0, 'h': 1.0, 'layers': ['F.Cu']},
        {'ref': 'C2', 'net': gnd, 'x': 26.0, 'y': 21.5, 'w': 1.0, 'h': 1.0, 'layers': ['F.Cu']},
    ]
    return pads + list(extra)


def _board(gnd='GND', far_copper=False, caps=True, power_nets=()):
    items = {('XTAL1', 'F.Cu'): [('seg', 14.0, 20.0, 18.0, 20.0, 0.15)],
             ('XTAL2', 'F.Cu'): [('seg', 22.0, 20.0, 26.0, 20.0, 0.15)]}
    if far_copper:
        # ground copper 40 mm away with a foreign track beside it:
        # the reviewer's reproduction (SPI clock flagged as an
        # intrusion into the crystal's 'quiet' ground)
        items[(gnd, 'F.Cu')] = [('seg', 55.0, 60.0, 75.0, 60.0, 0.15)]
        items[('SPI_CLK', 'F.Cu')] = [('seg', 55.0, 60.6, 75.0, 60.6, 0.15)]
    pads = _pads(gnd) if caps else [p for p in _pads(gnd) if p['ref'] == 'X1']
    return {'layers': LAYERS, 'items': items, 'pads': pads,
            'vias': [{'net': gnd, 'x': 14.0, 'y': 23.0, 'd': 0.6},
                     {'net': gnd, 'x': 26.0, 'y': 23.0, 'd': 0.6}],
            'zones': {(gnd, 'In1.Cu'): [[(0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0)]]},
            'power_nets': list(power_nets)}


def test_ground_copper_is_not_quiet():
    rep = Q.evaluate(ENTRY, _board(far_copper=True))
    assert rep['own_nets'] == ['XTAL1', 'XTAL2'], rep['own_nets']
    assert rep['intrusions'] == [], rep['intrusions']
    assert rep['pass'], rep


def test_agnd_board_finds_caps_and_passes():
    rep = Q.evaluate(ENTRY, _board(gnd='AGND', far_copper=True))
    assert {c['ref'] for c in rep['load_caps']} == {'C1', 'C2'}, rep['load_caps']
    assert all(c['ground_return'] for c in rep['load_caps'])
    assert rep['intrusions'] == [] and rep['pass'], rep


def test_no_load_caps_is_a_finding_not_a_pass():
    rep = Q.evaluate(ENTRY, _board(caps=False))
    assert rep['load_caps'] == []
    assert any(f['kind'] == 'no_load_caps_found' for f in rep['findings']), rep['findings']
    assert not rep['pass']


def test_supply_net_on_the_part_is_not_quiet():
    extra = [{'ref': 'X1', 'net': '+3V3', 'x': 24.0, 'y': 23.0, 'w': 1.6, 'h': 1.6, 'layers': ['F.Cu']}]
    b = _board()
    b['pads'] = _pads('GND', extra)
    b['power_nets'] = ['+3V3']
    rep = Q.evaluate(ENTRY, b)
    assert '+3V3' not in rep['own_nets'], rep['own_nets']
    assert rep['pass'], rep
