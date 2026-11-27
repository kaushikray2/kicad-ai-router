"""RF gate/warn evaluation for impedance-marked nets (issue #67 Step 2).

Pure Python: the worker (worker.py `rfgeom`) extracts board facts with
pcbnew; this module turns facts into findings under the ticket's
design-rules annex (GATE-1..12, WARN-1..8).  Severity vocabulary:
'fail' (a GATE rule is violated), 'warn' (a WARN rule, report only),
'ok' / 'na' are never emitted as findings -- absence of a finding is
the pass.  Every finding is {'rule', 'severity', 'detail'} with an
optional 'at': [x, y] board location in mm.

Fact shapes (mm, board coordinates):
  segments: [{'layer', 'x1', 'y1', 'x2', 'y2', 'w', 'len'}]
  arcs:     [{'layer', 'sx', 'sy', 'ex', 'ey', 'w', 'len'}]
  vias:     [{'x', 'y'}]
  pads:     [{'ref', 'num', 'x', 'y', 'layers': [...], 'thru': bool}]
"""
import math
from collections import defaultdict

_EPS = 1e-9


def _key(x, y):
    return (round(x, 3), round(y, 3))


# ---------------------------------------------------------------------------
# Path graph: joints (angles), stubs, branches, components, distances.
# ---------------------------------------------------------------------------

