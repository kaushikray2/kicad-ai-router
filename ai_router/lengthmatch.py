"""Length / delay evaluation for matched groups (#69).

Delay is computed from routed geometry and the stackup's
effective permittivity per layer -- the #67 calculator's
microstrip eps_eff for surface layers, eps_r itself for inner
(stripline) layers.  No field solver and no dispersion model
beyond what the calculator carries; meander self-coupling is
not modelled.  Both limitations are stated in the report.

Layer info shape (built by the pipeline from the worker stackup
command): {layer: {'class': 'surface_top'|'surface_bottom'|
'inner', 'er': float, 'h_mm': float, 'thickness_mm': float}}
where h_mm is the dielectric thickness to the reference plane
(adjacent dielectric) and er its permittivity.
"""
import math

from . import tlcalc

MM_PER_PS = tlcalc.C0_MM / 1e12  # mm travelled in 1 ps in vacuum


def layer_eps_eff(layer_info, width_mm):
    er = layer_info.get('er') or 4.5
    if layer_info.get('class') in ('surface_top', 'surface_bottom'):
        h = layer_info.get('h_mm') or 0.2
        t = layer_info.get('thickness_mm') or 0.035
        return tlcalc.microstrip(max(width_mm, 0.05), h, t, er).eps_eff
    return float(er)  # stripline: homogeneous dielectric


def member_stats(segments, layers):
    """segments: [{layer, length_mm, width_mm}].  Returns total
    length and delay accumulating per-layer eps_eff."""
    length = 0.0
    delay = 0.0
    for s in segments:
        info = layers.get(s['layer']) or {}
        eps = layer_eps_eff(info, s.get('width_mm') or 0.2)
        length += s['length_mm']
        delay += s['length_mm'] * math.sqrt(eps) / MM_PER_PS
    return {'length_mm': length, 'delay_ps': delay}


def evaluate_group(entry, members):
    """members: {net: member_stats}.  Resolves the target and
    grades every member.  Tolerance semantics: a member is in
    tolerance when its shortfall vs target is within BOTH
    declared tolerances (mm checked on length, ps on delay)."""
    mode = entry.get('target_mode', 'longest')
    if mode == 'longest':
        tgt_net = max(members, key=lambda n: members[n]['length_mm'])
        target = dict(members[tgt_net])
        target['from_net'] = tgt_net
    elif mode == 'length':
        v = entry['target_value']
        target = {'length_mm': v, 'delay_ps': None, 'from_net': None}
    else:
        v = entry['target_value']
        target = {'length_mm': None, 'delay_ps': v, 'from_net': None}
    tol_mm = entry.get('tolerance_mm')
    tol_ps = entry.get('tolerance_ps')
    out = {'name': entry.get('name'), 'target_mode': mode, 'target': target,
           'tolerance_mm': tol_mm, 'tolerance_ps': tol_ps, 'members': {}}
    for net, st in members.items():
        d_mm = (target['length_mm'] - st['length_mm']) if target['length_mm'] is not None else None
        d_ps = (target['delay_ps'] - st['delay_ps']) if target['delay_ps'] is not None else None
        ok = True
        if d_mm is not None and tol_mm is not None and abs(d_mm) > tol_mm:
            ok = False
        if d_ps is not None and tol_ps is not None and abs(d_ps) > tol_ps:
            ok = False
        if d_mm is not None and tol_mm is None and tol_ps is None:
            ok = d_mm <= 0
        out['members'][net] = {'length_mm': round(st['length_mm'], 3),
                               'delay_ps': round(st['delay_ps'], 2),
                               'delta_mm': round(d_mm, 3) if d_mm is not None else None,
                               'delta_ps': round(d_ps, 2) if d_ps is not None else None,
                               'within_tolerance': ok}
    return out
