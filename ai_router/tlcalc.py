"""Transmission-line calculator for engineered (impedance-marked) nets.

Pure Python (no pcbnew) so the pipeline, the verification report and the
tests can all use it.  Every model is a published closed form, ported from
a named reference implementation; each result carries the model name, its
source and the validity range the source states.  Inputs outside the
stated range still compute, but the result is flagged
(``in_range == False``) rather than silently extrapolated.

Model provenance
----------------
* Surface microstrip: Hammerstad & Jensen static model with Kirschning &
  Jansen dispersion, as implemented by KiCad 10's PCB Calculator (itself
  derived from qucs).  KiCad's calculator is the acceptance reference for
  single-ended widths (issue #67 Step 1).
* Embedded microstrip: Wadell, *Transmission Line Design Handbook* (1991)
  section 3.5.4 exponential cover filling over the surface result; the
  same construction IPC-D-317A publishes for coated microstrip, which is
  how the solder-mask correction (annex CALC-4) is applied: a flooded
  mask coat is an embedded cover.
* Stripline (centred and offset): KiCad 10 PCB Calculator stripline
  (qucs lineage): two-branch line impedance, offset handled as the
  parallel combination of two centred lines.
* Coplanar waveguide (with or without backside ground): Ghione & Naldi
  conformal mapping as implemented by KiCad 10's PCB Calculator,
  including its thickness correction and dispersion factor.
* Edge-coupled differential microstrip: IPC-2141A closed form with the
  AN-905 odd-mode factor; cross-checked against the worked example in
  the Saturn PCB Toolkit help (W=10, S=5, H=15, T=2.10 mil, er=4.6 ->
  Zo 77.504, Zodd 50.490, Zdiff 100.979 ohm).
* Edge-coupled differential stripline: Cohn, "Shielded Coupled-Strip
  Transmission Line", IRE Trans. MTT-3 (1955): even/odd conformal
  mapping with Cohn's fringing-capacitance thickness corrections.

Per annex CALC-6 there is deliberately NO conductor-roughness term
anywhere: conductor loss is computed with roughness = 0 and the report
layer surfaces roughness as a warning instead (WARN-3).

Units: millimetres, hertz, ohms, S/m.  Loss is dB per millimetre.
"""

import math
from dataclasses import dataclass, field

C0 = 299792458.0            # speed of light, m/s
C0_MM = 299792458000.0      # speed of light, mm/s
ZF0 = 376.730313668         # free-space impedance, ohm (KiCad/qucs value)
MU0 = 4.0 * math.pi * 1e-7  # H/m
COPPER_SIGMA = 5.8e7        # S/m, KiCad PCB Calculator copper default

# Solder-mask correction switch point (annex CALC-4): the coated-microstrip
# correction is applied at or below this dielectric height.
MASK_CORRECTION_MAX_H_MM = 0.25


@dataclass
class TLResult:
    z0: float
    eps_eff: float
    model: str
    reference: str
    in_range: bool = True
    flags: list = field(default_factory=list)
    loss_db_per_mm: float = None
    zodd: float = None
    zeven: float = None
    zdiff: float = None
    detail: dict = field(default_factory=dict)

    def lambda_g_mm(self, f_hz):
        """Guide wavelength at f_hz, or None when f is 0/unknown."""
        if not f_hz or f_hz <= 0:
            return None
        return C0_MM / (f_hz * math.sqrt(self.eps_eff))

    def to_dict(self):
        return {
            'z0': self.z0, 'eps_eff': self.eps_eff, 'model': self.model,
            'reference': self.reference, 'in_range': self.in_range,
            'flags': list(self.flags),
            'loss_db_per_mm': self.loss_db_per_mm,
            'zodd': self.zodd, 'zeven': self.zeven, 'zdiff': self.zdiff,
            'detail': dict(self.detail),
        }


# ---------------------------------------------------------------------------
# Elliptic integrals via the arithmetic-geometric mean (exact to rounding).
# ---------------------------------------------------------------------------