class PathGraph:
    """Track graph of one net.  Nodes are (x, y, layer); vias and
    through-hole pads link the layer-nodes at their position."""

    def __init__(self, segments, arcs, vias, pads):
        self.edges = []          # (node_a, node_b, length, kind, width, layer)
        self.adj = defaultdict(list)   # node -> [edge indices]
        self.via_pos = {_key(v['x'], v['y']) for v in vias}
        self.pad_at = {}         # (poskey, layer) -> pad dict
        self.pad_nodes = defaultdict(list)  # 'REF.NUM' -> [nodes]
        for p in pads:
            pk = _key(p['x'], p['y'])
            for layer in p.get('layers') or []:
                node = (pk[0], pk[1], layer)
                self.pad_at[(pk, layer)] = p
                self.pad_nodes['%s.%s' % (p['ref'], p['num'])].append(node)
        def add_edge(a, b, length, kind, width, layer):
            if a == b:
                return
            idx = len(self.edges)
            self.edges.append((a, b, length, kind, width, layer))
            self.adj[a].append(idx)
            self.adj[b].append(idx)
        for s in segments or []:
            a = (round(s['x1'], 3), round(s['y1'], 3), s['layer'])
            b = (round(s['x2'], 3), round(s['y2'], 3), s['layer'])
            ln = s.get('len') or math.hypot(s['x2'] - s['x1'], s['y2'] - s['y1'])
            add_edge(a, b, ln, 'seg', s.get('w'), s['layer'])
        for s in arcs or []:
            a = (round(s['sx'], 3), round(s['sy'], 3), s['layer'])
            b = (round(s['ex'], 3), round(s['ey'], 3), s['layer'])
            ln = s.get('len') or math.hypot(s['ex'] - s['sx'], s['ey'] - s['sy'])
            add_edge(a, b, ln, 'arc', s.get('w'), s['layer'])
        # links: vias and thru pads join every layer-node at a position
        by_pos = defaultdict(set)
        for node in list(self.adj.keys()) + [n for ns in self.pad_nodes.values() for n in ns]:
            by_pos[(node[0], node[1])].add(node)
        for pos, nodes in by_pos.items():
            linked = (pos in self.via_pos) or any(
                (self.pad_at.get((pos, n[2])) or {}).get('thru') for n in nodes)
            if not linked or len(nodes) < 2:
                continue
            nodes = sorted(nodes)
            for i in range(len(nodes) - 1):
                add_edge(nodes[i], nodes[i + 1], 0.0, 'link', None, None)

    def _other(self, eidx, node):
        a, b = self.edges[eidx][0], self.edges[eidx][1]
        return b if a == node else a

    def track_degree(self, node):
        return sum(1 for i in self.adj[node] if self.edges[i][3] in ('seg', 'arc'))

    def is_junction(self, node):
        """Node terminates a chain walk: pad, via, or linked position."""
        pos = (node[0], node[1])
        if pos in self.via_pos:
            return True
        if (pos, node[2]) in self.pad_at:
            return True
        return any(self.edges[i][3] == 'link' for i in self.adj[node])

    def _walk(self, start, first_eidx):
        """Walk a chain from `start` along edge first_eidx.  Returns
        (length, end_node, end_kind): end_kind in 'pad'|'via'|'branch'|
        'dangling'|'junction'."""
        total = self.edges[first_eidx][2]
        prev = first_eidx
        cur = self._other(first_eidx, start)
        for _ in range(100000):
            if self.is_junction(cur):
                pos = (cur[0], cur[1])
                if (pos, cur[2]) in self.pad_at:
                    return total, cur, 'pad'
                return total, cur, 'via'
            nxt = [i for i in self.adj[cur]
                   if i != prev and self.edges[i][3] in ('seg', 'arc')]
            if not nxt:
                return total, cur, 'dangling'
            if len(nxt) > 1:
                return total, cur, 'branch'
            total += self.edges[nxt[0]][2]
            prev = nxt[0]
            cur = self._other(nxt[0], cur)
        return total, cur, 'branch'

    def stubs(self):
        """Dangling chains: [{'len', 'at': (x, y), 'layer', 'kind'}].
        kind 'stub' ends at a junction/branch; 'isolated' is a free
        fragment with two dangling ends (reported once)."""
        out = []
        seen = set()
        for node in list(self.adj.keys()):
            if self.track_degree(node) != 1 or self.is_junction(node):
                continue
            eidx = next(i for i in self.adj[node] if self.edges[i][3] in ('seg', 'arc'))
            if eidx in seen:
                continue
            ln, end, kind = self._walk(node, eidx)
            # mark every edge of the chain as seen (#95: only the
            # first edge was marked, so a multi-segment isolated
            # fragment was walked and reported again from its other
            # dangling end).  Re-traverse with _walk's own rules.
            seen.add(eidx)
            _prev, _cur = eidx, self._other(eidx, node)
            for _ in range(100000):
                if self.is_junction(_cur):
                    break
                _nxt = [i for i in self.adj[_cur]
                        if i != _prev and self.edges[i][3] in ('seg', 'arc')]
                if len(_nxt) != 1:
                    break
                seen.add(_nxt[0])
                _prev, _cur = _nxt[0], self._other(_nxt[0], _cur)
            out.append({'len': ln, 'at': (node[0], node[1]), 'layer': node[2],
                        'kind': 'isolated' if kind == 'dangling' else 'stub'})
        return out

    def branches(self):
        """Branch points (degree >= 3 track nodes, not pad/via):
        [{'at', 'layer', 'extra_arms': [lengths of arms beyond the two
        longest]}]."""
        out = []
        for node in list(self.adj.keys()):
            if self.track_degree(node) < 3 or self.is_junction(node):
                continue
            arms = []
            for i in self.adj[node]:
                if self.edges[i][3] not in ('seg', 'arc'):
                    continue
                ln, _end, kind = self._walk(node, i)
                if kind in ('pad', 'via'):
                    arms.append(ln)
            if len(arms) >= 3:
                arms.sort(reverse=True)
                out.append({'at': (node[0], node[1]), 'layer': node[2],
                            'extra_arms': arms[2:]})
        return out

    def joints(self):
        """Two-track joints on one layer: [{'at', 'layer', 'interior_deg',
        'arced', 'widths'}].  Interior 180 = straight; 90 = right corner."""
        out = []
        for node in list(self.adj.keys()):
            inc = [i for i in self.adj[node] if self.edges[i][3] in ('seg', 'arc')]
            if len(inc) != 2 or self.is_junction(node):
                continue
            (a1, b1, _l1, k1, w1, _ly), (a2, b2, _l2, k2, w2, _ly2) = (
                self.edges[inc[0]], self.edges[inc[1]])
            o1 = b1 if a1 == node else a1
            o2 = b2 if a2 == node else a2
            v1 = (o1[0] - node[0], o1[1] - node[1])
            v2 = (o2[0] - node[0], o2[1] - node[1])
            n1 = math.hypot(*v1); n2 = math.hypot(*v2)
            if n1 < _EPS or n2 < _EPS:
                continue
            cosang = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
            out.append({'at': (node[0], node[1]), 'layer': node[2],
                        'interior_deg': math.degrees(math.acos(cosang)),
                        'arced': 'arc' in (k1, k2),
                        'widths': (w1, w2)})
        return out

    def components(self):
        """Union-find component id per node."""
        parent = {}
        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for a, b, _l, _k, _w, _ly in self.edges:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        return {n: find(n) for n in self.adj.keys()}

    def distance(self, nodes_a, nodes_b):
        """Shortest copper-path distance (Dijkstra) between two node
        sets, or None when unconnected."""
        import heapq
        targets = set(nodes_b)
        dist = {n: 0.0 for n in nodes_a}
        pq = [(0.0, n) for n in nodes_a]
        heapq.heapify(pq)
        seen = set()
        while pq:
            d, n = heapq.heappop(pq)
            if n in seen:
                continue
            seen.add(n)
            if n in targets:
                return d
            for i in self.adj[n]:
                m = self._other(i, n)
                nd = d + self.edges[i][2]
                if nd < dist.get(m, float('inf')):
                    dist[m] = nd
                    heapq.heappush(pq, (nd, m))
        return None


