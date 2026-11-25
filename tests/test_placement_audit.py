"""#42: placement audit + library-rule materialisation (pure layer)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import library_rules, placement_audit

MOD = '(footprint "X" (property "AIRuleEdgeClearanceMm" "5") (zone (net 0) (layers "F.Cu") (keepout (tracks not_allowed) (vias not_allowed) (copperpour not_allowed)) (polygon (pts (xy 0 0) (xy 10 0) (xy 10 8) (xy 0 8)))))'


def _fp(ref, fpid, x, y, pads, courtyard=None, zones=None, props=None, value=''):
    return {'ref': ref, 'fpid': fpid, 'value': value, 'x': x, 'y': y, 'rot': 0,
            'pads': [{'num': n, 'net': net, 'x': px, 'y': py} for (n, net, px, py) in pads],
            'courtyard': courtyard, 'zones': zones or [], 'properties': props or {}}


def test_library_parse_and_classify():
    r = library_rules.parse_footprint_rules(MOD)
    assert r['properties'] == {'AIRuleEdgeClearanceMm': '5'}
    assert r['zones'][0]['keepout'] == {'tracks': True, 'vias': True, 'copperpour': True}
    assert library_rules.classify('Mod:GNSS_Module', '') == 'gnss_module'
    assert library_rules.classify('Xtal:Crystal_SMD_3215', '') == 'crystal'
    assert library_rules.classify('Misc:Resistor', '10k') is None


def _facts():
    return {'outline_segments': [[2, 2, 58, 2], [58, 2, 58, 38], [58, 38, 2, 38], [2, 38, 2, 2]],
            'footprints': [
                _fp('U1', 'Mod:GNSS_Module', 30, 20,
                    [('1', 'RF_OUT', 31, 21), ('2', 'GND', 31, 23)],
                    courtyard=[29, 19, 41, 29]),
                _fp('U2', 'Misc:MCU', 35, 24, [('1', 'V5', 35, 24)]),
                _fp('U3', 'Misc:Radio', 10, 10, [('1', 'V5', 10, 10)],
                    props={'AIRuleDecapMm': '3'}),
                _fp('C1', 'Misc:Cap', 20, 10, [('1', 'V5', 20, 10), ('2', 'GND', 20, 12)], value='100n'),
                _fp('U4', 'Mod:GNSS_Module', 4, 10, [('1', 'RF2', 5, 11)],
                    courtyard=[3.5, 9, 12, 15], props={'AIRuleEdgeClearanceMm': '5'}),
                _fp('Y1', 'Xtal:Crystal_SMD', 45, 30, [('1', 'XTAL', 45, 30)]),
            ]}


def test_audit_findings():
    lib = {'Mod:GNSS_Module': library_rules.parse_footprint_rules(MOD),
           'Xtal:Crystal_SMD': {'properties': {}, 'zones': []}}
    rep = placement_audit.audit(_facts(), lib)
    # U1's library zone is missing on the board copy -> materialise,
    # absolute outline at the footprint position
    assert len(rep['materialise']) == 2  # U1 and U4 share the footprint
    m = [e for e in rep['materialise'] if e['ref'] == 'U1'][0]
    assert m['ref'] == 'U1' and m['outline'][0] == [30.0, 20.0] and m['outline'][2] == [40.0, 28.0]
    assert m['provenance'] == 'library:Mod:GNSS_Module'
    # U2's pad sits inside U1's (materialised) keepout -> intrusion
    kinds = [(c['kind'], c.get('intruder')) for c in rep['footprints']['U1']['checks']]
    assert ('keepout_intrusion', 'U2') in kinds
    # U3's nearest cap is 10 mm away against a 3 mm budget
    dec = [c for c in rep['footprints']['U3']['checks'] if c['kind'] == 'decoupling'][0]
    assert dec['ok'] is False and dec['nearest_cap'] == 'C1' and dec['distance_mm'] == 10.0
    # U4 hugs the board edge: 1.5 mm against a 5 mm budget
    edge = [c for c in rep['footprints']['U4']['checks'] if c['kind'] == 'edge_clearance'][0]
    assert edge['ok'] is False and edge['distance_mm'] == 1.5
    # crystal with a rule-less library footprint -> library gap
    assert rep['gaps'] == [{'ref': 'Y1', 'fpid': 'Xtal:Crystal_SMD', 'class': 'crystal'}]
    # RF-class nets join first_nets advice; GND does not
    assert 'RF_OUT' in rep['first_nets_extra'] and 'GND' not in rep['first_nets_extra']


def test_unresolved_library_is_not_a_gap():
    facts = _facts()
    rep = placement_audit.audit(facts, {})  # nothing resolved
    assert rep['gaps'] == []
    assert 'Y1' in rep['unresolved']


def test_generic_module_is_not_rf_class():
    # #77: the bare substring 'module' classified ANY module-named
    # footprint as an RF module.
    assert library_rules.classify('Sensors:Humidity_Module', 'Generic sensor module') is None
    assert library_rules.classify('RF:E22-900M22S', 'LoRa module') == 'rf_module'
    assert library_rules.classify('Wireless:ESP32-WROOM', '') == 'rf_module'

def test_pour_nets_never_join_first_nets():
    # #77: supply rails / pour nets must stay out of first_nets_extra
    # even on a genuine RF module; its signal nets keep the advice.
    facts = {'outline_segments': [], 'pour_nets': ['3V3', '+5V'],
             'footprints': [_fp('U2', 'RF:E22-900M22S', 10, 10,
                                [('1', '3V3', 10, 10), ('2', 'TX', 11, 10),
                                 ('3', 'RX', 12, 10), ('4', 'GND', 13, 10)],
                                value='LoRa module')]}
    rep = placement_audit.audit(facts, {})
    assert rep['first_nets_extra'] == ['TX', 'RX'], rep['first_nets_extra']
