"""#67 Step 1: transmission-line calculator fixtures.

Fixture provenance (no invented numbers):
* Saturn PCB Toolkit help worked example, differential microstrip
  (W=10, S=5, H=15, T=2.10 mil, er=4.6) -- the IPC-2141A/AN-905 form
  reproduces it to 0.2%.
* Cohn stripline reference points evaluated independently by AGM
  (pcb-toolkit crate test suite): exact for the conformal-mapping form.
* Cohn coupled-stripline even/odd values, independently evaluated in
  the pcb-toolkit crate test suite (Cohn 1955 eq. 18/20/22).
* Conductor-backed CPW: independent AGM evaluation of the Ghione &
  Naldi model (pcb-toolkit crate test suite).
* Microstrip single-ended: KiCad's calculator (the acceptance
  reference) uses the qucs filling-factor variant of Hammerstad &
  Jensen; the crate's classic Wheeler/Hammerstad variant differs by
  up to ~2% on thick strips.  Those checks are labelled CROSS-FORM
  and given cross-form tolerances -- they are agreement checks between
  two published variants, not exactness claims.
"""
import math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import tlcalc as T

MIL = 0.0254

def _rel(a, b):
    return abs(a - b) / abs(b)

# --- exact-model fixtures ---------------------------------------------------

def test_cohn_stripline_reference_points():
    # AGM-exact Cohn points (er=1): W/B = 1, 0.1, 10.
    assert _rel(T._cohn_z0_symmetric(10.0, 10.0, 0.0, 1.0), 65.3989) < 1e-4
    assert _rel(T._cohn_z0_symmetric(1.0, 10.0, 0.0, 1.0), 194.361) < 1e-4
    assert _rel(T._cohn_z0_symmetric(100.0, 10.0, 0.0, 1.0), 9.0265) < 1e-4

def test_diff_stripline_cohn_modes():
    thin = T.diff_stripline(10.0, 5.0, 21.4, 0.0, 4.6)
    assert _rel(thin.zeven, 56.153) < 1e-4
    assert _rel(thin.zodd, 39.686) < 1e-4
    thick = T.diff_stripline(10.0, 5.0, 21.4, 1.4, 4.6)
    assert _rel(thick.zeven, 49.525) < 1e-3
    assert _rel(thick.zodd, 33.565) < 1e-3
    assert _rel(thick.z0, 42.423) < 1e-3
    assert _rel(thick.zdiff, 2.0 * thick.zodd) < 1e-12

def test_diff_microstrip_saturn_example():
    r = T.diff_microstrip(10.0, 5.0, 15.0, 2.10, 4.6)
    assert _rel(r.z0, 77.504) < 0.002
    assert _rel(r.zodd, 50.490) < 0.002
    assert _rel(r.zeven, 118.971) < 0.002
    assert _rel(r.zdiff, 100.979) < 0.002

def test_coplanar_backed_agm_reference():
    r = T.coplanar(10.0, 5.0, 5.0, 0.0, 4.6)
    assert _rel(r.z0, 46.0957448) < 0.005      # KiCad ellipk vs AGM evaluation
    assert _rel(r.eps_eff, 3.3419380) < 1e-4

# --- cross-formulation agreement (labelled, loose tolerances) ---------------

def test_microstrip_cross_form_hj():
    # KiCad (qucs) form vs the classic Hammerstad/Wheeler variant's
    # published evaluation (crate): T=0 agrees to ~0.5%, thick to ~2.5%.
    assert _rel(T.microstrip(10.0, 5.0, 0.0, 4.6).z0, 47.911) < 0.005
    assert _rel(T.microstrip(5.0, 5.0, 0.0, 2.2).z0, 95.265) < 0.005
    assert _rel(T.microstrip(10.0, 5.0, 1.4, 4.6).z0, 44.832) < 0.025

def test_stripline_kicad_vs_cohn():
    for (w, b, t, er) in [(10.0, 40.0, 1.4, 4.6), (10.0, 21.4, 1.4, 4.6),
                          (15.0, 30.0, 0.0, 10.2)]:
        assert _rel(T.stripline(w, b, t, er).z0,
                    T._cohn_z0_symmetric(w, b, t, er)) < 0.015

def test_embedded_cross_form():
    r = T.embedded_microstrip(10.0, 5.0, 1.4, 1.0, 4.6)
    assert _rel(r.z0, 42.196) < 0.02           # crate H-J variant evaluation
    assert _rel(r.eps_eff, 3.7336) < 0.03

# --- exact properties --------------------------------------------------------

def test_embedded_limits():
    s = T.microstrip(10.0, 5.0, 1.4, 4.6)
    e0 = T.embedded_microstrip(10.0, 5.0, 1.4, 0.0, 4.6)
    assert _rel(e0.z0, s.z0) < 1e-12 and _rel(e0.eps_eff, s.eps_eff) < 1e-12
    deep = T.embedded_microstrip(10.0, 5.0, 1.4, 250.0, 4.6)
    assert _rel(deep.eps_eff, 4.6) < 1e-6      # homogeneous limit

def test_dispersion_low_frequency_limit():
    dc = T.microstrip(10.0, 10.0, 1.4, 4.6)
    lf = T.microstrip(10.0, 10.0, 1.4, 4.6, f_hz=1e6)
    assert _rel(lf.z0, dc.z0) < 1e-4
    rf = T.microstrip(10.0, 10.0, 1.4, 4.6, f_hz=10e9)
    assert rf.eps_eff > dc.eps_eff and rf.eps_eff < 4.6

