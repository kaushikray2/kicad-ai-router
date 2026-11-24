"""Sizing math for #68 (P2.9 Step 1): per-segment width =
max(thermal width, drop width) at stacked worst-case corners.

Thermal: the Brooks & Adam (2015) published fits to the IPC-2152
charts, dT = K * W^a * Th^b * I^p with W/Th in mils -- external:
K=215.3, a=-1.15, b=-1.00, p=2 (all thicknesses); internal rows by
copper weight (Appendix 6 of the paper; see the sizing annex on
issue #68 for provenance and the consistency checks run against
the IPC-2221 closed form, which is implemented here as a
cross-check only, per the ticket).  Saturn's IPC-2152 mode is the
same charts with modifiers (plane / parallel conductors / etch);
this module is the no-modifiers baseline, evaluated at minimum
manufactured geometry (width minus etch tolerance, thickness minus
foil tolerance) in place of a trapezoidal etch model.

Drop: V = I * rho(T) * L / (W * t), rho(T) = rho20 * (1 + alpha *
(T - 20)) with rho20 = 1.72e-8 ohm*m and alpha = 0.00393 (the
ticket's values), T the maximum operating temperature (ambient +
delta-T budget), geometry at the same minimum corner.

Vias: barrel cross-section A = pi * t_p * (d + t_p) with capacity
from the IPC-2221 closed form at k = 0.048 (external constant;
the internal-k figure is reported alongside as the conservative
bound).  Saturn publishes no via equations and its IPC-2152 via
model is height-dependent, so exact Saturn parity is NOT claimed
for vias -- the annex on #68 records the comparison against the
ticket's indicative Saturn points (this model lands 3-16% below
them, i.e. conservative; via counts round up).
"""
import math

RHO20 = 1.72e-8          # ohm*m, copper at 20 C (ticket value)
ALPHA = 0.00393          # /C (ticket value)
MIL = 0.0254             # mm
MIL2_PER_MM2 = 1.0 / (MIL * MIL)

# Brooks & Adam internal rows: (oz, K, a, b, p, width-dependent K or None)
# width-dependent K entries: list of (min_width_mil, K) descending width.
_INTERNAL_ROWS = [
    (0.5, None, -1.10, -1.52, 2.0, [(100.0, 110.0), (50.0, 125.0), (0.0, 130.0)]),
    (1.0, 200.0, -1.10, -1.52, 1.9, None),
    (2.0, 300.3, -1.15, -1.52, 2.0, None),
    (3.0, None, -1.15, -1.52, 1.9, [(150.0, 300.0), (50.0, 300.0), (20.0, 235.0),
                                    (15.0, 240.0), (10.0, 225.0), (0.0, 200.0)]),
]


def rho_at(t_c):
    return RHO20 * (1.0 + ALPHA * (t_c - 20.0))


def _internal_row(t_mil, w_mil):
    oz = t_mil / 1.35  # Brooks & Adam note: 1 oz = 1.35 mil
    row = min(_INTERNAL_ROWS, key=lambda r: abs(r[0] - oz))
    (_oz, K, a, b, p, wdep) = row
    if wdep:
        for (min_w, kk) in wdep:
            if w_mil >= min_w:
                K = kk
                break
    return K, a, b, p, row[0]


def temp_rise_c(width_mm, thick_mm, current_a, internal):
    """Brooks & Adam IPC-2152 fit: temperature rise of a conductor."""
    if width_mm <= 0 or thick_mm <= 0 or current_a <= 0:
        return 0.0
    w_mil, t_mil = width_mm / MIL, thick_mm / MIL
    if not internal:
        return 215.3 * w_mil ** -1.15 * t_mil ** -1.00 * current_a ** 2
    K, a, b, p, _oz = _internal_row(t_mil, w_mil)
    return K * w_mil ** a * t_mil ** b * current_a ** p


def thermal_width_mm(current_a, dt_budget_c, thick_mm, internal,
                     etch_tol_mm=0.02, foil_tol_frac=0.10):
    """Nominal width whose MINIMUM manufactured geometry (width -
    etch tolerance, thickness - foil tolerance) keeps the sustained
    current's rise within budget.  Bisected on the fit itself."""
    if current_a <= 0:
        return 0.0
    t_min = thick_mm * (1.0 - foil_tol_frac)
    lo, hi = 0.01, 100.0
    if temp_rise_c(hi - etch_tol_mm, t_min, current_a, internal) > dt_budget_c:
        return None  # unreachable in any sane width: caller reports, never invents
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if temp_rise_c(mid - etch_tol_mm, t_min, current_a, internal) > dt_budget_c:
            lo = mid
        else:
            hi = mid
    return hi


