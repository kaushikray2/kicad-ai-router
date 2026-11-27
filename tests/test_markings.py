"""#67 Step 1 framework: markings sidecar store (pure python)."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import markings

def _board(tmp):
    p = os.path.join(tmp, 'demo.kicad_pcb')
    open(p, 'w').write('(kicad_pcb)')
    return p

def test_missing_sidecar_is_none():
    with tempfile.TemporaryDirectory() as tmp:
        assert markings.load_markings(_board(tmp)) is None

def test_roundtrip_preserves_all_sections_and_unknowns():
    data = {
        'version': 1, 'board': 'demo.kicad_pcb',
        'impedance': [{'net': 'RF_OUT', 'z0_ohm': 50, 'freq_hz': 2450000000,
                       'dk': 3.66, 'dk_basis': 'design', 'mask': 'covered'}],
        'power': [{'net': 'VBAT', 'note': 'carried for #68'}],
        'length_groups': [{'name': 'g1', 'nets': ['A', 'B'], 'tolerance_mm': 0.5}],
        'hv_domains': [{'name': 'mains', 'nets': ['L', 'N'], 'voltage_v': 230}],
        'quiet': [{'net': 'SENSE', 'future_field': 1}],
        'unknown_top_level': {'kept': True},
    }
    with tempfile.TemporaryDirectory() as tmp:
        board = _board(tmp)
        markings.save_markings(markings.markings_path_for(board), data)
        m = markings.load_markings(board)
        assert m is not None
        assert m.impedance_nets() == ['RF_OUT']
        e = m.impedance_for('RF_OUT')
        assert e['z0_ohm'] == 50.0 and e['freq_hz'] == 2450000000.0
        assert e['dk'] == 3.66 and e['dk_basis'] == 'design'
        assert m.section('power')[0]['note'] == 'carried for #68'
        assert m.section('quiet')[0]['future_field'] == 1
        assert m.data['unknown_top_level'] == {'kept': True}

def test_malformed_sidecar_raises_not_silently_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        board = _board(tmp)
        path = markings.markings_path_for(board)
        open(path, 'w').write('{not json')
        try:
            markings.load_markings(board)
            raise AssertionError('expected MarkingsError')
        except markings.MarkingsError:
            pass

def test_impedance_validation():
    bad = [
        {'impedance': [{'z0_ohm': 50}]},                       # no net
        {'impedance': [{'net': 'X'}]},                         # no z0
        {'impedance': [{'net': 'X', 'z0_ohm': -50}]},          # negative z0
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'dk_basis': 'guess'}]},
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'mask': 'maybe'}]},
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'freq_hz': -1}]},
        {'version': 99},
        {'power': {'not': 'a list'}},
    ]
    for data in bad:
        try:
            markings.validate(data)
            raise AssertionError('expected MarkingsError for %r' % (data,))
        except markings.MarkingsError:
            pass
    ok = markings.validate({'impedance': [{'net': 'A', 'z0_ohm': 75}]})
    assert ok['impedance'][0]['z0_ohm'] == 75.0
    for name in markings.SECTIONS:
        assert ok.get(name, []) == [] or name == 'impedance'

def test_impedance_step2_fields():
    ok = markings.validate({'impedance': [{
        'net': 'ANT', 'z0_ohm': 50, 'freq_hz': 2.44e9, 'role': 'tx',
        'loss_budget_db': 0.4, 'fence': True, 'smooth': False,
        'pi': {'radio': ['L1', 'C1', 'C2'], 'antenna': ['L9'],
               'radio_ref': 'U1'}}]})
    e = ok['impedance'][0]
    assert e['role'] == 'tx' and e['loss_budget_db'] == 0.4
    assert e['pi']['radio'] == ['L1', 'C1', 'C2'] and e['pi']['radio_ref'] == 'U1'
    bad = [
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'role': 'loud'}]},
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'loss_budget_db': -0.5}]},
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'pi': ['L1']}]},
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'pi': {'middle': ['L1']}}]},
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'pi': {'radio': 'L1'}}]},
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'fence': 'yes'}]},
    ]
    for data in bad:
        try:
            markings.validate(data)
            raise AssertionError('expected MarkingsError for %r' % (data,))
        except markings.MarkingsError:
            pass


def test_differential_section():
    ok = markings.validate({'version': 1, 'differential': [
        {'name': 'USB', 'nets': ['USB_P', 'USB_N'], 'zdiff_ohm': 90}]})
    assert ok['differential'][0]['zdiff_ohm'] == 90.0
    assert markings.Markings(ok).differential_legs() == ['USB_P', 'USB_N']
    for bad in ({'name': 'X', 'nets': ['A'], 'zdiff_ohm': 90},
                {'name': 'X', 'nets': ['A', 'A'], 'zdiff_ohm': 90},
                {'name': 'X', 'nets': ['A', 'B'], 'zdiff_ohm': -1},
                {'nets': ['A', 'B'], 'zdiff_ohm': 90},
                {'name': 'X', 'nets': ['A', 'B'], 'zdiff_ohm': 90, 'gap_mm': 0}):
        try:
            markings.validate({'version': 1, 'differential': [bad]})
        except markings.MarkingsError:
            continue
        raise AssertionError('accepted bad differential entry: %r' % (bad,))


def test_power_section_validation():
    ok = markings.validate({'version': 1, 'power': [{
        'net': 'V5', 'voltage': {'nominal_v': 5},
        'loads': {'U1': {'peak_a': 1.0}}, 'regulators': {'U2': {'type': 'ldo'}}}]})
    e = ok['power'][0]
    assert e['voltage'] == {'nominal_v': 5.0, 'min_v': 5.0, 'max_v': 5.0}
    assert e['loads']['U1']['sustained_a'] == 1.0  # conservative default
    for bad in ({'net': 'V5', 'voltage': {'nominal_v': 5, 'min_v': 6}},
                {'net': 'V5', 'loads': {'U1': {'peak_a': -1}}},
                {'net': 'V5', 'loads': {'U1': {'peak_a': 1, 'kind': 'magic'}}},
                {'net': 'V5', 'regulators': {'U2': {'type': 'buck'}}},
                {'net': 'V5', 'protective': {'F1': {}}},
                {'loads': {}}):
        try:
            markings.validate({'version': 1, 'power': [bad]})
        except markings.MarkingsError:
            continue
        raise AssertionError('accepted bad power entry: %r' % (bad,))


def test_impedance_model_and_gap_validation():
    # #216: the schema validator itself checks model/gap_mm --
    # a hand-written sidecar must fail at validate(), not at
    # pipeline run time (the editor half was #201).
    bad = [
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'model': 'waveguide'}]},
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'model': 'coplanar', 'gap_mm': 0}]},
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'model': 'coplanar', 'gap_mm': -0.2}]},
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'gap_mm': 'wide'}]},
        {'impedance': [{'net': 'X', 'z0_ohm': 50, 'model': 'coplanar'}]},
    ]
    for data in bad:
        try:
            markings.validate(data)
            raise AssertionError('expected MarkingsError for %r' % (data,))
        except markings.MarkingsError:
            pass
    ok = markings.validate({'impedance': [
        {'net': 'A', 'z0_ohm': 50, 'model': 'coplanar', 'gap_mm': 0.15},
        {'net': 'B', 'z0_ohm': 50, 'model': 'microstrip'},
        {'net': 'C', 'z0_ohm': 50, 'model': 'stripline', 'gap_mm': 0.3}]})
    assert ok['impedance'][0]['gap_mm'] == 0.15
    assert ok['impedance'][2]['gap_mm'] == 0.3