def _agm(a, b):
    if a == 0.0 or b == 0.0:
        return 0.0
    for _ in range(64):
        an = 0.5 * (a + b)
        bn = math.sqrt(a * b)
        if abs(an - bn) <= 1e-16 * an:
            return an
        a, b = an, bn
    return a


def elliptic_k(k):
    """Complete elliptic integral of the first kind K(k), modulus k."""
    kp = math.sqrt(max(0.0, 1.0 - k * k))
    return math.pi / (2.0 * _agm(1.0, kp))


def elliptic_ratio(k):
    """K(k)/K(k') for modulus 0 < k < 1, via AGM (well conditioned)."""
    if not (0.0 < k < 1.0):
        raise ValueError('elliptic modulus out of range: %r' % (k,))
    kp = math.sqrt(1.0 - k * k)
    return _agm(1.0, k) / _agm(1.0, kp)


def _ratio_saturated(k):
    """elliptic_ratio with the modulus clamped off the 0/1 endpoints.

    At extreme aspect ratios the exact modulus saturates to 0 or 1 in
    floating point (e.g. tanh ratios for a very wide CPW centre); K(k)
    diverges only logarithmically there, so clamping at 1e-12 keeps the
    result finite and close instead of crashing the calculator.
    """
    return elliptic_ratio(min(max(k, 1e-12), 1.0 - 1e-12))


# ---------------------------------------------------------------------------
# Surface microstrip -- KiCad 10 PCB Calculator port (Hammerstad & Jensen
# static, Kirschning & Jansen dispersion; qucs lineage).
# ---------------------------------------------------------------------------

def _ms_z0_homogeneous(u):
    f = 6.0 + (2.0 * math.pi - 6.0) * math.exp(-((30.666 / u) ** 0.7528))
    return (ZF0 / (2.0 * math.pi)) * math.log(f / u + math.sqrt(1.0 + 4.0 / (u * u)))


def _ms_delta_u_thickness(u, t_h, er):
    if t_h <= 0.0:
        return 0.0
    du = (t_h / math.pi) * math.log(
        1.0 + (4.0 * math.e) * math.tanh(math.sqrt(6.517 * u)) ** 2 / t_h)
    return 0.5 * du * (1.0 + 1.0 / math.cosh(math.sqrt(max(0.0, er - 1.0))))


def _ms_filling_factor(u, er):
    u2 = u * u
    u3 = u2 * u
    u4 = u3 * u
    a = (1.0 + math.log((u4 + u2 / 2704.0) / (u4 + 0.432)) / 49.0
         + math.log(1.0 + u3 / 5929.741) / 18.7)
    b = 0.564 * (((er - 0.9) / (er + 3.0)) ** 0.053)
    return (1.0 + 10.0 / u) ** (-a * b)


def _ms_delta_q_thickness(u, t_h):
    return (2.0 * math.log(2.0) / math.pi) * (t_h / math.sqrt(u))


def _ms_er_dispersion(u, er, f_n):
    p1 = (0.27488 + u * (0.6315 + 0.525 / ((1.0 + 0.0157 * f_n) ** 20.0))
          - 0.065683 * math.exp(-8.7513 * u))
    p2 = 0.33622 * (1.0 - math.exp(-0.03442 * er))
    p3 = 0.0363 * math.exp(-4.6 * u) * (1.0 - math.exp(-((f_n / 38.7) ** 4.97)))
    p4 = 1.0 + 2.751 * (1.0 - math.exp(-((er / 15.916) ** 8.0)))
    return p1 * p2 * (((p3 * p4 + 0.1844) * f_n) ** 1.5763)