def ipc2221_current_a(area_mil2, dt_c, internal):
    """The legacy closed form, cross-check only (ticket)."""
    k = 0.024 if internal else 0.048
    return k * dt_c ** 0.44 * area_mil2 ** 0.725


def ipc2221_width_mm(current_a, dt_c, thick_mm, internal):
    k = 0.024 if internal else 0.048
    area = (current_a / (k * dt_c ** 0.44)) ** (1.0 / 0.725)  # mil^2
    return area / (thick_mm / MIL)


def drop_width_mm(current_peak_a, length_mm, thick_mm, drop_budget_v,
                  t_op_c, etch_tol_mm=0.02, foil_tol_frac=0.10):
    """Nominal width whose minimum geometry keeps the segment drop
    within budget at the peak current and maximum operating temp."""
    if current_peak_a <= 0 or drop_budget_v <= 0:
        return 0.0
    t_min_m = thick_mm * (1.0 - foil_tol_frac) / 1000.0
    w_min_m = rho_at(t_op_c) * (length_mm / 1000.0) * current_peak_a / (drop_budget_v * t_min_m)
    return w_min_m * 1000.0 + etch_tol_mm


def segment_resistance_ohm(width_mm, thick_mm, length_mm, t_op_c):
    """Nominal-geometry loop resistance of one segment (the
    resistances-only fallback and the report both use this)."""
    if width_mm <= 0 or thick_mm <= 0:
        return None
    a_m2 = (width_mm / 1000.0) * (thick_mm / 1000.0)
    return rho_at(t_op_c) * (length_mm / 1000.0) / a_m2


def barrel_area_mil2(drill_mm, plating_um=25.0):
    t_mm = plating_um / 1000.0
    return math.pi * t_mm * (drill_mm + t_mm) * MIL2_PER_MM2


def via_capacity_a(drill_mm, dt_c, plating_um=25.0, internal_k=False):
    return ipc2221_current_a(barrel_area_mil2(drill_mm, plating_um), dt_c, internal_k)


def vias_needed(current_a, drill_mm, dt_c, plating_um=25.0):
    cap = via_capacity_a(drill_mm, dt_c, plating_um)
    if cap <= 0:
        return None
    return max(1, math.ceil(current_a / cap))


def round_up_increment(width_mm, increment_mm=0.05):
    if width_mm is None or increment_mm <= 0:
        return width_mm
    return math.ceil(width_mm / increment_mm - 1e-9) * increment_mm


DEFAULT_CORNERS = {'ambient_c': 40.0, 'delta_t_c': 20.0,
                   'drop_budget_v': 0.1, 'width_increment_mm': 0.05,
                   'etch_tol_mm': 0.02, 'foil_tol_frac': 0.10,
                   'plating_um': 25.0}


def size_segment(current_peak_a, current_sustained_a, length_mm, thick_mm,
                 internal, corners):
    """The ticket's sizing rule for one segment.  `corners` carries
    ambient_c, delta_t_c, drop_budget_v, width_increment_mm,
    etch_tol_mm, foil_tol_frac, plating_um.  Returns the full
    breakdown, or {'error': ...} when a budget is unreachable --
    a failed segment is reported, never silently widened."""
    dt = corners.get('delta_t_c', 20.0)
    t_op = corners.get('ambient_c', 40.0) + dt
    w_th = thermal_width_mm(current_sustained_a, dt, thick_mm, internal,
                            corners.get('etch_tol_mm', 0.02),
                            corners.get('foil_tol_frac', 0.10))
    if w_th is None:
        return {'error': 'thermal budget unreachable within 100 mm width'}
    w_drop = drop_width_mm(current_peak_a, length_mm, thick_mm,
                           corners.get('drop_budget_v', 0.1), t_op,
                           corners.get('etch_tol_mm', 0.02),
                           corners.get('foil_tol_frac', 0.10))
    w = max(w_th, w_drop)
    return {'width_mm': round(round_up_increment(w, corners.get('width_increment_mm', 0.05)), 4),
            'thermal_width_mm': round(w_th, 4), 'drop_width_mm': round(w_drop, 4),
            'governing': 'thermal' if w_th >= w_drop else 'drop',
            'resistance_ohm': segment_resistance_ohm(w, thick_mm, length_mm, t_op),
            't_op_c': t_op}
