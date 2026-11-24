"""#68 Step 1: sizing math fixtures.

Thermal values are the Brooks & Adam fits to the IPC-2152 charts
(see the sizing annex on #68); the via worked example (0.3 mm
drill, 25 um plating, 10 C rise -> 1.90 A, 3 vias for 5 A) is the
published Sierra-style barrel calculation, cross-checked in two
independent write-ups.  The ticket's indicative Saturn via points
(1.74 / 1.96 / 2.52 A at 0.2 / 0.25 / 0.4 mm) are asserted as a
conservative band, not exact parity -- Saturn publishes no via
equations and its model is height-dependent (annex)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import wcca

CORNERS = {'ambient_c': 40.0, 'delta_t_c': 20.0, 'drop_budget_v': 0.1,
           'width_increment_mm': 0.05, 'etch_tol_mm': 0.02, 'foil_tol_frac': 0.10,
           'plating_um': 25.0}


def test_thermal_fit_known_point_and_corners():
    # 2 A on 1 oz external: the IPC-2152 fit solves ~0.93 mm for 10 C
    # (IPC-2221's closed form gives 0.78 mm; IPC-2152 runs ~25% lower
    # currents, i.e. wider -- Brooks & Adam's stated relationship).
    assert abs(wcca.temp_rise_c(0.927, 0.035, 2.0, False) - 10.0) < 0.2
    w = wcca.thermal_width_mm(2.0, 10.0, 0.035, False, 0.02, 0.10)
    t_min = 0.035 * 0.9
    assert wcca.temp_rise_c(w - 0.02, t_min, 2.0, False) <= 10.0
    assert wcca.temp_rise_c(w - 0.03, t_min, 2.0, False) > 10.0  # minimal within 10 um
    # internal cools nearly as well as external under IPC-2152 (the
    # standard's central finding): internal width < 1.3x external,
    # nowhere near IPC-2221's 2.6x.
    wi = wcca.thermal_width_mm(2.0, 10.0, 0.035, True, 0.02, 0.10)
    assert wi < 1.3 * w, (wi, w)


def test_ipc2221_crosscheck_point():
    # published fit point: 68.9 mil^2 external at 20 C rise -> ~3.85 A
    assert abs(wcca.ipc2221_current_a(68.9, 20.0, False) - 3.85) < 0.02


def test_drop_width_forward_check():
    w = wcca.drop_width_mm(2.0, 50.0, 0.035, 0.1, 60.0, 0.02, 0.10)
    r = wcca.segment_resistance_ohm(w - 0.02, 0.035 * 0.9, 50.0, 60.0)
    assert r * 2.0 <= 0.1 + 1e-9
    r_below = wcca.segment_resistance_ohm(w - 0.03, 0.035 * 0.9, 50.0, 60.0)
    assert r_below * 2.0 > 0.1


def test_via_capacity_and_count():
    assert abs(wcca.via_capacity_a(0.3, 10.0, 25.0) - 1.90) < 0.02
    assert wcca.vias_needed(5.0, 0.3, 10.0, 25.0) == 3
    for drill, saturn in ((0.2, 1.74), (0.25, 1.96), (0.4, 2.52)):
        cap = wcca.via_capacity_a(drill, 10.0, 25.0)
        assert 0.8 * saturn <= cap <= saturn, (drill, cap, saturn)  # conservative band


def test_size_segment_governing_and_rounding():
    s = wcca.size_segment(2.0, 2.0, 200.0, 0.035, False, CORNERS)
    assert s['governing'] == 'drop'  # 200 mm at 0.1 V budget: drop dominates
    assert abs(s['width_mm'] / 0.05 - round(s['width_mm'] / 0.05)) < 1e-6  # fab increment
    s2 = wcca.size_segment(2.0, 2.0, 5.0, 0.035, False, CORNERS)
    assert s2['governing'] == 'thermal'
    assert s2['width_mm'] >= s2['thermal_width_mm']