def _ms_z0_dispersion(u, er, er_eff_0, er_eff_f, f_n):
    r1 = 0.03891 * er ** 1.4
    r2 = 0.267 * u ** 7.0
    r3 = 4.766 * math.exp(-3.228 * u ** 0.641)
    r4 = 0.016 + (0.0514 * er) ** 4.524
    r5 = (f_n / 28.843) ** 12.0
    r6 = 22.2 * u ** 1.92
    r7 = 1.206 - 0.3144 * math.exp(-r1) * (1.0 - math.exp(-r2))
    r8 = 1.0 + 1.275 * (1.0 - math.exp(
        -0.004625 * r3 * er ** 1.674 * (f_n / 18.365) ** 2.745))
    tmpf = (er - 1.0) ** 6.0
    r9 = (5.086 * r4 * (r5 / (0.3838 + 0.386 * r4)) * (math.exp(-r6) / (1.0 + 1.2992 * r5))
          * (tmpf / (1.0 + 10.0 * tmpf)))
    r10 = 0.00044 * er ** 2.136 + 0.0184
    tmpf = (f_n / 19.47) ** 6.0
    r11 = tmpf / (1.0 + 0.0962 * tmpf)
    r12 = 1.0 / (1.0 + 0.00245 * u * u)
    r13 = 0.9408 * er_eff_f ** r8 - 0.9603
    r14 = (0.9408 - r9) * er_eff_0 ** r8 - 0.9603
    r15 = 0.707 * r10 * (f_n / 12.3) ** 1.097
    r16 = 1.0 + 0.0503 * er * er * r11 * (1.0 - math.exp(-((u / 15.0) ** 6.0)))
    r17 = r7 * (1.0 - 1.1241 * (r12 / r16) * math.exp(-0.026 * f_n ** 1.15656 - r15))
    return (r13 / r14) ** r17


def _ms_static(w, h, t, er):
    """Static (zero-frequency) microstrip solution.

    Returns (z0, er_eff_0, z0_h_1): the static characteristic impedance,
    the full effective permittivity (thickness-corrected, the value the
    dispersion step starts from), and the er=1 homogeneous impedance the
    conductor-loss model uses.  No cover layer (filling factor q_c = 1).
    """
    u = w / h
    t_h = t / h
    du1 = _ms_delta_u_thickness(u, t_h, 1.0)
    z0_h_1 = _ms_z0_homogeneous(u + du1)
    u_r = u + _ms_delta_u_thickness(u, t_h, er)
    z0_h_r = _ms_z0_homogeneous(u_r)
    q = _ms_filling_factor(u_r, er) - _ms_delta_q_thickness(u_r, t_h)
    er_eff_t = 0.5 * (er + 1.0) + 0.5 * q * (er - 1.0)
    er_eff_0 = er_eff_t * (z0_h_1 / z0_h_r) ** 2
    z0 = z0_h_r / math.sqrt(er_eff_t)
    return z0, er_eff_0, z0_h_1


def _ms_losses(w, h, er, er_eff_0, z0_h_1, f_hz, tand, sigma):
    """Conductor (roughness = 0, annex CALC-6) + dielectric loss, dB/mm."""
    if f_hz <= 0:
        return None
    alpha_c = 0.0
    if sigma and sigma > 0:
        delta = math.sqrt(1.0 / (math.pi * f_hz * MU0 * sigma))  # metres
        r_s = 1.0 / (sigma * delta)
        k = math.exp(-1.2 * (z0_h_1 / ZF0) ** 0.7)
        q_c = (math.pi * z0_h_1 * (w / 1000.0) * f_hz) / (r_s * C0 * k)
        alpha_c = (20.0 * math.pi / math.log(10.0)) * f_hz * math.sqrt(er_eff_0) / (C0 * q_c)
    alpha_d = 0.0
    if tand and tand > 0 and er > 1.0:
        alpha_d = ((20.0 * math.pi / math.log(10.0)) * (f_hz / C0)
                   * (er / math.sqrt(er_eff_0)) * ((er_eff_0 - 1.0) / (er - 1.0)) * tand)
    return (alpha_c + alpha_d) / 1000.0


