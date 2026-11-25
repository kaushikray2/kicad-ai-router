"""Per-segment power plan for a routed board (#68 Step 2).

Pure module (no pcbnew): given the computed power tree
(ai_router.powertree), per-net copper facts from the worker
(tracks / vias / pads / filled zones), the stackup's copper
layers and the corner set, it attributes design currents to
routed segments, sizes them with ai_router.wcca, and produces:

  * a widen-only action map (track uuid -> required width),
  * via-array additions where a layer transition carries more
    sustained current than its via cluster's barrel capacity,
  * the verification report data: per-segment thermal/drop
    results, per-load full-loop drop, the decoupling checklist,
    pour-carried and unrouted loads, and every assumption made.

Attribution model: for each rail, the copper graph (tracks as
edges, vias as layer hubs, pads attached to the nearest graph
node) is searched from the source pads; each load's cornered
current flows along its shortest path, per operating mode, so
mutually exclusive loads never sum on a shared segment.  Edge
components (regulators/conduits) contribute their transformed
input current at their input pins.  A load with several supply
pins is attributed to its nearest pin's path and flagged --
parallel-pin current sharing is not modelled.

Drop is computed on the full loop: supply path at per-segment
design-peak currents, plus the return path attributed the same
way on the return net.  When the return net has a filled zone,
the return is a stated solid-plane assumption (0 ohm), never a
silent one.  Rails whose currents are incomplete get a
resistances-only treatment: geometry and resistances are
reported, nothing is sized and nothing is widened -- the tool
never invents a current.
"""
import heapq
import math
import re

from . import wcca

_CAP_VALUE = re.compile(r'^\s*\d+(\.\d+)?\s*(p|n|u|\u00b5|m)?F\b', re.IGNORECASE)
_ATTACH_MM = 0.75
_VIA_CLUSTER_MM = 1.0
_CAP_VIA_MM = 1.0


def _q(v):
    return round(v, 2)


class _Graph:
    def __init__(self):
        self.adj = {}      # node -> [(node, edge_id)]
        self.edges = []    # {a, b, kind, uuid, layer, length, width, r_via}

    def _link(self, a, b, eid):
        self.adj.setdefault(a, []).append((b, eid))
        self.adj.setdefault(b, []).append((a, eid))

    def add_edge(self, a, b, **kw):
        eid = len(self.edges)
        e = {'a': a, 'b': b}
        e.update(kw)
        self.edges.append(e)
        self._link(a, b, eid)
        return eid


def _build_graph(facts, copper_layers, r_via):
    g = _Graph()
    nodes = set()
    for t in facts.get('tracks', []):
        a = (_q(t['x1']), _q(t['y1']), t['layer'])
        b = (_q(t['x2']), _q(t['y2']), t['layer'])
        nodes.update((a, b))
        g.add_edge(a, b, kind='track', uuid=t.get('uuid'), layer=t['layer'],
                   length=math.hypot(t['x2'] - t['x1'], t['y2'] - t['y1']),
                   width=t['w'])
    for v in facts.get('vias', []):
        hub = ('VIA', _q(v['x']), _q(v['y']))
        for layer in copper_layers:
            n = (_q(v['x']), _q(v['y']), layer)
            nodes.add(n)
            g.add_edge(n, hub, kind='via', uuid=v.get('uuid'), layer=None,
                       length=0.0, width=None, r_via=r_via / 2.0)
    pad_nodes = {}
    for p in facts.get('pads', []):
        for layer in p.get('layers', []):
            if layer not in copper_layers:
                continue
            n = (_q(p['x']), _q(p['y']), layer)
            nodes.add(n)
            pad_nodes.setdefault((p['ref'], str(p['pin'])), []).append(n)
            best, bd = None, _ATTACH_MM
            for m in list(nodes):
                if m == n or m[2] != layer or m[0] == 'VIA':
                    continue
                d = math.hypot(m[0] - n[0], m[1] - n[1])
                if d < bd:
                    best, bd = m, d
            if best is not None:
                g.add_edge(n, best, kind='attach', uuid=None, layer=layer,
                           length=0.0, width=None)
    return g, pad_nodes


