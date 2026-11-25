"""Quiet nets (#71): sensitivity markings with isolation gates.

The check set follows published vendor layout guidance
(Microchip's oscillator design guide; ST AN2867 sec.7 -- see the
design note on issue #71): keep foreign signals away from the
quiet copper, mind the land pattern, give load caps a short
ground return, and tie guards at both ends (#67's termination
rule).  The numeric isolation defaults below are TOOL defaults
-- no vendor publishes a universal millimetre value -- stated in
every report and overridable per marking.

Geometric isolation only: no coupling simulation, no dB claims.
Zone fills (pours) are not intrusions -- a solid ground pour is
the recommended environment; routed tracks, vias and foreign
pads are the aggressors checked.
"""
import math

from . import hvdomains as _H

PROFILES = {
    'crystal': {'isolation_mm': 1.0, 'land_keepout': True,
                'load_cap_return': True, 'allow_nets': ['GND']},
    'analog': {'isolation_mm': 1.5, 'land_keepout': False,
               'load_cap_return': False, 'allow_nets': []},
    'reference': {'isolation_mm': 2.0, 'land_keepout': False,
                  'load_cap_return': False, 'allow_nets': []},
}
_DEFAULT = {'isolation_mm': 1.0, 'land_keepout': False,
            'load_cap_return': False, 'allow_nets': []}
LAND_MARGIN_MM = 0.5      # stated: land-pattern keepout margin
CAP_RETURN_VIA_MM = 2.0   # stated: cap ground via search radius


def resolve(entry):
    """Merge a marking with its profile defaults; every value in
    the result is printed in the report."""
    prof = dict(_DEFAULT)
    prof_name = entry.get('profile')
    if prof_name:
        prof.update(PROFILES[prof_name])
    out = dict(prof)
    out['profile'] = prof_name
    out['isolation_source'] = 'profile' if prof_name else 'tool default'
    if 'isolation_mm' in entry:
        out['isolation_mm'] = entry['isolation_mm']
        out['isolation_source'] = 'user'
    allow = list(out.get('allow_nets') or [])
    for n in entry.get('allow_nets') or []:
        if n not in allow:
            allow.append(n)
    out['allow_nets'] = allow
    out['guard_net'] = entry.get('guard_net')
    out['name'] = entry.get('name') or entry.get('net') or entry.get('ref')
    return out


def _pt_in_rect(x, y, rect, grow=0.0):
    return (rect[0] - grow <= x <= rect[2] + grow
            and rect[1] - grow <= y <= rect[3] + grow)


def _seg_hits_rect(item, rect, grow):
    if item[0] == 'disc':
        return _pt_in_rect(item[1], item[2], rect, grow + item[3])
    for (x, y) in ((item[1], item[2]), (item[3], item[4])):
        if _pt_in_rect(x, y, rect, grow + item[5]):
            return True
    # segment crossing the rect without an endpoint inside:
    # sample the midpoint as a cheap conservative extra probe
    mx, my = (item[1] + item[3]) / 2.0, (item[2] + item[4]) / 2.0
    return _pt_in_rect(mx, my, rect, grow + item[5])