def microstrip(w, h, t, er, f_hz=0.0, tand=0.0, sigma=COPPER_SIGMA, mask=None):
    """Surface microstrip Z0 / eps_eff at width w (all dims mm).

    mask, when given, is (thickness_mm, er) of a flooded solder-mask coat:
    at h <= MASK_CORRECTION_MAX_H_MM the coated (embedded) value becomes
    the primary result (annex CALC-4); above it the correction stays off
    and the result is flagged.
    """
    z0, er_eff_0, z0_h_1 = _ms_static(w, h, t, er)
    flags = []
    u = w / h
    in_range = (0.01 <= u <= 100.0) and (1.0 <= er <= 128.0)
    if not in_range:
        flags.append('outside_hj_validity')
    eps_eff = er_eff_0
    z0_f = z0
    if f_hz and f_hz > 0:
        f_n = f_hz * h / 1e9  # normalised frequency, GHz*mm
        p = _ms_er_dispersion(u, er, f_n)
        er_eff_f = er - (er - er_eff_0) / (1.0 + p)
        d = _ms_z0_dispersion(u, er, er_eff_0, er_eff_f, f_n)
        z0_f = z0 * d
        eps_eff = er_eff_f
        lam0 = C0_MM / f_hz
        if er > 20.0 or h / lam0 > 0.13:
            in_range = False
            flags.append('outside_dispersion_validity')
    loss = _ms_losses(w, h, er, er_eff_0, z0_h_1, f_hz, tand, sigma)
    res = TLResult(z0=z0_f, eps_eff=eps_eff, model='microstrip',
                   reference='Hammerstad & Jensen 1980 + Kirschning & Jansen '
                             'dispersion, as KiCad 10 PCB Calculator',
                   in_range=in_range, flags=flags, loss_db_per_mm=loss)
    if mask is not None:
        mask_t, _mask_er = mask
        if h <= MASK_CORRECTION_MAX_H_MM:
            coated = embedded_microstrip(w, h, t, mask_t, er)
            res.detail['z0_unmasked'] = res.z0
            res.detail['mask_delta_ohm'] = coated.z0 - res.z0
            res.z0 = coated.z0
            res.eps_eff = coated.eps_eff
            res.flags.append('mask_correction_applied')
        else:
            res.flags.append('mask_correction_off_thick_dielectric')
    return res


# ---------------------------------------------------------------------------
# Embedded microstrip -- Wadell 1991 section 3.5.4 cover filling (the form
# IPC-D-317A publishes; also the coated-microstrip construction).
# ---------------------------------------------------------------------------

def embedded_microstrip(w, h, t, cover, er, f_hz=0.0):
    """Microstrip buried under `cover` mm of dielectric of the same er.

    eps_eff,emb = er - (er - eps_eff,surf) * exp(-2*cover/h); the impedance
    is the surface static solution re-evaluated at eps_eff,emb.
    """
    z0_s, er_eff_s, z0_h_1 = _ms_static(w, h, t, er)
    flags = []
    if cover <= 0.0:
        return TLResult(z0=z0_s, eps_eff=er_eff_s, model='embedded_microstrip',
                        reference='Wadell 1991 s3.5.4 (cover = 0: surface line)',
                        flags=flags)
    er_eff_e = er - (er - er_eff_s) * math.exp(-2.0 * cover / h)
    # The geometry's air (er=1) impedance over the embedded eps_eff: the
    # surface Z0 rescaled by sqrt(eps_eff,surf / eps_eff,emb).
    z0_e = z0_h_1 / math.sqrt(er_eff_e)
    if f_hz and f_hz > 0:
        flags.append('embedded_dispersion_not_modelled')
    return TLResult(z0=z0_e, eps_eff=er_eff_e, model='embedded_microstrip',
                    reference='Wadell 1991 s3.5.4; IPC-D-317A embedded/coated form',
                    flags=flags)


# ---------------------------------------------------------------------------
# Stripline -- KiCad 10 PCB Calculator port (qucs lineage).  `b` is the
# plane-to-plane spacing; `a` is the gap from the strip's lower face to the
# lower plane (default: centred).  Offset is the parallel combination of
# two centred lines of spacings 2a+t and 2(b-a)-t.
# ---------------------------------------------------------------------------