def _dijkstra(g, sources):
    dist = {s: 0.0 for s in sources}
    prev = {}
    pq = [(0.0, s) for s in sources]
    heapq.heapify(pq)
    while pq:
        d, n = heapq.heappop(pq)
        if d > dist.get(n, float('inf')):
            continue
        for (m, eid) in g.adj.get(n, []):
            w = g.edges[eid]['length'] if g.edges[eid]['kind'] == 'track' else 0.0
            nd = d + w
            if nd < dist.get(m, float('inf')):
                dist[m] = nd
                prev[m] = (n, eid)
                heapq.heappush(pq, (nd, m))
    return dist, prev


def _path(prev, node):
    out = []
    while node in prev:
        p, eid = prev[node]
        out.append(eid)
        node = p
    return out


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


def _attribute(g, dist, prev, contributors):
    """contributors: [{id, node, currents: {mode: (peak, sust)}}].
    Returns (edge_currents, paths): edge_currents[eid][mode] =
    [peak, sust]; paths[id] = [eid] or None when unreachable.
    Reachability is dist membership (#82): a node on a disconnected
    island with a stub track is in g.adj but in no dist map -- the
    old prev/adj test handed it an empty path, which the loop-drop
    reporter then read as drop_v 0.0 / ok True."""
    ec = {}
    paths = {}
    for c in contributors:
        if c['node'] is None or c['node'] not in dist:
            paths[c['id']] = None
            continue
        eids = _path(prev, c['node'])
        paths[c['id']] = eids
        for mode, (pk, su) in c['currents'].items():
            if pk == 0 and su == 0:
                continue
            for eid in eids:
                slot = ec.setdefault(eid, {}).setdefault(mode, [0.0, 0.0])
                slot[0] += pk
                slot[1] += su
    return ec, paths


def _design(ec):
    out = {}
    for eid, modes in ec.items():
        out[eid] = (max((v[0] for v in modes.values()), default=0.0),
                    max((v[1] for v in modes.values()), default=0.0))
    return out


def plan_power(tree, parsed, facts_by_net, layers, corners, entries_by_net):
    copper = [n for n, l in layers.items() if l.get('type') == 'copper']
    surface = {copper[0], copper[-1]} if copper else set()
    thickness = {n: layers[n]['thickness_mm'] for n in copper}
    board_t = sum(l.get('thickness_mm', 0.0) for l in layers.values()) or 1.6
    # via barrel resistance: rho * L / A, A the barrel annulus area
    # at a nominal 0.3 mm drill (resistance is drill-insensitive at
    # fixed plating; capacity is computed per actual drill later)
    d0 = 0.3
    area = math.pi * (corners['plating_um'] / 1000.0) * (d0 + corners['plating_um'] / 1000.0)
    rho = wcca.RHO20 * (1 + wcca.ALPHA * (corners['ambient_c'] + corners['delta_t_c'] - 20.0))
    r_via = rho * (board_t / 1000.0) / (area / 1e6)
    modes = tree.get('modes', ['BASE'])
    assumptions = []
    ret_net = tree['return_net']
    ret_facts = facts_by_net.get(ret_net, {})
    plane_return = bool(ret_facts.get('zones'))
    if plane_return:
        assumptions.append('return net %s has a filled zone: solid-plane return '
                           'assumed (0 ohm) for loop drop' % ret_net)
    # return-net attribution (all rails' loads + edge components)
    ret_graph = ret_prev = ret_ec = None
    if not plane_return and ret_facts:
        ret_graph, ret_pads = _build_graph(ret_facts, copper, r_via)
        contribs = []
        for net, rail in tree['rails'].items():
            for ref, load in rail['loads'].items():
                pins = tree['return_pins'].get(ref) or []
                if not pins:
                    continue
                node = _first_node(ret_pads, pins)
                cur = {}
                for m in modes:
                    act = load.get('modes') is None or m in (load.get('modes') or [])
                    cur[m] = (load['peak_cornered_a'], load['sustained_cornered_a']) if act else (0.0, 0.0)
                contribs.append({'id': ref, 'node': node, 'currents': cur})
        for e in tree['edges']:
            pins = tree['return_pins'].get(e['ref']) or []
            if not pins or not e.get('by_mode'):
                continue
            cur = {m: (e['by_mode'][m]['peak_a'], e['by_mode'][m]['sustained_a'])
                   for m in modes if m in e['by_mode']}
            contribs.append({'id': e['ref'], 'node': _first_node(ret_pads, pins),
                             'currents': cur})
        sources = []
        for net, rail in tree['rails'].items():
            for ref in rail['source_pins']:
                for pin in tree['return_pins'].get(ref, []):
                    sources += ret_pads.get((pin[0], str(pin[1])), [])
        ret_dist, ret_prev = _dijkstra(ret_graph, sources)
        ret_ec, ret_paths = _attribute(ret_graph, ret_dist, ret_prev, contribs)
    else:
        ret_paths = {}
    out = {'rails': {}, 'widen': {}, 'via_adds': [], 'assumptions': assumptions,
           'corners': dict(corners), 'modes': modes}
    for net, rail in tree['rails'].items():
        entry = entries_by_net.get(net, {})
        facts = facts_by_net.get(net, {})
        rep = _plan_rail(net, rail, entry, facts, tree, parsed, copper, surface,
                         thickness, corners, r_via, modes,
                         ret_graph, ret_ec, ret_paths, plane_return, ret_net,
                         facts_by_net.get(ret_net, {}), out)
        out['rails'][net] = rep
    return out