def pad_component_map(graph):
    """'REF.NUM' -> component id for every pad touching the graph."""
    comp = graph.components()
    out = {}
    for name, nodes in graph.pad_nodes.items():
        ids = {comp[n] for n in nodes if n in comp}
        if ids:
            out[name] = sorted(ids)[0]
    return out


# ---------------------------------------------------------------------------
# Rule evaluation.  ctx keys (all optional unless noted):
#   band ('SUB'|'MID'|'HIGH'), freq_hz, stub_limit_mm, return_ceiling_mm,
#   width_mm (applied width), w_over_lambda_g, est_loss_db, length_mm,
#   loss_budget_db, role, finish, mask {'intent','correction_applied','h_mm'},
#   refs {layer: {'ref_layer', 'pour_nets'}} 
#   z0_ohm, tol_pct, layer_z0 {layer: tlcalc callable w -> result}
#   (the #67 tolerance band; GATE-13)
# facts keys (worker rfgeom): segments, arcs, vias, pads, return_vias
#   [{'nearest_mm', 'within'}] aligned with vias, corridor {layer:
#   {'severed': [...], 'centre_gaps': [...]}}, foreign_under,
#   floating_islands, keepout_intrusions, gnd_pad_connections,
#   pi_presence {ref: {'exists', 'on_net'}}
# marking keys: pi {'radio': [refs], 'antenna': [refs], 'radio_ref': ref}
# ---------------------------------------------------------------------------

def _f(rule, severity, detail, at=None):
    d = {'rule': rule, 'severity': severity, 'detail': detail}
    if at is not None:
        d['at'] = [round(at[0], 2), round(at[1], 2)]
    return d