def _sl_line_impedance(a_height, w, t, er):
    hmt = a_height - t
    if w / hmt >= 0.35:
        # t*log(...) vanishes as t -> 0 (its argument -> 1 proportionally).
        tlog = t * math.log(a_height * a_height / (hmt * hmt) - 1.0) if t > 0.0 else 0.0
        w_eff = w + (2.0 * a_height * math.log((2.0 * a_height - t) / hmt)
                     - tlog) / math.pi
        return ZF0 * hmt / (math.sqrt(er) * 4.0 * w_eff)
    tdw = t / w
    if tdw > 1.0:
        tdw = w / t
    if tdw > 0.0:
        de = 1.0 + tdw / math.pi * (1.0 + math.log(4.0 * math.pi / tdw)) + 0.236 * tdw ** 1.65
    else:
        de = 1.0  # t = 0 limit of the correction terms
    de *= (t / 2.0) if (t / w) > 1.0 else (w / 2.0)
    return ZF0 / (2.0 * math.pi * math.sqrt(er)) * math.log(4.0 * a_height / (math.pi * de))


def stripline(w, b, t, er, a=None, f_hz=0.0, tand=0.0):
    """Stripline Z0.  eps_eff = er (homogeneous dielectric, no dispersion,
    as in the reference implementation).  Loss is the dielectric term only.
    """
    if t >= b:
        raise ValueError('stripline thickness must be < plane spacing')
    if a is None:
        a = (b - t) / 2.0
    z1 = _sl_line_impedance(2.0 * a + t, w, t, er)
    z2 = _sl_line_impedance(2.0 * (b - a) - t, w, t, er)
    z0 = 2.0 / (1.0 / z1 + 1.0 / z2)
    flags = []
    in_range = True
    if t / b > 0.25:
        in_range = False
        flags.append('thick_strip_beyond_crosscheck_range')
    loss = None
    if f_hz and f_hz > 0 and tand and tand > 0:
        loss = (20.0 / math.log(10.0)) * (math.pi / C0_MM) * f_hz * math.sqrt(er) * tand
    return TLResult(z0=z0, eps_eff=float(er), model='stripline',
                    reference='KiCad 10 PCB Calculator stripline (qucs lineage); '
                              'centred form cross-checked vs Cohn 1954',
                    in_range=in_range, flags=flags, loss_db_per_mm=loss)


# ---------------------------------------------------------------------------
# Coplanar waveguide -- Ghione & Naldi conformal mapping as implemented by
# KiCad 10's PCB Calculator (thickness correction + dispersion factor).
# ---------------------------------------------------------------------------

def coplanar(w, s, h, t, er, f_hz=0.0, grounded=True, tand=0.0):
    """CPW Z0: centre width w, gap s each side, dielectric height h to the
    backside (grounded=True: backside metal; False: air)."""
    k1 = w / (w + 2.0 * s)
    q1 = elliptic_ratio(k1)
    if grounded:
        k3 = (math.tanh(math.pi * w / (4.0 * h))
              / math.tanh(math.pi * (w + 2.0 * s) / (4.0 * h)))
        q3 = _ratio_saturated(k3)
        qz = 1.0 / (q1 + q3)
        er0 = 1.0 + q3 * qz * (er - 1.0)
        zl = ZF0 / 2.0 * qz
    else:
        k2 = (math.sinh(math.pi * w / (4.0 * h))
              / math.sinh(math.pi * (w + 2.0 * s) / (4.0 * h)))
        q2 = elliptic_ratio(k2)
        er0 = 1.0 + (er - 1.0) / 2.0 * q2 / q1
        zl = ZF0 / 4.0 / q1
        q3 = 0.0
    if t > 0.0:
        d = (1.25 * t / math.pi) * (1.0 + math.log(4.0 * math.pi * w / t))
        se = s - d
        we = w + d
        if se <= 0.0:
            raise ValueError('coplanar thickness correction consumes the gap')
        qe = elliptic_ratio(we / (we + 2.0 * se))
        if grounded:
            qz = 1.0 / (qe + q3)
            er0 = 1.0 + q3 * qz * (er - 1.0)
            zl = ZF0 / 2.0 * qz
        else:
            zl = ZF0 / 4.0 / qe
        er0 = er0 - (0.7 * (er0 - 1.0) * t / s) / (q1 + 0.7 * t / s)
    sr_er0 = math.sqrt(er0)
    sr_er_f = sr_er0
    if f_hz and f_hz > 0 and er > 1.0:
        fte = (C0_MM / 4.0) / (h * math.sqrt(er - 1.0))
        p = math.log(w / h)
        uu = 0.54 - (0.64 - 0.015 * p) * p
        vv = 0.43 - (0.86 - 0.54 * p) * p
        g = math.exp(uu * math.log(w / s) + vv)
        sr_er_f = sr_er0 + (math.sqrt(er) - sr_er0) / (1.0 + g * (f_hz / fte) ** -1.8)
    eps_eff = sr_er_f * sr_er_f
    z0 = zl / sr_er_f
    flags = []
    in_range = True
    if s > 0 and t / s > 0.1:
        in_range = False
        flags.append('thickness_correction_beyond_t_over_s_0.1')
    loss = None
    if f_hz and f_hz > 0 and tand and tand > 0 and er > 1.0:
        ad = (er / (er - 1.0)) * tand * math.pi / C0_MM
        loss = (20.0 / math.log(10.0)) * f_hz * ad * (eps_eff - 1.0) / sr_er_f
    return TLResult(z0=z0, eps_eff=eps_eff, model='coplanar',
                    reference='Ghione & Naldi conformal mapping, as KiCad 10 '
                              'PCB Calculator',
                    in_range=in_range, flags=flags, loss_db_per_mm=loss)