def _first_node(pad_nodes, pins):
    for (r, p) in pins:
        ns = pad_nodes.get((r, str(p)))
        if ns:
            return ns[0]
    return None


def _plan_rail(net, rail, entry, facts, tree, parsed, copper, surface,
               thickness, corners, r_via, modes, ret_graph, ret_ec,
               ret_paths, plane_return, ret_net, ret_facts, out):
    rep = {'net': net, 'complete': rail['complete'],
           'design': rail['design'], 'segments': [], 'vias': [],
           'loop_drops': [], 'decoupling': [], 'flags':
           [f for f in tree['flags'] if f.get('net') == net],
           'contributions': [], 'assumptions': []}
    g, pad_nodes = _build_graph(facts, copper, r_via)
    sources = []
    for ref, pins in rail['source_pins'].items():
        for pin in pins:
            sources += pad_nodes.get((pin[0], str(pin[1])), [])
    # #81: two more source kinds source_pins cannot see -- the
    # declared rail root (the external-input connector named in the
    # marking) and the out-pins of incoming tree edges (a fuse, FET
    # switch or passive-pin regulator feeding this rail).  Neither
    # is necessarily a power_out/output-typed pin; without seeding
    # them the rail plans with zero sources and every load on it
    # silently attributes 0 A.
    _root = rail.get('root') or {}
    if _root.get('ref'):
        for (r, _p), ns in pad_nodes.items():
            if r == _root['ref']:
                sources += ns
    for e in tree['edges']:
        if e.get('to') == net:
            for (r, p) in e.get('out_pins', []):
                sources += pad_nodes.get((r, str(p)), [])
    sources = list(dict.fromkeys(sources))
    if rail['root'] and not sources:
        rep['assumptions'].append('rail root is the declared external input; '
                                  'no source pads on this net')
    _dist, prev = _dijkstra(g, sources)
    contribs = []
    for ref, load in rail['loads'].items():
        pins = rail['load_pins'].get(ref, [])
        node = _first_node(pad_nodes, pins)
        if len(pins) > 1:
            rep['flags'].append({'kind': 'multi_pin_attributed_to_nearest',
                                 'net': net, 'ref': ref})
        cur = {}
        for m in modes:
            act = load.get('modes') is None or m in (load.get('modes') or [])
            cur[m] = (load['peak_cornered_a'], load['sustained_cornered_a']) if act else (0.0, 0.0)
        contribs.append({'id': ref, 'node': node, 'currents': cur, 'load': load})
        rep['contributions'].append({
            'ref': ref, 'kind': 'load',
            'source': load.get('source') or 'user',
            'citation': load.get('citation'),
            'peak_cornered_a': load['peak_cornered_a'],
            'sustained_cornered_a': load['sustained_cornered_a'],
            'modes': load.get('modes')})
    for e in tree['edges']:
        if e['from'] != net or not e.get('by_mode'):
            continue
        node = _first_node(pad_nodes, e.get('in_pins', []))
        cur = {m: (e['by_mode'][m]['peak_a'], e['by_mode'][m]['sustained_a'])
               for m in modes if m in e['by_mode']}
        contribs.append({'id': e['ref'], 'node': node, 'currents': cur, 'edge': e})
        rep['contributions'].append({'ref': e['ref'], 'kind': 'edge_input',
                                     'edge_kind': e['kind'], 'source': 'tree'})
    ec, paths = _attribute(g, _dist, prev, contribs)
    design = _design(ec)
    # #136 (B11): a protective edge floors BOTH sides at its
    # rating -- edge_input already floors the input side; the
    # downstream conductor is the copper the fuse protects, so
    # the segments incident to the fuse's downstream pins are
    # sized for at least the rating as well.
    for e in tree['edges']:
        if e.get('to') != net or e['kind'] != 'protective':
            continue
        rating = (e.get('spec') or {}).get('rating_a') or 0.0
        if not rating:
            continue
        src_nodes = set()
        for (r, pp) in e.get('out_pins', []):
            src_nodes.update(pad_nodes.get((r, str(pp)), []))
        for node in src_nodes:
            for (_m, eid) in g.adj.get(node, []):
                if g.edges[eid]['kind'] != 'track':
                    continue
                pk, su = design.get(eid, (0.0, 0.0))
                design[eid] = (max(pk, rating), max(su, rating))
        rep['assumptions'].append(
            'protective %s rating %.3g A floors the segments incident '
            'to its downstream pins (the conductor the fuse protects)'
            % (e['ref'], rating))
    # #83: sizing budget = the rail's declared drop_budget_v (the
    # same one the loop-drop verdict below uses), apportioned by
    # length share against the rail's longest attributed path --
    # size_segment spends its entire budget on the one segment it
    # is given, so an unapportioned call lets an N-segment path
    # consume N times the rail budget.
    rail_budget = entry.get('drop_budget_v') or corners['drop_budget_v']
    _pathlens = [sum(g.edges[i]['length'] for i in _pth
                     if g.edges[i]['kind'] == 'track')
                 for _pth in paths.values() if _pth]
    longest_path = max(_pathlens) if _pathlens else 0.0
    t_op = corners['ambient_c'] + corners['delta_t_c']
    if not rail['complete']:
        rep['resistances_only'] = True
        for e in g.edges:
            if e['kind'] != 'track':
                continue
            rep['segments'].append({
                'uuid': e['uuid'], 'layer': e['layer'],
                'length_mm': round(e['length'], 3), 'width_mm': e['width'],
                'resistance_ohm': round(wcca.segment_resistance_ohm(
                    e['width'], thickness[e['layer']], e['length'], t_op), 5)})
        rep['assumptions'].append('currents incomplete (unknown loads: %s): '
                                  'resistances only, no sizing claim'
                                  % ', '.join(rail['unknown_loads']))
        _decoupling(rep, net, rail, entry, parsed, facts, ret_facts, corners)
        return rep
    # sizing + widen actions (before loop drop, which uses planned widths)
    for e in g.edges:
        if e['kind'] != 'track':
            continue
        rep['segments'].append({'uuid': e['uuid'], 'layer': e['layer'],
                                'length_mm': round(e['length'], 3),
                                'width_mm': e['width']})
    seg_by_uuid = {s['uuid']: s for s in rep['segments']}
    for eid, e in enumerate(g.edges):
        if e['kind'] != 'track':
            continue
        pk, su = design.get(eid, (0.0, 0.0))
        seg = seg_by_uuid[e['uuid']]
        seg['i_peak_a'] = round(pk, 4)
        seg['i_sustained_a'] = round(su, 4)
        if pk > 0 or su > 0:
            _sc = dict(corners)
            _sc['drop_budget_v'] = rail_budget * (
                e['length'] / longest_path if longest_path > 0 else 1.0)
            s = wcca.size_segment(pk, su, e['length'], thickness[e['layer']],
                                  e['layer'] not in surface, _sc)
            if 'error' in s:
                seg['failed'] = s['error']
            else:
                seg['required_width_mm'] = s['width_mm']
                seg['governing'] = s['governing']
                seg['thermal_width_mm'] = s['thermal_width_mm']
                seg['drop_width_mm'] = s['drop_width_mm']
                seg['ok'] = e['width'] >= s['width_mm'] - 1e-9
                if not seg['ok']:
                    seg['shortfall_mm'] = round(s['width_mm'] - e['width'], 3)
                    out['widen'].setdefault(net, {})[e['uuid']] = s['width_mm']
    # via clusters
    via_pts = [(v['x'], v['y'], v.get('drill', 0.3)) for v in facts.get('vias', [])]
    seen = []
    for (x, y, drill) in via_pts:
        if any(math.hypot(x - a, y - b) < _VIA_CLUSTER_MM for (a, b) in seen):
            continue
        seen.append((x, y))
        cluster = sum(1 for (a, b, _d) in via_pts if math.hypot(x - a, y - b) < _VIA_CLUSTER_MM)
        carried = 0.0
        hub = ('VIA', _q(x), _q(y))
        for (m, eid) in g.adj.get(hub, []):
            for (m2, eid2) in g.adj.get(m, []):
                if g.edges[eid2]['kind'] == 'track':
                    carried = max(carried, design.get(eid2, (0.0, 0.0))[1])
        if carried <= 0:
            continue
        needed = wcca.vias_needed(carried, drill, corners['delta_t_c'], corners['plating_um'])
        rep['vias'].append({'x': x, 'y': y, 'drill_mm': drill,
                            'carried_sustained_a': round(carried, 3),
                            'existing': cluster, 'needed': needed,
                            'ok': needed <= cluster})
        if needed > cluster:
            out['via_adds'].append({'net': net, 'x': x, 'y': y,
                                    'add': needed - cluster, 'needed': needed,
                                    'existing': cluster})
    # per-load reachability / pour / full-loop drop at planned widths
    pad_pos = {}
    for p in facts.get('pads', []):
        pad_pos[(p['ref'], str(p['pin']))] = (p['x'], p['y'])
    src_pos = []
    for ref, pins in rail['source_pins'].items():
        if pins and (pins[0][0], str(pins[0][1])) in pad_pos:
            src_pos.append(pad_pos[(pins[0][0], str(pins[0][1]))])
    for c in contribs:
        if 'load' not in c:
            continue
        ref = c['id']
        pins = rail['load_pins'].get(ref, [])
        pos = pad_pos.get((pins[0][0], str(pins[0][1]))) if pins else None
        pth = paths.get(ref)
        if pth is None:
            pour = False
            if pos and src_pos:
                for z in facts.get('zones', []):
                    for poly in z['polys']:
                        if (_point_in_poly(pos[0], pos[1], poly)
                                and any(_point_in_poly(sx, sy, poly) for (sx, sy) in src_pos)):
                            pour = True
            rep['loop_drops'].append({'ref': ref, 'pour_carried': pour,
                                      'unrouted': not pour})
            continue
        best = 0.0
        for m in modes:
            dv = 0.0
            for eid in pth:
                e = g.edges[eid]
                i_pk = ec.get(eid, {}).get(m, [0.0, 0.0])[0]
                if e['kind'] == 'track':
                    wdt = out['widen'].get(net, {}).get(e['uuid'], e['width'])
                    dv += i_pk * wcca.segment_resistance_ohm(
                        wdt, thickness[e['layer']], e['length'], t_op)
                elif e['kind'] == 'via':
                    dv += c['currents'][m][0] * e['r_via']
            rp = ret_paths.get(ref)
            if not plane_return and rp:
                for eid in rp:
                    e = ret_graph.edges[eid]
                    i_pk = ret_ec.get(eid, {}).get(m, [0.0, 0.0])[0]
                    if e['kind'] == 'track':
                        dv += i_pk * wcca.segment_resistance_ohm(
                            e['width'], thickness[e['layer']], e['length'], t_op)
                    elif e['kind'] == 'via':
                        dv += c['currents'][m][0] * e['r_via']
            best = max(best, dv)
        budget = entry.get('drop_budget_v') or corners['drop_budget_v']
        rep['loop_drops'].append({'ref': ref, 'drop_v': round(best, 4),
                                  'budget_v': budget,
                                  'ok': best <= budget,
                                  'return': 'plane' if plane_return else 'traced'})
    _decoupling(rep, net, rail, entry, parsed, facts, ret_facts, corners)
    return rep


