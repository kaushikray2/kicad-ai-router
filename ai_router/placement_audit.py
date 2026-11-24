"""Pre-routing placement audit (#42).

Pure module: given per-footprint facts from the worker (position,
rotation, courtyard, pads, embedded rule-area zones, properties)
plus the library rules for each footprint (ai_router.
library_rules), it produces:

  * the placement report -- the placement-shaped facts routing
    cannot fix, surfaced BEFORE routing starts: keepout zones
    intruded by neighbouring parts, decoupling proximity where
    the footprint encodes it, module/antenna clearance to the
    board edge, and coverage warnings (a part in a class known
    to carry layout rules whose footprint carries none is a
    LIBRARY gap -- the fix belongs in the library);
  * the materialisation list -- library rule-area zones the
    board copy lacks, with absolute outlines, to be written onto
    the pipeline's working copy with their provenance;
  * first_nets advice for RF-class parts' signal nets.

The tool never moves a component.  Distances are pad-to-pad /
courtyard-corner based (draft geometry, stated where it is an
approximation).
"""
import math
import re

from . import library_rules

_CAP_VALUE = re.compile(r'^\s*\d+(\.\d+)?\s*(p|n|u|\u00b5|m)?F\b', re.IGNORECASE)
_GROUND_NETS = ('GND', 'AGND', 'DGND', 'PGND')


def _point_in_poly(x, y, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _transform(fp, outline):
    th = math.radians(fp.get('rot') or 0.0)
    c, s = math.cos(th), math.sin(th)
    return [[fp['x'] + x * c - y * s, fp['y'] + x * s + y * c] for (x, y) in outline]


def _zone_sig(z):
    ko = z.get('keepout') or {}
    return (tuple(sorted(z.get('layers') or [])),
            tuple((k, bool(ko.get(k))) for k in ('tracks', 'vias', 'copperpour')))


def audit(facts, library):
    fps = facts.get('footprints') or []
    by_ref = {f['ref']: f for f in fps}
    report = {'footprints': {}, 'materialise': [], 'first_nets_extra': [],
              'gaps': [], 'unresolved': []}
    for fp in fps:
        ref = fp['ref']
        lib = library.get(fp.get('fpid')) if library else None
        props = dict(fp.get('properties') or {})
        prop_src = 'board' if props else None
        if lib and not props and lib.get('properties'):
            props = dict(lib['properties'])
            prop_src = 'library'
        cls = library_rules.classify(fp.get('fpid'), fp.get('value'), props)
        item = {'fpid': fp.get('fpid'), 'class': cls, 'checks': [],
                'properties_from': prop_src}
        board_zones = fp.get('zones') or []
        lib_zones = (lib or {}).get('zones') or []
        item['zones_board'] = len(board_zones)
        item['zones_library'] = len(lib_zones)
        # materialise library zones the board copy lacks (signature
        # multiset difference)
        have = {}
        for z in board_zones:
            have[_zone_sig(z)] = have.get(_zone_sig(z), 0) + 1
        effective = list(board_zones)
        for z in lib_zones:
            sig = _zone_sig(z)
            if have.get(sig, 0) > 0:
                have[sig] -= 1
                continue
            entry = {'ref': ref, 'fpid': fp.get('fpid'),
                     'layers': z.get('layers') or [],
                     'keepout': z.get('keepout') or {},
                     'outline': _transform(fp, z.get('outline') or []),
                     'provenance': 'library:%s' % fp.get('fpid')}
            report['materialise'].append(entry)
            effective.append({'layers': entry['layers'], 'keepout': entry['keepout'],
                                'outline': entry['outline'], 'materialised': True})
        # 1. keepout intrusions by neighbouring parts
        for z in effective:
            poly = z.get('outline') or []
            if len(poly) < 3:
                continue
            for other in fps:
                if other['ref'] == ref:
                    continue
                hit = any(_point_in_poly(p['x'], p['y'], poly)
                          for p in other.get('pads') or [])
                if not hit and other.get('courtyard'):
                    cx0, cy0, cx1, cy1 = other['courtyard']
                    hit = any(_point_in_poly(cx, cy, poly)
                              for (cx, cy) in ((cx0, cy0), (cx0, cy1), (cx1, cy0), (cx1, cy1)))
                if hit:
                    item['checks'].append({'kind': 'keepout_intrusion',
                                           'owner': ref, 'intruder': other['ref'],
                                           'layers': z.get('layers'),
                                           'materialised_zone': bool(z.get('materialised'))})
        # 2. decoupling proximity (footprint-encoded budget)
        if props.get('AIRuleDecapMm'):
            try:
                budget = float(props['AIRuleDecapMm'])
            except (TypeError, ValueError):
                budget = None
            if budget is not None:
                supply = {p['net'] for p in fp.get('pads') or []
                          if p.get('net') and p['net'] not in _GROUND_NETS}
                best = None
                for other in fps:
                    if other['ref'] == ref:
                        continue
                    oval = other.get('value') or ''
                    if not (other['ref'].startswith('C') or _CAP_VALUE.match(oval)):
                        continue
                    for p in other.get('pads') or []:
                        if p.get('net') not in supply:
                            continue
                        for q in fp.get('pads') or []:
                            if q.get('net') == p.get('net'):
                                d = math.hypot(p['x'] - q['x'], p['y'] - q['y'])
                                if best is None or d < best[0]:
                                    best = (d, other['ref'])
                item['checks'].append({
                    'kind': 'decoupling', 'budget_mm': budget,
                    'nearest_cap': best[1] if best else None,
                    'distance_mm': round(best[0], 2) if best else None,
                    'ok': bool(best) and best[0] <= budget})
        # 3. board-edge clearance (footprint-encoded)
        if props.get('AIRuleEdgeClearanceMm'):
            try:
                budget = float(props['AIRuleEdgeClearanceMm'])
            except (TypeError, ValueError):
                budget = None
            segs = facts.get('outline_segments') or []
            box = fp.get('courtyard')
            if budget is not None and segs and box:
                pts = [(box[0], box[1]), (box[0], box[3]), (box[2], box[1]), (box[2], box[3])]
                d = min(_seg_dist(px, py, s[0], s[1], s[2], s[3])
                        for (px, py) in pts for s in segs)
                item['checks'].append({'kind': 'edge_clearance', 'budget_mm': budget,
                                       'distance_mm': round(d, 2), 'ok': d >= budget})
        # coverage
        has_rules = bool(board_zones or lib_zones or props)
        if cls and not has_rules:
            if lib is not None:
                item['library_gap'] = True
                report['gaps'].append({'ref': ref, 'fpid': fp.get('fpid'), 'class': cls})
            else:
                item['library_unresolved'] = True
                report['unresolved'].append(ref)
        if cls in ('rf_module', 'gnss_module'):
            for p in fp.get('pads') or []:
                n = p.get('net')
                if n and n not in _GROUND_NETS and n not in report['first_nets_extra']:
                    report['first_nets_extra'].append(n)
        report['footprints'][ref] = item
    return report