# ---------------------------------------------------------------------------
# Cohn stripline helpers (private): the exact conformal-mapping centred
# stripline (Cohn 1954 + Wadell thickness correction).  Used by the coupled
# stripline model, whose thickness corrections are stated against this
# single-strip solution; the headline stripline() above follows KiCad.
# ---------------------------------------------------------------------------

def _cohn_delta_w(w, b, t):
    if t <= 0.0:
        return 0.0
    m = 6.0 * (b - t) / (3.0 * b - t)
    return (t / math.pi) * (1.0 - 0.5 * math.log(
        (t / (2.0 * b - t)) ** 2 + (0.0796 * t / (w + 1.1 * t)) ** m))


def _cohn_z0_symmetric(w, b, t, er):
    if t >= b:
        raise ValueError('stripline thickness must be < plane spacing')
    we = w + _cohn_delta_w(w, b, t)
    be = b - t
    k = 1.0 / math.cosh(math.pi * we / (2.0 * be))
    return 30.0 * math.pi / math.sqrt(er) * _ratio_saturated(k)


# ---------------------------------------------------------------------------
# Edge-coupled differential microstrip -- IPC-2141A closed form with the
# AN-905 odd-mode factor.
# ---------------------------------------------------------------------------

def diff_microstrip(w, s, h, t, er):
    """Differential pair on a surface layer.  Returns z0 (single-ended, IPC
    form), zodd, zeven, zdiff; eps_eff is the single-line H-J value (the
    odd-mode eps_eff differs slightly; flagged, not modelled)."""
    zo = (87.0 / math.sqrt(er + 1.41)) * math.log(5.98 * h / (0.8 * w + t))
    if zo <= 0.0:
        raise ValueError('IPC-2141A microstrip form requires 5.98*h > 0.8*w + t')
    zodd = zo * (1.0 - 0.48 * math.exp(-0.96 * s / h))
    zeven = zo * zo / zodd
    _zs, er_eff, _z1 = _ms_static(w, h, t, er)
    flags = ['eps_eff_single_line_approx']
    in_range = (0.1 < w / h < 2.0) and (1.0 < er < 15.0) and (0.2 <= s / h <= 3.0)
    if not in_range:
        flags.append('outside_an905_validity')
    return TLResult(z0=zo, eps_eff=er_eff, model='diff_microstrip',
                    reference='IPC-2141A + AN-905 odd-mode factor; Saturn PCB '
                              'Toolkit help worked example',
                    in_range=in_range, flags=flags,
                    zodd=zodd, zeven=zeven, zdiff=2.0 * zodd)


# ---------------------------------------------------------------------------
# Edge-coupled differential stripline -- Cohn 1955 even/odd modes with the
# fringing-capacitance thickness corrections (eq. 18, 20, 22).
# ---------------------------------------------------------------------------