def _decoupling(rep, net, rail, entry, parsed, facts, ret_facts, corners):
    # caps on this rail: from the netlist (any component with a node
    # on the rail whose ref/value/footprint reads as a capacitor)
    comp_nets = {}
    for name, nodes in parsed['nets'].items():
        for (r, p) in nodes:
            comp_nets.setdefault(r, {})[name] = p
    comps = parsed['components']
    caps = []
    for ref, nets in comp_nets.items():
        if net not in nets:
            continue
        info = comps.get(ref, {})
        val = info.get('value', '') or ''
        fp = info.get('footprint', '') or ''
        if ref.startswith('C') or _CAP_VALUE.match(val) or 'Capacitor' in fp:
            caps.append((ref, nets[net]))
    pad_pos = {}
    for p in facts.get('pads', []):
        pad_pos[(p['ref'], str(p['pin']))] = (p['x'], p['y'])
    ret_pos = {}
    for p in ret_facts.get('pads', []):
        ret_pos[(p['ref'], str(p['pin']))] = (p['x'], p['y'])
    ret_vias = [(v['x'], v['y']) for v in ret_facts.get('vias', [])]
    budget = entry.get('decoupling_budget_mm') or corners.get('decoupling_budget_mm')
    for ref in rail['loads']:
        pins = rail['load_pins'].get(ref, [])
        pos = pad_pos.get((pins[0][0], str(pins[0][1]))) if pins else None
        item = {'ref': ref, 'cap_present': bool(caps), 'budget_mm': budget}
        if pos and caps:
            best = None
            for (cref, cpin) in caps:
                cp = pad_pos.get((cref, str(cpin)))
                if cp:
                    d = math.hypot(cp[0] - pos[0], cp[1] - pos[1])
                    if best is None or d < best[0]:
                        best = (d, cref)
            if best:
                item['nearest_cap'] = best[1]
                item['distance_mm'] = round(best[0], 2)
                item['within_budget'] = (best[0] <= budget) if budget else None
                item['cap_ground_via'] = None
                item['via_shared_with_load'] = None
                gp = _cap_ground_pos(best[1], net, parsed, ret_pos)
                if gp is not None:
                    near = [v for v in ret_vias if math.hypot(v[0] - gp[0], v[1] - gp[1]) <= _CAP_VIA_MM]
                    item['cap_ground_via'] = bool(near)
                    lp = _cap_ground_pos(ref, net, parsed, ret_pos)
                    if lp is not None and near:
                        item['via_shared_with_load'] = any(
                            math.hypot(v[0] - lp[0], v[1] - lp[1]) <= _CAP_VIA_MM for v in near)
        rep['decoupling'].append(item)



def _cap_ground_pos(ref, rail_net, parsed, ret_pos):
    for name, nodes in parsed['nets'].items():
        if name == rail_net:
            continue
        for (r, p) in nodes:
            if r == ref and (ref, str(p)) in ret_pos:
                return ret_pos[(ref, str(p))]
    return None