def evaluate(entry, board):
    """board: {'layers': [...stack order...],
               'items': {(net, layer): [hvdomains items]},
               'pads': [{ref, net, x, y, w, h, layers}],
               'vias': [{net, x, y, d}],
               'zones': {(net, layer): [[pts], ...]}}
    Returns the per-entry report dict."""
    cfg = resolve(entry)
    iso = cfg['isolation_mm']
    layers = board['layers']
    lidx = {l: i for i, l in enumerate(layers)}
    pads = board['pads']
    if entry.get('ref'):
        own_pads = [p for p in pads if p['ref'] == entry['ref']]
        own_nets = sorted({p['net'] for p in own_pads if p['net']})
    else:
        own_pads = [p for p in pads if p['net'] == entry.get('net')]
        own_nets = [entry['net']]
    own = set(own_nets)
    allowed = own | set(cfg['allow_nets'])
    if cfg['guard_net']:
        allowed.add(cfg['guard_net'])
    quiet_items = []   # (layer, item)
    for n in own_nets:
        for layer in layers:
            for it in board['items'].get((n, layer), []):
                quiet_items.append((layer, it))
    intrusions = []
    seen = set()
    def add(kind, net, layer, at, dist):
        key = (kind, net, layer, round(at[0], 1), round(at[1], 1))
        if key in seen:
            return
        seen.add(key)
        intrusions.append({'kind': kind, 'net': net, 'layer': layer,
                           'at': [round(at[0], 2), round(at[1], 2)],
                           'distance_mm': round(dist, 3)})
    # corridor + adjacent-layer crossings
    for (lq, qi) in quiet_items:
        for net in {n for (n, _l) in board['items']}:
            if net in allowed:
                continue
            for layer in layers:
                if abs(lidx[layer] - lidx[lq]) > 1:
                    continue
                for fi in board['items'].get((net, layer), []):
                    gap, pa, pb = _H._item_dist(qi, fi)
                    if gap < iso:
                        add('corridor' if layer == lq else 'adjacent_layer',
                            net, layer, pb, gap)
    # land-pattern keepout (component entries with the check on)
    land = None
    if cfg['land_keepout'] and own_pads:
        xs = [p['x'] for p in own_pads]
        ys = [p['y'] for p in own_pads]
        r = max(max(p['w'], p['h']) for p in own_pads) / 2.0
        land = [min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r]
        for net in {n for (n, _l) in board['items']}:
            if net in allowed:
                continue
            for layer in layers:
                for fi in board['items'].get((net, layer), []):
                    if _seg_hits_rect(fi, land, LAND_MARGIN_MM):
                        cx = fi[1] if fi[0] == 'disc' else (fi[1] + fi[3]) / 2
                        cy = fi[2] if fi[0] == 'disc' else (fi[2] + fi[4]) / 2
                        add('land_pattern', net, layer, (cx, cy), 0.0)
    # load-cap ground return (crystal profile)
    caps = []
    if cfg['load_cap_return'] and entry.get('ref'):
        gnd = 'GND'
        # load caps hang on the SIGNAL nets; the component's own
        # ground pad makes GND an own net too, and ground-only
        # parts (net-ties) must not be mistaken for caps
        signal_own = own - {gnd}
        by_ref = {}
        for p in pads:
            by_ref.setdefault(p['ref'], []).append(p)
        for ref, ps in sorted(by_ref.items()):
            if ref == entry['ref']:
                continue
            nets_here = {p['net'] for p in ps}
            if not (nets_here & signal_own) or gnd not in nets_here:
                continue
            ok = False
            for p in ps:
                if p['net'] != gnd:
                    continue
                for v in board['vias']:
                    if v['net'] == gnd and math.hypot(
                            v['x'] - p['x'], v['y'] - p['y']) <= CAP_RETURN_VIA_MM:
                        ok = True
                for layer in p.get('layers') or []:
                    for poly in board['zones'].get((gnd, layer), []):
                        if _H._point_in_poly(p['x'], p['y'], poly):
                            ok = True
            caps.append({'ref': ref, 'ground_return': ok})
    # guard continuity: tied at >= 2 distinct locations (#67 rule)
    guard = None
    if cfg['guard_net']:
        g = cfg['guard_net']
        anchor = 'GND'
        # Ties are component-level: a footprint carrying both the
        # guard net and the anchor net (net-tie, 0-ohm, or the
        # guarded component's own ground pin).  A guard-net via
        # never ties to a foreign pour -- it gets a clearance
        # hole -- so vias are not ties.
        ties = []
        by_ref = {}
        for p in pads:
            by_ref.setdefault(p['ref'], set()).add(p['net'])
        for p in pads:
            if p['net'] == g and anchor in by_ref.get(p['ref'], set()):
                ties.append((p['x'], p['y']))
        distinct = []
        for t in ties:
            if all(math.hypot(t[0] - u[0], t[1] - u[1]) > 1.0
                   for u in distinct):
                distinct.append(t)
        guard = {'net': g, 'anchor': anchor, 'ties': len(distinct),
                 'ok': len(distinct) >= 2,
                 'rule': 'a guard counts only if tied at both ends'}
    ok = not intrusions
    if guard is not None and not guard['ok']:
        ok = False
    if any(not c['ground_return'] for c in caps):
        ok = False
    return {'name': cfg['name'], 'profile': cfg['profile'],
            'isolation_mm': iso,
            'isolation_source': cfg['isolation_source'],
            'allow_nets': cfg['allow_nets'], 'own_nets': own_nets,
            'intrusions': intrusions, 'load_caps': caps,
            'guard': guard, 'pass': ok}