def evaluate_net(marking, ctx, facts):
    out = []
    segs = facts.get('segments') or []
    arcs = facts.get('arcs') or []
    vias = facts.get('vias') or []
    pads = facts.get('pads') or []
    band = ctx.get('band')
    if not segs and not arcs:
        return [_f('RF', 'na', 'marked net has no routed copper to check')]
    graph = PathGraph(segs, arcs, vias, pads)
    stub_limit = ctx.get('stub_limit_mm')
    # --- GATE-3: stubs and branches (threshold lambda_g/20 at band top)
    stubs = graph.stubs()
    for s in stubs:
        if stub_limit and s['len'] > stub_limit:
            out.append(_f('GATE-3', 'fail',
                          '%s of %.2f mm exceeds the lambda_g/20 stub limit %.2f mm'
                          % (s['kind'], s['len'], stub_limit), s['at']))
        elif (band == 'HIGH' and (marking or {}).get('role') == 'tx'
                and stub_limit and s['len'] > stub_limit / 2.0):
            out.append(_f('WARN-8', 'warn',
                          'stub of %.2f mm exceeds lambda_g/20 at the 2nd harmonic (%.2f mm)'
                          % (s['len'], stub_limit / 2.0), s['at']))
    for br in graph.branches():
        for arm in br['extra_arms']:
            if stub_limit and arm > stub_limit:
                out.append(_f('GATE-3', 'fail',
                              'branch arm of %.2f mm exceeds the lambda_g/20 limit %.2f mm'
                              % (arm, stub_limit), br['at']))
    # --- GATE-10 + WARN-5: width variation, acute angles, bare corners
    width = ctx.get('width_mm')
    if width:
        tol = max(0.02, 0.04 * width)
        bad = []
        for it in segs + arcs:
            w = it.get('w')
            if w and abs(w - width) > tol:
                bad.append(it)
        for it in bad[:8]:
            x = it.get('x1', it.get('sx')); y = it.get('y1', it.get('sy'))
            out.append(_f('GATE-10', 'fail',
                          'width %.3f mm differs from the marked width %.3f mm (neck-down / step)'
                          % (it['w'], width), (x, y)))
        if len(bad) > 8:
            out.append(_f('GATE-10', 'fail', '+ %d more width deviations' % (len(bad) - 8)))
    wl = ctx.get('w_over_lambda_g')
    corner_warn_at = {'MID': 0.05, 'HIGH': 0.03}.get(band)
    corners = 0
    corner_at = None
    for j in graph.joints():
        ang = j['interior_deg']
        if ang < 89.0 and not j['arced']:
            out.append(_f('GATE-10', 'fail',
                          'acute interior angle %.0f deg on a marked net' % ang, j['at']))
        elif 85.0 <= ang <= 95.0 and not j['arced']:
            corners += 1
            corner_at = corner_at or j['at']
    if corners and corner_warn_at is not None and wl and wl > corner_warn_at:
        out.append(_f('WARN-5', 'warn',
                      '%d unmitred 90 deg corner(s); W/lambda_g %.3f is past the %.2f measurability onset for %s'
                      % (corners, wl, corner_warn_at, band), corner_at))
    # --- GATE-4 / GATE-5: return structure at layer changes
    ceiling = ctx.get('return_ceiling_mm')
    for v, rv in zip(vias, facts.get('return_vias') or []):
        nearest = rv.get('nearest_mm')
        if ceiling and (nearest is None or nearest > ceiling):
            if nearest is not None and nearest <= 2.0 * ceiling:
                out.append(_f('GATE-5', 'fail',
                              'nearest ground return via %.2f mm from a feed via exceeds the %.2f mm ceiling'
                              % (nearest, ceiling), (v['x'], v['y'])))
            else:
                out.append(_f('GATE-4', 'fail',
                              'feed via has no ground return via within %.2f mm (nearest %s)'
                              % (ceiling, ('%.2f mm' % nearest) if nearest is not None else 'none on board'),
                              (v['x'], v['y'])))
    # --- GATE-1: reference-corridor severance
    for layer, cor in (facts.get('corridor') or {}).items():
        for run in cor.get('severed') or []:
            out.append(_f('GATE-1', 'fail',
                          'reference corridor fully severed under the feed on %s (run %.2f mm)'
                          % (layer, run['len_mm']), (run['x'], run['y'])))
        sev_at = [(run['x'], run['y']) for run in cor.get('severed') or []]
        for run in cor.get('centre_gaps') or []:
            if any(abs(run['x'] - sx) < 0.51 and abs(run['y'] - sy) < 0.51 for (sx, sy) in sev_at):
                continue  # already reported as a full severance above
            if run['len_mm'] > 2.0:
                out.append(_f('GATE-1', 'fail',
                              'feed crosses a %.2f mm gap in its reference plane on %s'
                              % (run['len_mm'], layer), (run['x'], run['y'])))
            elif run['len_mm'] > 0.3:
                out.append(_f('GATE-1', 'warn',
                              'centreline reference gap of %.2f mm on %s (single-antipad scale; corridor edges intact)'
                              % (run['len_mm'], layer), (run['x'], run['y'])))
    # --- GATE-13: per-layer Z0 tolerance band (tol_pct, #67).
    # GATE-10 above is a pure geometric width check; the band
    # check is physical: the same width is a different impedance
    # on a different layer (microstrip vs stripline), so a net
    # that transitions layers at the primary width can pass
    # GATE-10 and still be out of band where it actually runs.
    z0t, tolp = ctx.get('z0_ohm'), ctx.get('tol_pct')
    fns = ctx.get('layer_z0') or {}
    if z0t and tolp and fns:
        lo = z0t * (1.0 - tolp / 100.0)
        hi = z0t * (1.0 + tolp / 100.0)
        seen = {}
        for it in segs + arcs:
            if it.get('layer') in fns and it.get('w'):
                seen.setdefault((it['layer'], round(it['w'], 4)), it)
        for (layer, w), it in sorted(seen.items()):
            try:
                z = fns[layer](w).z0
            except Exception:
                continue
            if z < lo - 1e-9 or z > hi + 1e-9:
                out.append(_f('GATE-13', 'fail',
                              'on %s the routed width %.3f mm gives '
                              'Z0 %.1f ohm, outside the %.1f ohm '
                              '+/-%.4g%% band [%.1f, %.1f]'
                              % (layer, w, z, z0t, tolp, lo, hi),
                              (it.get('x1', it.get('sx')),
                               it.get('y1', it.get('sy')))))

    # --- GATE-11: aggressors under the corridor / vias in the RF area
    for fu in (facts.get('foreign_under') or [])[:8]:
        out.append(_f('GATE-11', 'fail',
                      '%s %s of net %s inside the reference corridor on %s'
                      % (fu['kind'], 'runs under the feed' if fu['kind'] == 'track' else 'surfaces in the RF area',
                         fu['net'], fu['layer']), (fu['x'], fu['y'])))
    # --- GATE-6: floating copper adjacent to the feed
    for fi in (facts.get('floating_islands') or [])[:8]:
        out.append(_f('GATE-6', 'fail',
                      'floating copper island (net %s, %s) adjacent to the feed, connected to no pad'
                      % (fi['net'], fi['layer']), (fi['x'], fi['y'])))
    # --- GATE-2: antenna keep-out intrusion
    for ki in (facts.get('keepout_intrusions') or [])[:8]:
        out.append(_f('GATE-2', 'fail',
                      '%s of net %s intrudes into the keep-out of %s'
                      % (ki['kind'], ki['net'], ki['footprint']), (ki['x'], ki['y'])))
    # --- GATE-8: thermal-relief spokes on RF-adjacent ground pads
    thermal = [g for g in (facts.get('gnd_pad_connections') or [])
               if g.get('connection') == 'thermal']
    if thermal:
        out.append(_f('GATE-8', 'fail',
                      'thermal-relief connection on RF-adjacent ground pad(s): %s'
                      % ', '.join('%s.%s' % (g['ref'], g['num']) for g in thermal[:6])))
    # --- GATE-7 / WARN-6: pi-match presence, segment, displacement
    pi = (marking or {}).get('pi') or {}
    presence = facts.get('pi_presence') or {}
    declared = [('radio', r) for r in pi.get('radio') or []] + \
               [('antenna', r) for r in pi.get('antenna') or []]
    missing = [(kind, r) for kind, r in declared
               if not (presence.get(r) or {}).get('exists')
               or not (presence.get(r) or {}).get('on_net')]
    for kind, r in missing:
        out.append(_f('GATE-7', 'fail',
                      'declared %s pi-match footprint %s is missing or not on the marked net' % (kind, r)))
    if pi.get('radio') and pi.get('antenna') and not missing:
        comp = pad_component_map(graph)
        def _comp_of(refs):
            ids = set()
            for name, cid in comp.items():
                if name.rsplit('.', 1)[0] in refs:
                    ids.add(cid)
            return ids
        rc, ac = _comp_of(set(pi['radio'])), _comp_of(set(pi['antenna']))
        if rc and ac and not (rc & ac):
            out.append(_f('GATE-7', 'fail',
                          'the 50 ohm segment between the radio and antenna pi-matches is not continuous'))
        radio_ref = pi.get('radio_ref')
        if radio_ref:
            src = [n for name, ns in graph.pad_nodes.items()
                   if name.rsplit('.', 1)[0] == radio_ref for n in ns]
            def _dist_to(refs):
                tgt = [n for name, ns in graph.pad_nodes.items()
                       if name.rsplit('.', 1)[0] in refs for n in ns]
                return graph.distance(src, tgt) if src and tgt else None
            d_radio = _dist_to(set(pi['radio']))
            d_ant = _dist_to(set(pi['antenna']))
            if d_radio is not None and d_ant is not None and d_radio > d_ant:
                out.append(_f('WARN-6', 'warn',
                              'radio pi-match is %.1f mm of copper from %s but the antenna pi-match is %.1f mm -- the matches look displaced from their ends'
                              % (d_radio, radio_ref, d_ant)))
    # --- GATE-9: mask-treatment mismatch (calculator vs declared intent)
    mask = ctx.get('mask') or {}
    intent = mask.get('intent')
    if intent == 'open' and mask.get('correction_applied'):
        out.append(_f('GATE-9', 'fail',
                      'width was calculated with the flooded-mask correction but the marking declares the feed mask-open'))
    elif (intent == 'covered' and mask.get('h_mm') is not None
            and mask['h_mm'] <= 0.25 and not mask.get('correction_applied')):
        out.append(_f('GATE-9', 'fail',
                      'marking declares a mask-covered feed at h = %.2f mm but no mask correction was applied in sizing'
                      % mask['h_mm']))
    # --- GATE-12: reference must be a solid ground pour
    for layer, r in (ctx.get('refs') or {}).items():
        pours = r.get('pour_nets') or []
        if not pours:
            out.append(_f('GATE-12', 'fail',
                          'feed on %s has no pour on its reference layer %s (no solid reference)'
                          % (layer, r.get('ref_layer'))))
        elif not any('GND' in p.upper() for p in pours):
            out.append(_f('GATE-12', 'fail',
                          'reference for %s is %s carrying %s -- a power/signal pour, not ground'
                          % (layer, r.get('ref_layer'), ', '.join(pours))))
    # --- WARN-1: feed loss against budget
    est = ctx.get('est_loss_db')
    if est is not None:
        budget = ctx.get('loss_budget_db') or 0.5
        lna = (marking or {}).get('role') in ('gnss_lna', 'lna_input')
        if est > budget:
            out.append(_f('WARN-1', 'warn',
                          'estimated feed loss %.2f dB exceeds the %.2f dB budget -- shorten the feed or use a lower-Df laminate'
                          % (est, budget)))
        elif lna and est > 0.2:
            out.append(_f('WARN-1', 'warn',
                          'estimated feed loss %.2f dB on an LNA-input feed (0.2 dB warn level; loss here is noise figure)' % est))
        elif band == 'MID' and est > 0.3:
            out.append(_f('WARN-1', 'warn', 'estimated feed loss %.2f dB exceeds 0.3 dB' % est))
        elif band == 'HIGH' and est > 0.5:
            out.append(_f('WARN-1', 'warn',
                          'estimated feed loss %.2f dB at 3-6 GHz -- a low-loss laminate is the usual remedy' % est))
    # --- WARN-2: ENIG finish on the RF path (never a precise number)
    finish = (ctx.get('finish') or '')
    length = ctx.get('length_mm') or 0.0
    if finish and ('ENIG' in finish.upper() or 'IMMERSION GOLD' in finish.upper()):
        lna = (marking or {}).get('role') in ('gnss_lna', 'lna_input')
        if band == 'HIGH' or (band == 'MID' and (lna or length > 20.0)):
            f2 = _f('WARN-2', 'warn',
                    'ENIG finish on the RF path: nickel carries a growing share of the current above ~1 GHz and adds loss the calculator does not model')
            if band == 'HIGH' or length > 20.0:
                f2['gate_visible'] = True
            out.append(f2)
    # --- WARN-3: roughness excluded from the loss estimate
    if band == 'HIGH' and length > 20.0:
        out.append(_f('WARN-3', 'warn',
                      'loss estimate excludes conductor roughness (no fab foil parameters); on ED foil the true loss is higher'))
    # --- WARN-4: glass weave under a narrow high-band trace
    if (ctx.get('freq_hz') or 0) >= 3e9 and width and width < 0.2:
        out.append(_f('WARN-4', 'warn',
                      'trace width %.3f mm at >= 3 GHz: glass-weave Dk variation can scatter impedance a few percent -- spread-glass style, a wider trace, or angled routing belong in the fab note' % width))
    # --- WARN-7: long sub-GHz feed
    if band == 'SUB' and length > 50.0:
        out.append(_f('WARN-7', 'warn',
                      'sub-GHz feed is %.0f mm long -- check loss and antenna placement' % length))
    return out