def test_solve_width_roundtrip():
    for model_fn, target in [
            (lambda w: T.microstrip(w, 0.2, 0.035, 4.4).z0, 50.0),
            (lambda w: T.microstrip(w, 0.2, 0.035, 4.4).z0, 75.0),
            (lambda w: T.stripline(w, 0.4, 0.035, 4.4).z0, 50.0),
            (lambda w: T.coplanar(w, 0.15, 0.2, 0.035, 4.4).z0, 50.0)]:
        w = T.solve_width(model_fn, target)
        assert w is not None
        assert _rel(model_fn(w), target) < 0.002
    assert T.solve_width(lambda w: T.microstrip(w, 0.2, 0.035, 4.4).z0, 500.0) is None

def test_mask_correction_annex_range():
    # Annex CALC-4 modelled point: flooded mask on h=0.21 mm moves a
    # 50 ohm line by about -1.3 ohm; typical range -1..-3 ohm.  This is a
    # range sanity check against the annex, not a fixture.
    bare = T.microstrip(0.38, 0.21, 0.035, 4.4)
    coated = T.microstrip(0.38, 0.21, 0.035, 4.4, mask=(0.02, 3.5))
    assert 'mask_correction_applied' in coated.flags
    assert -3.5 < (coated.z0 - bare.z0) < -0.3
    thick = T.microstrip(0.38, 1.0, 0.035, 4.4, mask=(0.02, 3.5))
    assert 'mask_correction_off_thick_dielectric' in thick.flags
    assert thick.z0 == T.microstrip(0.38, 1.0, 0.035, 4.4).z0

def test_loss_in_annex_band_range():
    # Annex CALC-5: standard FR-4 microstrip loss at 2.4 GHz (MID) is
    # ~0.08-0.12 dB/cm.  Range check, not a fixture.
    r = T.microstrip(0.4, 0.2, 0.035, 4.4, f_hz=2.4e9, tand=0.02)
    assert 0.008 < r.loss_db_per_mm < 0.012

def test_lambda_g_annex_table():
    # Annex reference table (FR-4 microstrip): lambda_g ~69 mm at 2.4 GHz.
    r = T.microstrip(0.4, 0.2, 0.035, 4.4, f_hz=2.4e9)
    assert _rel(r.lambda_g_mm(2.4e9), 69.0) < 0.03

def test_bands_and_validity_flags():
    assert T.band_of(868e6) == 'SUB'
    assert T.band_of(2.45e9) == 'MID'
    assert T.band_of(5.8e9) == 'HIGH'
    assert T.band_of(24e9) == 'ABOVE_6GHZ'
    assert T.band_of(0) is None
    out = T.diff_microstrip(30.0, 5.0, 15.0, 2.10, 4.6)   # W/H = 2 boundary
    assert out.in_range is False and 'outside_an905_validity' in out.flags
    far = T.microstrip(10.0, 10.0, 1.4, 4.6, f_hz=10e9)    # H/lambda0 = 0.33
    assert far.in_range is False

def test_mils_scale_invariance():
    a = T.microstrip(10.0, 5.0, 1.4, 4.6)
    b = T.microstrip(10.0 * MIL, 5.0 * MIL, 1.4 * MIL, 4.6)
    assert _rel(a.z0, b.z0) < 1e-9


def test_solve_diff_geometry_round_trips():
    d = lambda w, s: T.diff_microstrip(w, s, 0.2, 0.035, 4.4)
    sg = lambda w: T.microstrip(w, 0.2, 0.035, 4.4)
    for kw in ({}, {'gap_mm': 0.15}, {'width_mm': 0.3}):
        r = T.solve_diff_geometry(d, sg, 90.0, **kw)
        assert 'error' not in r, r
        back = d(r['width_mm'], r['gap_mm'])
        assert abs(back.zdiff - 90.0) < 0.1, (kw, r, back.zdiff)
    both = T.solve_diff_geometry(d, sg, 90.0, gap_mm=0.15, width_mm=0.3)
    assert both['mode'] == 'as_declared' and abs(both['zdiff'] - 81.3) < 0.2
    ds = lambda w, s: T.diff_stripline(w, s, 1.4, 0.035, 4.4)
    ss = lambda w: T.stripline(w, 1.4, 0.035, 4.4, a=0.68)
    r = T.solve_diff_geometry(ds, ss, 100.0)
    assert 'error' not in r and abs(ds(r['width_mm'], r['gap_mm']).zdiff - 100.0) < 0.1, r


def test_mask_correction_uses_mask_er():
    # #90: the mask's own dielectric constant must drive the cover
    # blend -- pre-fix both calls used the substrate er and agreed.
    a = T.microstrip(0.2, 0.2, 0.035, 4.4, mask=(0.02, 3.0))
    b = T.microstrip(0.2, 0.2, 0.035, 4.4, mask=(0.02, 4.4))
    assert 'mask_correction_applied' in a.flags
    assert abs(a.z0 - b.z0) > 0.05, (a.z0, b.z0)
    # same-er mask == embedded_microstrip with that er, exactly
    e = T.embedded_microstrip(0.2, 0.2, 0.035, 0.02, 4.4)
    assert abs(b.z0 - e.z0) < 1e-9

def test_mask_correction_flags_unmodelled_dispersion():
    # #90: at f > 0 the coated (static) value replaces the
    # dispersive one -- that must be flagged, not silent.
    r = T.microstrip(0.2, 0.2, 0.035, 4.4, f_hz=2e9, mask=(0.02, 3.5))
    assert 'mask_correction_applied' in r.flags
    assert 'embedded_dispersion_not_modelled' in r.flags, r.flags