def _cohn_modes_thin(w, s, b, er):
    a = math.tanh(math.pi * w / (2.0 * b))
    c = math.tanh(math.pi * (w + s) / (2.0 * b))
    ke = a * c
    ko = a / c
    scale = 30.0 * math.pi / math.sqrt(er)
    ze = scale / _ratio_saturated(ke)
    zo = scale / _ratio_saturated(ko)
    return ze, zo


def _cohn_fringe_ratio(x):
    y = 1.0 / (1.0 - x)
    return (2.0 * y * math.log(y + 1.0) - (y - 1.0) * math.log(y * y - 1.0)) / (2.0 * math.log(2.0))


def diff_stripline(w, s, b, t, er):
    """Differential pair centred between planes b apart.  eps_eff = er."""
    if s <= 0.0:
        raise ValueError('diff stripline spacing must be positive')
    z0s = _cohn_z0_symmetric(w, b, t, er)
    flags = []
    in_range = True
    if t / b > 0.25:
        in_range = False
        flags.append('thickness_correction_beyond_t_over_b_0.25')
    if t <= 0.0:
        ze, zo = _cohn_modes_thin(w, s, b, er)
        return TLResult(z0=z0s, eps_eff=float(er), model='diff_stripline',
                        reference='Cohn 1955 coupled stripline (zero thickness)',
                        in_range=in_range, flags=flags,
                        zodd=zo, zeven=ze, zdiff=2.0 * zo)
    z0s0 = _cohn_z0_symmetric(w, b, 0.0, er)
    ze0, zo0 = _cohn_modes_thin(w, s, b, er)
    r = _cohn_fringe_ratio(t / b)
    eta = ZF0 / math.sqrt(er)
    cf0 = 2.0 * math.log(2.0) / math.pi
    cft = r * cf0
    ye = 1.0 / z0s - r * (1.0 / z0s0 - 1.0 / ze0)

    def y20(zo_0):
        return 1.0 / z0s + r * (1.0 / zo_0 - 1.0 / z0s0)

    def y22(zo_0, sp):
        return (1.0 / zo_0 + (1.0 / z0s - 1.0 / z0s0)
                - (2.0 / eta) * (cft - cf0) + 2.0 * t / (eta * sp))

    if s >= 5.0 * t:
        yo = y20(zo0)
    else:
        s5 = 5.0 * t
        _ze5, zo0_5 = _cohn_modes_thin(w, s5, b, er)
        yo = y22(zo0, s) - y22(zo0_5, s5) + y20(zo0_5)
    ze = 1.0 / ye
    zo = 1.0 / yo
    return TLResult(z0=z0s, eps_eff=float(er), model='diff_stripline',
                    reference='Cohn 1955 coupled stripline, eq. 18/20/22; '
                              'Wadell 1991 s4.3',
                    in_range=in_range, flags=flags,
                    zodd=zo, zeven=ze, zdiff=2.0 * zo)


# ---------------------------------------------------------------------------
# Width solver + band helpers (annex CALC-1).
# ---------------------------------------------------------------------------

def solve_width(z0_of_w, target, lo=0.005, hi=25.0, tol=1e-4):
    """Bisect a width for target Z0 from a decreasing z0_of_w(w) callable.

    Returns the width in mm, or None when the target is unreachable inside
    [lo, hi] (caller must flag, never extrapolate silently).
    """
    z_lo = z0_of_w(lo)
    z_hi = z0_of_w(hi)
    if target > z_lo or target < z_hi:
        return None
    for _ in range(200):
        if hi - lo < tol:
            break
        mid = 0.5 * (lo + hi)
        if z0_of_w(mid) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def band_of(f_hz):
    """Annex frequency bands.  Above 6 GHz the tool makes no validation
    claims (NO-11): callers must refuse, not extrapolate."""
    if f_hz is None or f_hz <= 0:
        return None
    if f_hz < 1e9:
        return 'SUB'
    if f_hz < 3e9:
        return 'MID'
    if f_hz <= 6e9:
        return 'HIGH'
    return 'ABOVE_6GHZ'


def lambda_g_mm(eps_eff, f_hz):
    if not f_hz or f_hz <= 0:
        return None
    return C0_MM / (f_hz * math.sqrt(eps_eff))
