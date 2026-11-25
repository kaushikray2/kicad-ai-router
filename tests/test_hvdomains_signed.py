"""Regression tests for #129 (third review B3): HV domain voltages
are PEAK and SIGNED.  Validation used to require >= 0, so bipolar
rails entered as magnitudes (+15/+15) checked as 0 V apart -- 'same
declared potential; no spacing requirement' -- where the real
difference is 30 V (and +400/-400 is 800 V, 4.0 mm under B2)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from ai_router import hvdomains as H
from ai_router import markings
from ai_router.markings import MarkingsError


def _dom(name, v, **kw):
    e = {'name': name, 'nets': [name], 'peak_v': v}
    e.update(kw)
    return e


def test_bipolar_rails_validate_and_differ_by_30v():
    out = markings.validate({'version': 1, 'hv_domains': [
        _dom('P15', 15), _dom('N15', -15)]})
    a, b = out['hv_domains']
    assert a['peak_v'] == 15.0 and b['peak_v'] == -15.0
    dv = abs(a['peak_v'] - b['peak_v'])
    assert dv == 30.0
    assert H.required_spacing_mm(dv, 'B2') == 0.10


def test_plus_minus_400_is_800v_and_4mm_under_b2():
    out = markings.validate({'version': 1, 'hv_domains': [
        _dom('P400', 400), _dom('N400', -400)]})
    a, b = out['hv_domains']
    dv = abs(a['peak_v'] - b['peak_v'])
    assert dv == 800.0
    assert abs(H.required_spacing_mm(dv, 'B2') - 4.0) < 1e-9


def test_legacy_voltage_v_still_validates_and_normalises():
    out = markings.validate({'version': 1, 'hv_domains': [
        {'name': 'HV', 'nets': ['HV'], 'voltage_v': 100}]})
    e = out['hv_domains'][0]
    assert e['peak_v'] == 100.0 and e['voltage_v'] == 100.0


def test_missing_voltage_still_rejected():
    with pytest.raises(MarkingsError):
        markings.validate({'version': 1, 'hv_domains': [
            {'name': 'HV', 'nets': ['HV']}]})


def test_ac_flag_validates_as_boolean():
    out = markings.validate({'version': 1, 'hv_domains': [
        _dom('MAINS', 325, ac=True)]})
    assert out['hv_domains'][0]['ac'] is True
    with pytest.raises(MarkingsError):
        markings.validate({'version': 1, 'hv_domains': [
            _dom('MAINS', 325, ac='yes')]})
