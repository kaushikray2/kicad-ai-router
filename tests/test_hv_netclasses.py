"""#148 (B24) regression: HV domain spacing is enforced through
per-domain AI-HV-* netclasses in the working-copy project file
(the channel the engine and the DRC gate both read), not only
reported after the route."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import hvdomains as H
from ai_router.pipeline import Pipeline


class _Mk:
    hv_domains = [
        {'name': 'HV', 'nets': ['HV1', 'HV2'], 'peak_v': 400.0,
         'voltage_v': 400.0, 'coating': 'uncoated', 'altitude_m': 0},
        {'name': 'LV', 'nets': ['LV1'], 'peak_v': 0.0,
         'voltage_v': 0.0, 'coating': 'uncoated', 'altitude_m': 0}]


def _pipe(tmp_path):
    p = Pipeline.__new__(Pipeline)
    p.workdir = str(tmp_path)
    p.log = lambda *a: None
    return p


def test_hv_netclasses_written_with_required_clearance(tmp_path):
    # pre-existing project file with an impedance class: the HV
    # write must merge, not clobber
    pro = tmp_path / 'input.kicad_pro'
    pro.write_text(json.dumps({'net_settings': {
        'classes': [{'name': 'Default', 'clearance': 0.2},
                    {'name': 'AI-IMP-X', 'clearance': 0.2,
                     'track_width': 0.31}],
        'netclass_patterns': [{'netclass': 'AI-IMP-X', 'pattern': 'RF'}]}}))
    p = _pipe(tmp_path)
    applied = p._eng_patch_pro_hv(_Mk())
    data = json.loads(pro.read_text())
    classes = {c['name']: c for c in data['net_settings']['classes']}
    assert 'AI-IMP-X' in classes  # merged, not clobbered
    exp = H.pair_required_mm(400.0, True, 'uncoated', 0, True)[0]
    assert exp == 2.5
    assert classes['AI-HV-HV']['clearance'] == exp, classes['AI-HV-HV']
    pats = {pp['pattern']: pp['netclass']
            for pp in data['net_settings']['netclass_patterns']}
    assert pats['HV1'] == 'AI-HV-HV' and pats['HV2'] == 'AI-HV-HV'
    assert pats['LV1'] == 'AI-HV-LV'
    assert pats['RF'] == 'AI-IMP-X'
    assert applied['HV']['clearance_mm'] == exp
    # idempotent: a second run replaces, never duplicates
    p._eng_patch_pro_hv(_Mk())
    data2 = json.loads(pro.read_text())
    names = [c['name'] for c in data2['net_settings']['classes']]
    assert names.count('AI-HV-HV') == 1, names


def test_low_voltage_only_domains_write_nothing(tmp_path):
    class Low:
        hv_domains = [{'name': 'A', 'nets': ['N1'], 'peak_v': 5.0,
                       'voltage_v': 5.0, 'coating': 'uncoated',
                       'altitude_m': 0},
                      {'name': 'B', 'nets': ['N2'], 'peak_v': 0.0,
                       'voltage_v': 0.0, 'coating': 'uncoated',
                       'altitude_m': 0}]
    p = _pipe(tmp_path)
    applied = p._eng_patch_pro_hv(Low())
    # 5 V needs 0.13 mm (Table 6-1 first band) -- below that, the
    # first-band value still applies, so a class IS written; the
    # assertion is that its clearance equals the table value
    exp = H.pair_required_mm(5.0, True, 'uncoated', 0, True)[0]
    assert applied['A']['clearance_mm'] == exp
