"""pcbnew worker for the AI Router plugin. Runs as a subprocess under the
system python3 (which can import pcbnew on the host). Every subcommand
prints exactly one JSON object on the last stdout line.

Subcommands: strip, summary, scorecard, finish_leg, stitch"""
import sys, os, json, math, argparse, heapq
import numpy as np
import pcbnew

# v1.3.2 layer-law plumbing: the pipeline passes the run's banned
# layers (--no-tracks-on, canonical names); Grid excludes them from
# routable. Default when the flag is absent: In1.Cu only.
BANNED_LAYER_IDS = None
NO_TRACKS_ON_RAW = None
NO_TRACKS_ON_UNRESOLVED = []  # v1.9 (M4): --no-tracks-on names the canonical table could not resolve; Grid resolves them against the actual board or fails loud
# v1.11 (#73): --layer-cost LAYER=FACTOR pairs; Grid prices travel
# on a priced layer at its factor instead of excluding the layer.
# A priced layer is NOT banned (the price wins over a ban).
LAYER_COST_RAW = None
LAYER_COST_BY_ID = {}
LAYER_COST_UNRESOLVED = []  # (name, factor) pairs the canonical table could not resolve; Grid resolves them against the actual board or fails loud, same discipline as bans
# v1.12 (#63): --skeleton turns on the pour-skeleton reservation:
# Grid stamps each pour's pad-spanning-tree corridors as hard
# obstacles for foreign nets, and the summary carries the intrusion
# census the pipeline gate counts as router-added faults. Absent =
# off, and every leg command line is then exactly what it was.
SKELETON_ON = False
SKELETON_SLACK_MM = 0.1  # skeleton width = zone min-thickness + slack

def _layer_id_from_name(n):
    n = n.strip()
    if n == 'F.Cu': return pcbnew.F_Cu
    if n == 'B.Cu': return pcbnew.B_Cu
    if n.startswith('In') and n.endswith('.Cu'):
        try: return getattr(pcbnew, 'In%d_Cu' % int(n[2:-3]))
        except Exception: return None
    return None

def _parse_no_tracks_raw(spec):
    # v1.11.2 (#104): split a --no-tracks-on value into (canonical
    # layer ids, unresolved names). Empty tokens are skipped:
    # ''.split(',') is [''], and an empty token would otherwise land
    # in the unresolved list and make Grid.__init__ raise on a spec
    # that simply means 'ban nothing' (a trailing comma, 'In1.Cu,',
    # hit the same crash before this helper existed).
    banned = set(); unresolved = []
    for _x in spec.split(','):
        _x = _x.strip()
        if not _x: continue
        _i = _layer_id_from_name(_x)
        if _i is None: unresolved.append(_x)
        else: banned.add(_i)
    return banned, unresolved

def _parse_layer_cost_raw(spec):
    # v1.11.2 (#105): split a --layer-cost value into (factor by
    # canonical layer id, unresolved (name, factor) pairs), applying
    # the guards the pipeline's parse_layer_cost enforces --
    # non-empty name, digits-only factor, 1..1000 inclusive. Before
    # this, the worker accepted any integer int() would parse: 0
    # (travel on a 'priced' layer becomes free), negatives (negative
    # A* step costs) and >1000 all reached Grid._lfac when a spec
    # arrived without passing through the pipeline first. A
    # guarded-out token prices nothing, the worker's standing salvage
    # rule for unparseable tokens.
    by_id = {}; unresolved = []
    for _tok in spec.split(','):
        _nm, _eq, _fc = _tok.partition('=')
        _nm = _nm.strip(); _fc = _fc.strip()
        if not _nm or not _fc.isdigit(): continue
        _f = int(_fc)
        if not (1 <= _f <= 1000): continue
        _i = _layer_id_from_name(_nm)
        if _i is None: unresolved.append((_nm, _f))
        else: by_id[_i] = _f
    return by_id, unresolved

def _resolve_banned_on_board(b, names):
    # v1.9 (M4): resolve non-canonical --no-tracks-on names against
    # THIS board. A board may name its copper layers freely (the
    # one in-house board calls In1.Cu 'GND' in-file) and a config may use either
    # the canonical name or the board's own. Matches board layer
    # names and positional canonical names, case-insensitively.
    # Returns (ids, still_unresolved).
    stack = copper_stack(b)
    canon = {}
    for k, lid in enumerate(stack):
        canon[b.GetLayerName(lid).strip().lower()] = lid
        cname = 'F.Cu' if k == 0 else ('B.Cu' if k == len(stack) - 1 else 'In%d.Cu' % k)
        canon[cname.lower()] = lid
    ids, still = set(), []
    for n in names:
        lid = canon.get(n.strip().lower())
        if lid is None: still.append(n)
        else: ids.add(lid)
    return ids, still

def mm(v): return v / 1e6
def nm(x): return int(round(x * 1e6))
def out(obj):
    print('AIR_RESULT ' + json.dumps(obj))

def refill(b):
    f = pcbnew.ZONE_FILLER(b)
    f.Fill(b.Zones())

# ---------------------------------------------------------------- summary
def cmd_summary(board_path, fast=False):
    b = pcbnew.LoadBoard(board_path)
    refill(b)
    conn = b.GetConnectivity(); conn.Build(b)
    nets = {}
    for fp in b.GetFootprints():
        for p in fp.Pads():
            n = p.GetNetname()
            if not n: continue
            nets.setdefault(n, []).append(p)
    result = []
    for n, pads in sorted(nets.items()):
        # component counting: pads sharing a connectivity cluster
        seen = []
        comp = 0
        pad_ids = set()
        for p in pads:
            items = conn.GetConnectedItems(p)
            ids = set()
            for it in items:
                if it.Type() == pcbnew.PCB_PAD_T:
                    ids.add((round(mm(it.GetPosition().x), 2), round(mm(it.GetPosition().y), 2)))
            key = (round(mm(p.GetPosition().x), 2), round(mm(p.GetPosition().y), 2))
            if not any(key in s for s in seen):
                comp += 1
                seen.append(ids)
        result.append({'net': n, 'pads': len(pads), 'components': comp})
    tracks = list(b.GetTracks())
    pour_nets = sorted({z.GetNetname() for z in b.Zones() if z.GetNetname()})
    ncu = b.GetCopperLayerCount()
    copper_layers = (['F.Cu'] + ['In%d.Cu' % i for i in range(1, ncu - 1)] + ['B.Cu']) if ncu > 1 else ['F.Cu']
    # v1.3.2 layer census: tracks per canonical layer. The gate turns
    # this into banned_layer_tracks -- DRC alone cannot see a track
    # drawn on a plane layer (the pour refills around it).
    _stack = copper_stack(b); _lidx = {lay: k for k, lay in enumerate(_stack)}
    # v1.10.1 (#49): alias map (board layer name / canonical name,
    # lowercased -> canonical) so the pipeline resolves the layer law
    # ONCE against this board instead of each consumer string-matching
    # the config and silently dropping non-canonical names.
    layer_aliases = {}
    for _k, _lid in enumerate(_stack):
        layer_aliases[b.GetLayerName(_lid).strip().lower()] = copper_layers[_k]
        layer_aliases[copper_layers[_k].lower()] = copper_layers[_k]
    tracks_by_layer = {}
    track_length_by_layer = {}  # v1.11.4 (#115): per-layer track LENGTH beside the counts -- the gate prices a layer from length; counts cannot
    tracks_by_layer_net = {}
    for t in tracks:
        if t.Type() == pcbnew.PCB_VIA_T: continue
        _li = _lidx.get(t.GetLayer())
        if _li is None: continue  # v1.9.6 (#40): off-stack layer (external-router boards) -- skip, do not KeyError
        _cn = copper_layers[_li]
        tracks_by_layer[_cn] = tracks_by_layer.get(_cn, 0) + 1
        track_length_by_layer[_cn] = track_length_by_layer.get(_cn, 0.0) + mm(t.GetLength())
        _d = tracks_by_layer_net.setdefault(_cn, {})
        _nn = t.GetNetname()
        _d[_nn] = _d.get(_nn, 0) + 1
    # v1.5.1 census fix: each pour net's own plane layers (from its
    # zones), so the gate can exempt a pour net's tracks on its OWN
    # plane from banned_layer_tracks -- heal bridges there are the
    # legal v1.3.2 exception, and the raw census counted 105 of them
    # as breaches across the dataset-v3 grid (all /5V_IN, +5V, +48V
    # bridges on boards whose In1 is a power plane).
    pour_layers = {}
    for z in b.Zones():
        _zn = z.GetNetname()
        if not _zn: continue
        try: _zlays = list(z.GetLayerSet().CuStack())
        except Exception: _zlays = [z.GetLayer()]
        for _zl in _zlays:
            if _zl in _lidx:
                pour_layers.setdefault(_zn, set()).add(copper_layers[_lidx[_zl]])
    pour_layers = {k: sorted(v) for k, v in pour_layers.items()}
    # v1.12 (#63): the gate's skeleton census -- foreign copper in
    # reserved corridors, counted by the pipeline as router-added
    # faults. Only when the run turned the reservation on; the keys
    # are absent otherwise and old consumers see the old summary.
    if SKELETON_ON:
        _sk_items, _sk_mm, _sk_per = skeleton_intrusions(b)
    else:
        _sk_items = _sk_mm = _sk_per = None
    if fast:
        # v1.9.6 (#40): gate calls discard the congestion features --
        # skip the O(N^2) MST crossings + RUDY computation entirely.
        _res = {'nets': result, 'pour_nets': pour_nets, 'copper_layers': copper_layers, 'layer_aliases': layer_aliases,
                'crossings': None, 'rudy_peak': None, 'rudy_p95': None,
                'tracks': len([t for t in tracks if t.Type() != pcbnew.PCB_VIA_T]),
                'vias': len([t for t in tracks if t.Type() == pcbnew.PCB_VIA_T]),
                'wirelength_mm': round(sum(mm(t.GetLength()) for t in tracks if t.Type() != pcbnew.PCB_VIA_T), 1),
                'tracks_by_layer': tracks_by_layer, 'track_length_by_layer': {k: round(v, 1) for k, v in track_length_by_layer.items()}, 'tracks_by_layer_net': tracks_by_layer_net, 'pour_layers': pour_layers,
                'footprints': len(list(b.GetFootprints()))}
        if _sk_items is not None: _res['skeleton_intrusions'] = _sk_items; _res['skeleton_intrusion_mm'] = _sk_mm
        return _res
    # v1.2.2 congestion features (for the strategy dataset): ratsnest
    # MST crossings between different nets + RUDY peak/p95 of net
    # bounding boxes on a 32x32 grid.
    def _mst(pts):
        if len(pts) < 2: return []
        used = [pts[0]]; rest = pts[1:]; out = []
        while rest:
            bd, ba, bi = None, None, None
            for a in used:
                for i, c in enumerate(rest):
                    d = (a[0] - c[0]) ** 2 + (a[1] - c[1]) ** 2
                    if bd is None or d < bd: bd, ba, bi = d, a, i
            out.append((ba, rest[bi])); used.append(rest[bi]); rest.pop(bi)
        return out
    segs, boxes, allpts = [], [], []
    for n, pads in nets.items():
        pts = [(mm(p.GetPosition().x), mm(p.GetPosition().y)) for p in pads]
        allpts += pts
        if len(pts) >= 2:
            segs += [(n, a, c) for a, c in _mst(pts)]
            xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
    crossings = None
    if len(segs) <= 1600:
        crossings = 0
        def _cross(o, a, c): return (a[0] - o[0]) * (c[1] - o[1]) - (a[1] - o[1]) * (c[0] - o[0])
        for i in range(len(segs)):
            n1, a, c = segs[i]
            for j in range(i + 1, len(segs)):
                n2, d, e = segs[j]
                if n1 == n2: continue
                if (_cross(a, c, d) * _cross(a, c, e) < 0) and (_cross(d, e, a) * _cross(d, e, c) < 0): crossings += 1
    rudy_peak = rudy_p95 = None
    if boxes and allpts:
        gx0 = min(q[0] for q in allpts); gx1 = max(q[0] for q in allpts)
        gy0 = min(q[1] for q in allpts); gy1 = max(q[1] for q in allpts)
        grid = np.zeros((32, 32))
        for (x0, y0, x1, y1) in boxes:
            wd, ht = max(x1 - x0, 0.2), max(y1 - y0, 0.2)
            dens = (wd + ht) / (wd * ht)
            i0 = max(0, int((x0 - gx0) / max(gx1 - gx0, 1e-6) * 31)); i1 = min(31, int((x1 - gx0) / max(gx1 - gx0, 1e-6) * 31))
            j0 = max(0, int((y0 - gy0) / max(gy1 - gy0, 1e-6) * 31)); j1 = min(31, int((y1 - gy0) / max(gy1 - gy0, 1e-6) * 31))
            grid[j0:j1 + 1, i0:i1 + 1] += dens
        rudy_peak = round(float(grid.max()), 3); rudy_p95 = round(float(np.percentile(grid, 95)), 3)
    _res = {'nets': result, 'pour_nets': pour_nets, 'copper_layers': copper_layers, 'layer_aliases': layer_aliases,
            'crossings': crossings, 'rudy_peak': rudy_peak, 'rudy_p95': rudy_p95,
            'tracks': len([t for t in tracks if t.Type() != pcbnew.PCB_VIA_T]),
            'vias': len([t for t in tracks if t.Type() == pcbnew.PCB_VIA_T]),
            'wirelength_mm': round(sum(mm(t.GetLength()) for t in tracks if t.Type() != pcbnew.PCB_VIA_T), 1),
            'tracks_by_layer': tracks_by_layer, 'track_length_by_layer': {k: round(v, 1) for k, v in track_length_by_layer.items()}, 'tracks_by_layer_net': tracks_by_layer_net, 'pour_layers': pour_layers,
            'footprints': len(list(b.GetFootprints()))}
    if _sk_items is not None: _res['skeleton_intrusions'] = _sk_items; _res['skeleton_intrusion_mm'] = _sk_mm
    return _res

# ---------------------------------------------------------------- fix_violations (#155)
def _track_key(t):
    # Geometry identity of one track/via item, for rollback matching.
    if t.Type() == pcbnew.PCB_VIA_T:
        dr = t.GetDrill()
        drm = mm(dr.x) if hasattr(dr, 'x') else mm(dr)
        return ('via', t.GetNetCode(), round(mm(t.GetPosition().x), 3), round(mm(t.GetPosition().y), 3), round(mm(t.GetWidth(pcbnew.F_Cu)), 3), round(drm, 3))
    if t.Type() == pcbnew.PCB_ARC_T:
        # #159: the midpoint is part of an arc's geometry -- keying
        # it as a plain segment made rollback recreate it as a
        # straight chord.
        return ('arc', t.GetNetCode(), t.GetLayer(), round(mm(t.GetStart().x), 3), round(mm(t.GetStart().y), 3), round(mm(t.GetMid().x), 3), round(mm(t.GetMid().y), 3), round(mm(t.GetEnd().x), 3), round(mm(t.GetEnd().y), 3), round(mm(t.GetWidth()), 3))
    return ('seg', t.GetNetCode(), t.GetLayer(), round(mm(t.GetStart().x), 3), round(mm(t.GetStart().y), 3), round(mm(t.GetEnd().x), 3), round(mm(t.GetEnd().y), 3), round(mm(t.GetWidth()), 3))

def _restore_key(b, key):
    if key[0] == 'via':
        _k, code, x, y, dia, drill = key
        v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I(nm(x), nm(y)))
        v.SetWidth(nm(dia)); v.SetDrill(nm(drill)); v.SetNetCode(code); b.Add(v)
    elif key[0] == 'arc':
        _k, code, lay, x1, y1, xm, ym, x2, y2, wdt = key
        t = pcbnew.PCB_ARC(b)
        t.SetStart(pcbnew.VECTOR2I(nm(x1), nm(y1))); t.SetMid(pcbnew.VECTOR2I(nm(xm), nm(ym))); t.SetEnd(pcbnew.VECTOR2I(nm(x2), nm(y2)))
        t.SetWidth(nm(wdt)); t.SetLayer(lay); t.SetNetCode(code); b.Add(t)
    else:
        _k, code, lay, x1, y1, x2, y2, wdt = key
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(nm(x1), nm(y1))); t.SetEnd(pcbnew.VECTOR2I(nm(x2), nm(y2)))
        t.SetWidth(nm(wdt)); t.SetLayer(lay); t.SetNetCode(code); b.Add(t)

def _fix_one_violation(b, net, x, y, radius=3.0):
    # P2.1b (#155): rip ONLY `net`'s copper within `radius` of the
    # fault point (+-3 mm per plan.md) and re-close the net with the
    # exact-checked bridge planner. True = the rip stood; False =
    # rolled back, board unchanged for this fault. Guarantees on
    # True: the net is no more split than before, and every drawn
    # item passed _new_items_clean inside _bridge_plan. Whether the
    # fault itself is gone is the pipeline gate's call.
    # #162: refill first -- inside a cmd_fix_violations batch, an
    # earlier fault's rip/repair changes what this net's fill
    # should be, and the cached fill polygons do not move until a
    # refill; judging this fault against the pre-batch fill counts
    # clusters the fill no longer has (and misses the connection
    # the fresh fill would have made), so valid pour fixes rolled
    # back. Zone-less boards skip the cost.
    if list(b.Zones()): refill(b)
    conn = b.GetConnectivity(); conn.Build(b)
    pads, clusters = _pad_clusters(b, conn, net)
    if not pads: return False
    before = len(clusters)
    rip = []
    for t in list(b.GetTracks()):
        if t.GetNetname() != net: continue
        if t.IsLocked(): continue  # #159: user-locked copper is never ripped (the #100/#119 class)
        if t.Type() == pcbnew.PCB_VIA_T:
            if math.hypot(mm(t.GetPosition().x) - x, mm(t.GetPosition().y) - y) < radius + mm(t.GetWidth(pcbnew.F_Cu)) / 2: rip.append(t)
        elif seg_pt(x, y, mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y)) < radius + mm(t.GetWidth()) / 2:
            rip.append(t)
    if not rip: return False
    saved = [_track_key(t) for t in rip]
    for t in rip: b.RemoveNative(t)
    # Vias of the net inside the window that the rip left dangling
    # are removed too -- a stranded via is a via_dangling fault the
    # fix would otherwise trade for the one it came to repair.
    conn = b.GetConnectivity(); conn.Build(b)
    for t in list(b.GetTracks()):
        if t.GetNetname() != net or t.Type() != pcbnew.PCB_VIA_T: continue
        if math.hypot(mm(t.GetPosition().x) - x, mm(t.GetPosition().y) - y) >= radius + 1.0: continue
        if t.IsLocked(): continue  # #159: a locked via stays even when stranded
        if not [it for it in conn.GetConnectedItems(t) if it.Type() != pcbnew.PCB_PAD_T]:
            saved.append(_track_key(t)); b.RemoveNative(t)
    nets = b.GetNetsByName(); stack = copper_stack(b)
    drawn = []
    conn = b.GetConnectivity(); conn.Build(b)
    now = len(_pad_clusters(b, conn, net)[1])
    rounds = 0
    while now > before and rounds < 4:
        plan = _bridge_plan(b, net, before)
        if not plan: break
        segs, vias = plan
        if not segs and not vias: break
        tw = net_class_width(b, net, W_TRACK); vd, vdr = net_class_via(b, net)
        code = nets[net].GetNetCode()
        for (li, x1, y1, x2, y2) in segs:
            add_seg(b, nets, net, li, x1, y1, x2, y2, tw)
            drawn.append(('seg', code, stack[li], round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3), round(tw, 3)))
        for (vx, vy) in vias:
            add_via(b, nets, net, vx, vy, vd, vdr)
            drawn.append(('via', code, round(vx, 3), round(vy, 3), round(vd, 3), round(vdr, 3)))
        conn = b.GetConnectivity(); conn.Build(b)
        now = len(_pad_clusters(b, conn, net)[1])
        rounds += 1
    if now > before:
        want = {}
        for k in drawn: want[k] = want.get(k, 0) + 1
        for t in list(b.GetTracks()):
            k = _track_key(t)
            if want.get(k): want[k] -= 1; b.RemoveNative(t)
        for k in saved: _restore_key(b, k)
        return False
    return True

def cmd_fix_violations(board_path, out_path, faults):
    # faults: [{type, x, y, nets: [candidate offender nets, in
    # order]}] -- the router-added faults from the pipeline's base
    # diff (#155). Each fault is attempted per candidate net until
    # one rip+reclose stands.
    b = pcbnew.LoadBoard(board_path); refill(b)
    fixed, failed = [], []
    for f in faults:
        ok = None
        for net in (f.get('nets') or []):
            if _fix_one_violation(b, net, float(f['x']), float(f['y'])):
                ok = net; break
        rec = {'type': f.get('type'), 'x': f.get('x'), 'y': f.get('y'), 'net': ok or (f.get('nets') or [None])[0]}
        (fixed if ok else failed).append(rec)
    if fixed:
        refill(b); pcbnew.SaveBoard(out_path, b)
    return {'status': 'ok' if fixed else 'none', 'fixed': fixed, 'failed': failed}

# ---------------------------------------------------------------- skeleton
def cmd_skeleton(board_path):
    # #63 measurement surface: per (zone, layer) skeleton -- the pad
    # components of its net on this board state, how many tree
    # edges' corridors hold foreign copper (severed edges), and the
    # census totals. The P2.1b pour fault definition reads two of
    # these (base vs final): a pour fault is router-added when a
    # net's pad components grow over the run; an intrusion is the
    # cost signal that predicts it.
    b = pcbnew.LoadBoard(board_path); refill(b)
    conn = b.GetConnectivity(); conn.Build(b)
    skels = pour_skeletons(b)
    items, total_mm, per_net = skeleton_intrusions(b, skels)
    comp_cache = {}
    def _components(net):
        if net in comp_cache: return comp_cache[net]
        pads = []
        for fp in b.GetFootprints():
            for p in fp.Pads():
                if p.GetNetname() == net: pads.append(p)
        seen = []; comp = 0
        for p in pads:
            ids = set()
            for it in conn.GetConnectedItems(p):
                if it.Type() == pcbnew.PCB_PAD_T:
                    ids.add((round(mm(it.GetPosition().x), 2), round(mm(it.GetPosition().y), 2)))
            key = (round(mm(p.GetPosition().x), 2), round(mm(p.GetPosition().y), 2))
            if not any(key in s for s in seen):
                comp += 1; seen.append(ids)
        comp_cache[net] = comp
        return comp
    rows = []
    for sk in skels:
        sev = 0
        for (i, j, _d) in sk['edges']:
            n, _m2, _p2 = skeleton_intrusions(b, [dict(sk, edges=[(i, j, _d)])])
            if n: sev += 1
        rows.append({'net': sk['net'], 'layer': b.GetLayerName(sk['layer']),
                     'width_mm': sk['width'], 'pads': len(sk['pads']), 'edges': len(sk['edges']),
                     'severed_edges': sev, 'components': _components(sk['net'])})
    return {'skeletons': rows, 'intrusions': items, 'intrusion_mm': total_mm, 'intrusions_by_net': per_net}

# ---------------------------------------------------------------- scorecard
def cmd_scorecard(board_path):
    b = pcbnew.LoadBoard(board_path)
    net_pads = {}
    for fp in b.GetFootprints():
        for p in fp.Pads():
            net_pads.setdefault(p.GetNetname(), []).append((fp.GetReference(), mm(p.GetPosition().x), mm(p.GetPosition().y)))
    rows = []
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        if not ref.startswith('C'): continue
        for p in fp.Pads():
            n = p.GetNetname()
            if n in ('GND', '', None): continue
            px, py = mm(p.GetPosition().x), mm(p.GetPosition().y)
            best = None
            for (oref, ox, oy) in net_pads.get(n, []):
                if oref == ref: continue
                d = math.hypot(ox - px, oy - py)
                if best is None or d < best[0]: best = (d, oref)
            if best: rows.append({'cap': ref, 'net': n, 'dist_mm': round(best[0], 2), 'to': best[1]})
    rows.sort(key=lambda r: -r['dist_mm'])
    return {'worst_caps': rows[:12]}

# ---------------------------------------------------------------- grid A*
W_TRACK = 0.20
CLR_PLAN = 0.23          # board rule 0.20 + grid-quantization margin
VIA_R = 0.30
PITCH = 0.1

def seg_pt(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1; L2 = dx * dx + dy * dy
    if L2 == 0: return math.hypot(px - x1, py - y1)
    u = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + u * dx), py - (y1 + u * dy))

def _pad_sdf_params(p):
    # (cx, cy, kind, a, b, rot, cr): the pad-shape parameterisation the
    # Grid stamps (v1.2.3), shared by the exact checkers (v1.2.5) so a
    # bridge the planner finds legal is not rejected by a disc model.
    sx, sy = mm(p.GetSize().x) / 2, mm(p.GetSize().y) / 2
    shp = p.GetShape()
    if shp == pcbnew.PAD_SHAPE_CIRCLE: kind, cr = 'disc', 0.0
    elif shp == pcbnew.PAD_SHAPE_OVAL: kind, cr = 'oval', 0.0
    elif shp in (pcbnew.PAD_SHAPE_RECT, pcbnew.PAD_SHAPE_ROUNDRECT, pcbnew.PAD_SHAPE_CHAMFERED_RECT):
        kind = 'rect'
        cr = mm(p.GetRoundRectCornerRadius()) if shp == pcbnew.PAD_SHAPE_ROUNDRECT else 0.0
    else:  # trapezoid / custom: conservative disc, as in the Grid
        kind, cr = 'disc', 0.0
        sx = sy = 0.5 * math.hypot(mm(p.GetSize().x), mm(p.GetSize().y))
    return (mm(p.GetPosition().x), mm(p.GetPosition().y), kind, sx, sy,
            p.GetOrientation().AsDegrees() * math.pi / 180.0, cr)

def _pad_sdf_dist(params, x, y):
    cx, cy, kind, a, bb, rot, cr = params
    co, si = math.cos(rot), math.sin(rot)
    dx, dy = x - cx, y - cy
    lx, ly = co * dx + si * dy, -si * dx + co * dy
    if kind == 'disc': return math.hypot(lx, ly) - a
    if kind == 'oval':
        if a >= bb: return math.hypot(max(abs(lx) - (a - bb), 0.0), ly) - bb
        return math.hypot(lx, max(abs(ly) - (bb - a), 0.0)) - a
    qx, qy = abs(lx) - (a - cr), abs(ly) - (bb - cr)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - cr

def _seg_pad_dist(params, x1, y1, x2, y2):
    # Min pad-SDF along a segment, sampled at 0.02 mm; the SDF is
    # 1-Lipschitz so the sampled min less half a step is a safe bound.
    cx, cy, _k, a, bb, _r, _c = params
    ext = math.hypot(a, bb)
    if seg_pt(cx, cy, x1, y1, x2, y2) - ext > 1.0: return seg_pt(cx, cy, x1, y1, x2, y2) - ext
    steps = max(2, int(math.hypot(x2 - x1, y2 - y1) / 0.02) + 1)
    return min(_pad_sdf_dist(params, x1 + (x2 - x1) * k / steps, y1 + (y2 - y1) * k / steps) for k in range(steps + 1)) - 0.011

def copper_stack(b):
    # Ordered pcbnew copper layer IDs, top to bottom. KiCad numbers
    # copper out of stack order (F=0, B=2, In1=4, In2=6 on KiCad 10),
    # so grid layer indices are positions in THIS table, never raw IDs.
    n = b.GetCopperLayerCount()
    if n <= 1: return [pcbnew.F_Cu]
    if n == 2: return [pcbnew.F_Cu, pcbnew.B_Cu]
    return [pcbnew.F_Cu] + [getattr(pcbnew, 'In%d_Cu' % k) for k in range(1, n - 1)] + [pcbnew.B_Cu]

def _in_poly(px, py, pts):
    inside = False
    for i in range(len(pts)):
        x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % len(pts)]
        if (y1 > py) != (y2 > py):
            if px < x1 + (py - y1) * (x2 - x1) / (y2 - y1): inside = not inside
    return inside

def _mst_edges(pts):
    # Prim spanning tree over (x, y) points; returns [(i, j, dist)].
    n = len(pts)
    if n < 2: return []
    used = [0]; rest = list(range(1, n)); edges = []
    while rest:
        bd, ba, bi = None, None, None
        for a in used:
            ax, ay = pts[a]
            for i in rest:
                d = (ax - pts[i][0]) ** 2 + (ay - pts[i][1]) ** 2
                if bd is None or d < bd: bd, ba, bi = d, a, i
        edges.append((ba, bi, round(math.sqrt(bd), 3))); used.append(bi); rest.remove(bi)
    return edges

_SKEL_CACHE = {}

def pour_skeletons(b):
    # #63: each pour's pad spanning tree, per (zone, copper layer).
    # The tree is the pour's intended connectivity: the pads it joins
    # are meant to stay connected through the fill, so foreign copper
    # inside a tree corridor is what severs the pour. Pads are
    # confined to the pour region (the zone's own outline) -- a pad
    # across the board belongs to another zone of the same net, not
    # to this pour. Width per the ticket: zone min-thickness +
    # slack; the reserved corridor adds the board clearance on top
    # (foreign copper must clear the skeleton track, not touch it).
    # The result is cached per board object with a shape check on
    # hit. #161: the shape must cover everything the computation
    # consumes -- landed vias of the zone nets are MST nodes below,
    # so a via edit (stitch, fix_violations) changes the skeleton
    # even though zones and footprints never move; and a bare
    # id(b) can be recycled by a later board in the same process,
    # so the board's filename rides the key.
    key = (id(b), b.GetFileName())
    n_tracks = len(list(b.GetTracks()))
    n_vias = sum(1 for t in b.GetTracks() if t.Type() == pcbnew.PCB_VIA_T)
    hit = _SKEL_CACHE.get(key)
    if hit is not None and hit[0] == len(list(b.Zones())) and hit[1] == len(list(b.GetFootprints())) and hit[2] == n_tracks and hit[3] == n_vias:
        return hit[4]
    stack = copper_stack(b); lidx = {lay: k for k, lay in enumerate(stack)}
    clr = board_clearance(b)
    zone_nets = set()
    for z in b.Zones():
        if not z.GetIsRuleArea() and z.GetNetname(): zone_nets.add(z.GetNetname())
    net_pads = {}
    net_vias = {}
    if zone_nets:
        for fp in b.GetFootprints():
            for p in fp.Pads():
                n = p.GetNetname()
                if n in zone_nets: net_pads.setdefault(n, []).append(p)
        for t in b.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T and t.GetNetname() in zone_nets:
                net_vias.setdefault(t.GetNetname(), []).append(t)
    out = []
    for z in b.Zones():
        if z.GetIsRuleArea(): continue
        net = z.GetNetname()
        if not net: continue
        try: lays = list(z.GetLayerSet().CuStack())
        except Exception: lays = [z.GetLayer()]
        width = mm(z.GetMinThickness()) + SKELETON_SLACK_MM
        rings = []
        try:
            o = z.Outline()
            for oi in range(o.OutlineCount()):
                ol = o.Outline(oi)
                ring = [(mm(ol.CPoint(k).x), mm(ol.CPoint(k).y)) for k in range(ol.PointCount())]
                if len(ring) >= 3: rings.append(ring)
        except Exception:
            rings = []
        for lay in lays:
            if lay not in lidx: continue
            pts = set()
            # Node rule: on an outer layer, the pour's connection
            # points are the pads physically on it (SMD on-layer +
            # through-hole). On an INNER layer every pad of the net
            # inside the region is a connection point -- it reaches
            # the plane by via at the pad, so the pad's position is
            # the node (a plane whose pads are all SMD would
            # otherwise have no skeleton at all until vias land).
            inner = lay not in (pcbnew.F_Cu, pcbnew.B_Cu)
            for p in net_pads.get(net, []):
                if p.GetDrillSize().x > 0 or p.IsOnLayer(lay) or inner:
                    pt = (round(mm(p.GetPosition().x), 3), round(mm(p.GetPosition().y), 3))
                    if rings and not any(_in_poly(pt[0], pt[1], r) for r in rings): continue
                    pts.add(pt)
            for v in net_vias.get(net, []):
                try:
                    if not v.IsOnLayer(lay): continue
                except Exception:
                    pass
                pt = (round(mm(v.GetPosition().x), 3), round(mm(v.GetPosition().y), 3))
                if rings and not any(_in_poly(pt[0], pt[1], r) for r in rings): continue
                pts.add(pt)
            pts = sorted(pts)
            if len(pts) < 2: continue
            out.append({'net': net, 'layer': lay, 'layer_idx': lidx[lay],
                        'width': round(width, 3), 'corridor': round(width / 2 + clr, 3),
                        'pads': pts, 'edges': _mst_edges(pts)})
    _SKEL_CACHE[key] = (len(list(b.Zones())), len(list(b.GetFootprints())), n_tracks, n_vias, out)
    return out

def _seg_in_band(x1, y1, x2, y2, sx1, sy1, sx2, sy2, band):
    # Sampled length of segment (x1..y2) lying within `band` of the
    # skeleton segment (sx1..sy2); 0.0 when it never enters the band.
    L = math.hypot(x2 - x1, y2 - y1)
    if L == 0: return 0.0
    steps = max(1, int(L / 0.25))
    hit = 0
    for k in range(steps + 1):
        t = k / steps
        if seg_pt(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, sx1, sy1, sx2, sy2) < band: hit += 1
    return L * hit / (steps + 1)

def skeleton_intrusions(b, skeletons=None):
    # #63 gate census: foreign copper inside reserved corridors.
    # Returns (items, mm, per_net): an item is one foreign track
    # segment or via whose copper enters a corridor (centreline
    # distance < corridor + the item's own half-width); mm is the
    # sampled in-corridor length of intruding segments (vias add no
    # length). An item is counted once per pour it intrudes.
    if skeletons is None: skeletons = pour_skeletons(b)
    if not skeletons: return 0, 0.0, {}
    stack = copper_stack(b); lidx = {lay: k for k, lay in enumerate(stack)}
    segs_by_layer = {}
    vias = []
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            vias.append((t.GetNetname(), mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetWidth(pcbnew.F_Cu)) / 2))
        else:
            li = lidx.get(t.GetLayer())
            if li is not None:
                segs_by_layer.setdefault(li, []).append((t.GetNetname(), mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y), mm(t.GetWidth()) / 2))
    items = 0; total_mm = 0.0; per_net = {}
    for sk in skeletons:
        net = sk['net']; li = sk['layer_idx']; cor = sk['corridor']
        seen = set()
        for (i, j, _d) in sk['edges']:
            sx1, sy1 = sk['pads'][i]; sx2, sy2 = sk['pads'][j]
            bx0, bx1 = min(sx1, sx2) - cor - 1.0, max(sx1, sx2) + cor + 1.0
            by0, by1 = min(sy1, sy2) - cor - 1.0, max(sy1, sy2) + cor + 1.0
            for (n2, x1, y1, x2, y2, hr) in segs_by_layer.get(li, []):
                if n2 == net: continue
                if max(x1, x2) < bx0 or min(x1, x2) > bx1 or max(y1, y2) < by0 or min(y1, y2) > by1: continue
                if seg_seg_dist((x1, y1, x2, y2), (sx1, sy1, sx2, sy2)) < cor + hr:
                    key = ('T', n2, li, round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2))
                    if key not in seen:
                        seen.add(key); items += 1; per_net[net] = per_net.get(net, 0) + 1
                    total_mm += _seg_in_band(x1, y1, x2, y2, sx1, sy1, sx2, sy2, cor + hr)
            for (n2, cx, cy, vr) in vias:
                if n2 == net: continue
                if not (bx0 <= cx <= bx1 and by0 <= cy <= by1): continue
                if seg_pt(cx, cy, sx1, sy1, sx2, sy2) < cor + vr:
                    key = ('V', n2, round(cx, 2), round(cy, 2))
                    if key not in seen:
                        seen.add(key); items += 1; per_net[net] = per_net.get(net, 0) + 1
    return items, round(total_mm, 1), per_net

class Grid:
    def __init__(self, b, window, track_w=None, via_r=None):
        self.X0, self.X1, self.Y0, self.Y1 = window
        self.via_r = via_r if via_r else VIA_R
        self.clr_plan = board_clearance(b) + 0.03
        self.inf = (track_w if track_w else W_TRACK) / 2 + self.clr_plan
        self.NX = int((self.X1 - self.X0) / PITCH) + 1
        self.NY = int((self.Y1 - self.Y0) / PITCH) + 1
        self.gx = self.X0 + np.arange(self.NX) * PITCH
        self.gy = self.Y0 + np.arange(self.NY) * PITCH
        # v1.3 phase (a): the grid is N-layer. Layer indices are stack
        # positions (see copper_stack). Placement law follows the
        # router's: every copper layer is routable except In1.Cu (the
        # ground-plane layer in the standing layer law).
        self.stack = copper_stack(b)
        self.nl = len(self.stack)
        self.lidx = {lay: k for k, lay in enumerate(self.stack)}
        _banned = BANNED_LAYER_IDS if BANNED_LAYER_IDS is not None else {pcbnew.In1_Cu}
        if NO_TRACKS_ON_UNRESOLVED:
            # v1.9 (M4): a ban name the canonical table could not
            # resolve must not be silently dropped (the pre-v1.9
            # behaviour -- that layer was simply never banned in the
            # finisher). Resolve against this board or fail loud; the
            # gate census remains the backstop for non-Grid tools.
            _extra, _still = _resolve_banned_on_board(b, NO_TRACKS_ON_UNRESOLVED)
            if _still:
                raise ValueError('--no-tracks-on names not resolvable on this board: %s (copper layers: %s)' % (', '.join(_still), ', '.join(b.GetLayerName(l) for l in self.stack)))
            _banned = set(_banned) | _extra
        _cost = dict(LAYER_COST_BY_ID)
        for _nm, _f in LAYER_COST_UNRESOLVED:
            _ids, _still = _resolve_banned_on_board(b, [_nm])
            if _still:
                raise ValueError('--layer-cost names not resolvable on this board: %s (copper layers: %s)' % (', '.join(_still), ', '.join(b.GetLayerName(l) for l in self.stack)))
            for _lid in _ids: _cost[_lid] = _f
        # v1.11 (#73): a priced layer is NOT banned -- if a layer
        # arrives as both (legs receive the raw configured bans),
        # the price wins, mirroring the pipeline's effective law.
        _banned = _banned - set(_cost)
        self.layer_cost = {self.lidx[lay]: f for lay, f in _cost.items() if lay in self.lidx}
        self._lfac = [float(self.layer_cost.get(k, 1)) for k in range(self.nl)]
        self.routable = {k for k, lay in enumerate(self.stack) if lay not in _banned}
        self.banned_idx = {k for k, lay in enumerate(self.stack) if lay in _banned}
        self.pads, self.segs, self.vias = [], [], []
        # v1.3.1 P1: keep only items that can touch the window (+2 mm
        # margin over the largest stamp radius). build() stamps the
        # same set either way; the win is _plan_attempts, which builds
        # a grid per victim candidate and used to scan board-wide
        # copper for candidates that cannot intersect the leg window.
        _wx0, _wx1, _wy0, _wy1 = window[0] - 2.0, window[1] + 2.0, window[2] - 2.0, window[3] + 2.0
        def _near(x, y): return _wx0 <= x <= _wx1 and _wy0 <= y <= _wy1
        for fp in b.GetFootprints():
            for p in fp.Pads():
                if not _near(mm(p.GetPosition().x), mm(p.GetPosition().y)): continue
                layers = set()
                if p.IsOnLayer(pcbnew.F_Cu): layers.add(0)
                if p.IsOnLayer(pcbnew.B_Cu): layers.add(self.nl - 1)
                if p.GetDrillSize().x > 0: layers = set(range(self.nl))
                # v1.2.3: stamp the pad's TRUE shape (rect / roundrect /
                # oval / circle SDF), not the 0.5*hypot disc -- the disc
                # over-blocked a 1.27x0.56 pad by 2.5x on its short axis
                # and closed legal channels + via sites on fine pitch.
                sx, sy = mm(p.GetSize().x) / 2, mm(p.GetSize().y) / 2
                shp = p.GetShape()
                if shp == pcbnew.PAD_SHAPE_CIRCLE: kind, cr = 'disc', 0.0
                elif shp == pcbnew.PAD_SHAPE_OVAL: kind, cr = 'oval', 0.0
                elif shp in (pcbnew.PAD_SHAPE_RECT, pcbnew.PAD_SHAPE_ROUNDRECT, pcbnew.PAD_SHAPE_CHAMFERED_RECT):
                    kind = 'rect'
                    cr = mm(p.GetRoundRectCornerRadius()) if shp == pcbnew.PAD_SHAPE_ROUNDRECT else 0.0
                else:  # trapezoid / custom: keep the conservative disc
                    kind, cr = 'disc', 0.0
                    sx = sy = 0.5 * math.hypot(mm(p.GetSize().x), mm(p.GetSize().y))
                self.pads.append((p.GetNetname(), layers, mm(p.GetPosition().x), mm(p.GetPosition().y),
                                  kind, sx, sy, p.GetOrientation().AsDegrees() * math.pi / 180.0, cr))
        for t in b.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                if _near(mm(t.GetPosition().x), mm(t.GetPosition().y)):
                    self.vias.append((t.GetNetname(), mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetWidth(pcbnew.F_Cu)) / 2))
            else:
                li = self.lidx.get(t.GetLayer())
                sx, sy, ex, ey = mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y)
                # v1.7.1 (issue #1): bbox intersection, not endpoint-only --
                # a segment crossing the window with both endpoints outside
                # the +-2 mm box was silently dropped from every planner.
                if li is not None and not (max(sx, ex) < _wx0 or min(sx, ex) > _wx1 or max(sy, ey) < _wy0 or min(sy, ey) > _wy1):
                    self.segs.append((t.GetNetname(), li, sx, sy, ex, ey, mm(t.GetWidth()) / 2))
        # Rule areas (keepouts): the finisher was blind to them and
        # routed legs through the GNSS RF keepout (26 items_not_allowed
        # in one run). Stamp them as hard obstacles in build().
        self.keepouts = []
        # v1.3 phase (a) fix: footprint-embedded rule areas count too --
        # the J1 keepout lives inside the J1 footprint, and b.Zones()
        # never lists it, so the planner, the via hunt and the exact
        # checker were all blind to it (a hunt via landed 0.015 mm from
        # its edge: items_not_allowed at the gate).
        _all_zones = list(b.Zones())
        for fp in b.GetFootprints(): _all_zones.extend(list(fp.Zones()))
        for z in _all_zones:
            if not z.GetIsRuleArea(): continue
            layers = {k for k, lay in enumerate(self.stack) if z.IsOnLayer(lay)}
            o = z.Outline()
            for oi in range(o.OutlineCount()):
                ol = o.Outline(oi)
                pts = [(mm(ol.CPoint(k).x), mm(ol.CPoint(k).y)) for k in range(ol.PointCount())]
                if pts:
                    _xs = [q[0] for q in pts]; _ys = [q[1] for q in pts]
                    if max(_xs) < _wx0 or min(_xs) > _wx1 or max(_ys) < _wy0 or min(_ys) > _wy1: continue
                self.keepouts.append((pts, layers, z.GetDoNotAllowTracks(), z.GetDoNotAllowVias()))
        # v1.12 (#63): pour skeletons whose corridors touch the window
        # (the same +-2 mm filter as the copper above). build() and
        # build_owners() stamp them as foreign-net keepouts; the
        # corridors are stored as plain segments per skeleton.
        self.skeletons = []
        if SKELETON_ON:
            for sk in pour_skeletons(b):
                keep = []
                for (i, j, _d) in sk['edges']:
                    x1, y1 = sk['pads'][i]; x2, y2 = sk['pads'][j]
                    if max(x1, x2) < _wx0 or min(x1, x2) > _wx1 or max(y1, y2) < _wy0 or min(y1, y2) > _wy1: continue
                    keep.append((x1, y1, x2, y2))
                if keep:
                    self.skeletons.append({'net': sk['net'], 'layer_idx': sk['layer_idx'], 'width': sk['width'], 'segs': keep})
    def build(self, ignore_nets, target=None):
        NX, NY, X0, Y0 = self.NX, self.NY, self.X0, self.Y0
        gx, gy = self.gx, self.gy
        blocked = np.zeros((self.nl, NY, NX), dtype=bool)
        via_block = np.zeros((NY, NX), dtype=bool)
        INF = self.inf
        def stamp_seg(li, x1, y1, x2, y2, hr):
            rad = hr + INF
            i0, i1 = max(0, int((min(x1, x2) - rad - X0) / PITCH)), min(NX - 1, int((max(x1, x2) + rad - X0) / PITCH) + 1)
            j0, j1 = max(0, int((min(y1, y2) - rad - Y0) / PITCH)), min(NY - 1, int((max(y1, y2) + rad - Y0) / PITCH) + 1)
            radv = self.via_r + hr + self.clr_plan
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    d = seg_pt(gx[i], gy[j], x1, y1, x2, y2)
                    if d < rad: blocked[li, j, i] = True
                    if d < radv: via_block[j, i] = True
        def stamp_disc(layers, cx, cy, r):
            rad = r + INF; radv = r + self.via_r + self.clr_plan
            R = max(rad, radv)  # bbox only: the blocked test keeps rad (v1.2.3 fix --
            # an earlier draft stamped blocked out to radv, over-blocking every via)
            i0, i1 = max(0, int((cx - R - X0) / PITCH)), min(NX - 1, int((cx + R - X0) / PITCH) + 1)
            j0, j1 = max(0, int((cy - R - Y0) / PITCH)), min(NY - 1, int((cy + R - Y0) / PITCH) + 1)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    dd = math.hypot(gx[i] - cx, gy[j] - cy)
                    if dd < rad:
                        for li in layers: blocked[li, j, i] = True
                    if dd < radv: via_block[j, i] = True
        def in_poly(px, py, pts):
            inside = False
            for i2 in range(len(pts)):
                x1, y1 = pts[i2]; x2, y2 = pts[(i2 + 1) % len(pts)]
                if (y1 > py) != (y2 > py):
                    if px < x1 + (py - y1) * (x2 - x1) / (y2 - y1): inside = not inside
            return inside
        for pts, klayers, no_tracks, no_vias in self.keepouts:
            xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
            i0, i1 = max(0, int((min(xs) - X0) / PITCH)), min(NX - 1, int((max(xs) - X0) / PITCH) + 1)
            j0, j1 = max(0, int((min(ys) - Y0) / PITCH)), min(NY - 1, int((max(ys) - Y0) / PITCH) + 1)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    if in_poly(gx[i], gy[j], pts):
                        if no_tracks:
                            for li in klayers: blocked[li, j, i] = True
                        if no_vias: via_block[j, i] = True
        def stamp_pad(layers, cx, cy, kind, a, bb, rot, cr):
            ext = math.hypot(a, bb)
            rad = ext + INF; radv = ext + self.via_r + self.clr_plan
            R = max(rad, radv)  # the old disc stamp clipped via_block to the track radius
            i0, i1 = max(0, int((cx - R - X0) / PITCH)), min(NX - 1, int((cx + R - X0) / PITCH) + 1)
            j0, j1 = max(0, int((cy - R - Y0) / PITCH)), min(NY - 1, int((cy + R - Y0) / PITCH) + 1)
            co, si = math.cos(rot), math.sin(rot)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    dx, dy = gx[i] - cx, gy[j] - cy
                    lx, ly = co * dx + si * dy, -si * dx + co * dy
                    if kind == 'disc':
                        d = math.hypot(lx, ly) - a
                    elif kind == 'oval':
                        if a >= bb: d = math.hypot(max(abs(lx) - (a - bb), 0.0), ly) - bb
                        else: d = math.hypot(lx, max(abs(ly) - (bb - a), 0.0)) - a
                    else:  # rect / roundrect SDF
                        qx, qy = abs(lx) - (a - cr), abs(ly) - (bb - cr)
                        d = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - cr
                    if d < INF:
                        for li in layers: blocked[li, j, i] = True
                    if d < self.via_r + self.clr_plan: via_block[j, i] = True
        for net, layers, cx, cy, kind, a, bb, rot, cr in self.pads:
            # v1.5: pads are stamped for every net except the routed
            # (target) net -- INCLUDING victim nets in ignore_nets.
            # Ripping removes a victim's tracks and vias, never its
            # pads, and the exact checker tests the drawn leg against
            # all foreign pads; a planner that cannot see victim pads
            # threads legs through them (measured: /TMS leg across
            # U1.48, a pad of its own victim /TCK, SDF distance
            # -0.011 mm). Tracks/vias of ignored nets stay soft.
            if target is not None:
                if net != target: stamp_pad(layers, cx, cy, kind, a, bb, rot, cr)
            elif net not in ignore_nets: stamp_pad(layers, cx, cy, kind, a, bb, rot, cr)
        for net, cx, cy, r in self.vias:
            if net not in ignore_nets: stamp_disc(set(range(self.nl)), cx, cy, r)
        for net, li, x1, y1, x2, y2, hr in self.segs:
            if net not in ignore_nets: stamp_seg(li, x1, y1, x2, y2, hr)
        # v1.12 (#63): reserved skeleton corridors, stamped LAST so
        # no ordinary stamping can reopen them. A corridor blocks
        # every net except its own pour's; with target None, the
        # ignore set names the nets being re-planned and their own
        # pours' corridors stay open to them. Callers' free_disc()
        # at the target's pads still applies afterwards -- a foreign
        # pad inside a corridor stays reachable; the reservation
        # guards the path between the pour's pads, not other pads.
        if SKELETON_ON:
            for sk in self.skeletons:
                if target is not None:
                    if sk['net'] == target: continue
                elif sk['net'] in ignore_nets: continue
                for (x1, y1, x2, y2) in sk['segs']:
                    stamp_seg(sk['layer_idx'], x1, y1, x2, y2, sk['width'] / 2)
        return blocked, via_block
    def free_disc(self, blocked, via_block, cx, cy, layers):
        NX, NY, X0, Y0 = self.NX, self.NY, self.X0, self.Y0
        i0, i1 = max(0, int((cx - 0.55 - X0) / PITCH)), min(NX - 1, int((cx + 0.55 - X0) / PITCH) + 1)
        j0, j1 = max(0, int((cy - 0.55 - Y0) / PITCH)), min(NY - 1, int((cy + 0.55 - Y0) / PITCH) + 1)
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                if math.hypot(self.gx[i] - cx, self.gy[j] - cy) < 0.55:
                    # Free only the track cells at the pad: via_block
                    # stays, so a via is never placed where foreign
                    # copper (any layer) forbids it -- freeing it put
                    # leg vias inside clearance of B.Cu tracks and on
                    # an In2 track (measured clearance + short faults).
                    for li in layers: blocked[li, j, i] = False
    def cell(self, x, y):
        return (min(self.NX - 1, max(0, int(round((x - self.X0) / PITCH)))),
                min(self.NY - 1, max(0, int(round((y - self.Y0) / PITCH)))))
    def astar(self, blocked, via_block, start_xy, goal_xy, start_layers, goal_layers, extra_layers=(), max_pops=None):
        # v1.3.2 layer law: the search may only TRAVEL on routable
        # layers plus extra_layers the caller vouches for (a pour
        # net's own plane layer when bridging that net). Before this,
        # TH-pad/via endpoints seeded the search on every layer --
        # including the banned GND plane, whose blocked plane is
        # nearly empty, so heal/finish paths ran tracks straight
        # across it and the pour refill hid them from DRC.
        extra = set(extra_layers)
        allowed = self.routable | extra
        si = self.cell(*start_xy); gi = self.cell(*goal_xy)
        starts = [(l, si[1], si[0]) for l in start_layers if l in allowed]
        # v1.8.1 (#11): a blocked goal cell is unreachable -- exclude it
        # so an all-blocked goal returns at once instead of flooding
        # every reachable cell first.
        goalset = {(l, gi[1], gi[0]) for l in goal_layers if l in allowed and not blocked[l, gi[1], gi[0]]}
        if not starts or not goalset: return None, None
        dist = {s: 0.0 for s in starts}; prev = {}
        def _h(j, i):
            dx, dy = abs(i - gi[0]), abs(j - gi[1])
            return max(dx, dy) + 0.41421356 * min(dx, dy)
        pq = [(_h(s[1], s[2]), 0.0, s) for s in starts]; heapq.heapify(pq)
        found = None; pops = 0
        while pq:
            _f, dcur, node = heapq.heappop(pq)  # heap key is f = g + h; staleness compares g
            pops += 1
            # v1.8.1 (#10): optional expansion budget -- the via hunt
            # passes one so a fenced target cannot flood the whole
            # window once per candidate via.
            if max_pops is not None and pops > max_pops: return None, None
            if dcur > dist.get(node, 1e18): continue
            if node in goalset: found = node; break
            li, j, i = node
            for dj, di in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nj, ni = j + dj, i + di
                if 0 <= nj < self.NY and 0 <= ni < self.NX and not blocked[li, nj, ni]:
                    # v1.2.4 corner-cut guard: a diagonal step may not slip
                    # between two blocked orthogonal neighbours -- it passes
                    # within p/sqrt(2) of their corners (v3 audit, bug #6).
                    if dj and di and (blocked[li, j + dj, i] or blocked[li, j, i + di]): continue
                    nd = dcur + (1.41421356 if dj and di else 1.0) * (1.0 if li in extra else self._lfac[li]); nb = (li, nj, ni)  # v1.11 (#73): travel on a priced layer costs its factor per cell (1 = unpriced, the old cost exactly; the heuristic stays unit-cost, admissible since factors are >= 1). v1.11.2 (#103): a layer the caller vouches for via extra_layers -- a net's OWN plane, at every call site -- travels at unit cost; pricing it inverted the policy (an own-plane bridge dearer than under the hard ban the price replaced)
                    if nd < dist.get(nb, 1e18): dist[nb] = nd; prev[nb] = node; heapq.heappush(pq, (nd + _h(nj, ni), nd, nb))
            if not via_block[j, i]:
                # A through via pierces every layer: transition to any
                # layer the search is allowed to travel on.
                for ol in allowed:
                    if ol == li or blocked[ol, j, i]: continue
                    nb = (ol, j, i); nd = dcur + 25.0
                    if nd < dist.get(nb, 1e18): dist[nb] = nd; prev[nb] = node; heapq.heappush(pq, (nd + _h(j, i), nd, nb))
        if found is None: return None, None
        path = [found]
        while path[-1] in prev: path.append(prev[path[-1]])
        path = path[::-1]
        pts = [(p[0], round(float(self.gx[p[2]]), 2), round(float(self.gy[p[1]]), 2)) for p in path]
        simp = [pts[0]]
        for a, bp, c in zip(pts, pts[1:], pts[2:]):
            if bp[0] != a[0] or bp[0] != c[0]: simp.append(bp); continue
            v1 = (bp[1] - a[1], bp[2] - a[2]); v2 = (c[1] - bp[1], c[2] - bp[2])
            if abs(v1[0] * v2[1] - v1[1] * v2[0]) > 1e-9: simp.append(bp)
        simp.append(pts[-1])
        return simp, dist[found]

    def build_owners(self, ignore_nets, target):
        # v1.3.1 phase (b): like build(), but foreign TRACK/VIA copper
        # is SOFT. Returns (blocked, via_block, owner, via_owner, nets):
        # the first two are the HARD masks (foreign pads -- the target's
        # own pads exempt, victims' pads included since v1.7.1 --
        # keepouts, and GND, never a finish victim); owner[(li, j, i)] /
        # via_owner[(j, i)] are BITMASKS over `nets` naming every foreign
        # net whose clearance halo touches the cell (v1.7.1, issue #2:
        # halo-overlap cells are priced per touched net, not hard).
        NX, NY, X0, Y0 = self.NX, self.NY, self.X0, self.Y0
        gx, gy = self.gx, self.gy
        NL = self.nl
        hard = np.zeros((NL, NY, NX), dtype=bool)
        vhard = np.zeros((NY, NX), dtype=bool)
        INF = self.inf
        def stamp_seg(arr, varr, li, x1, y1, x2, y2, hr):
            rad = hr + INF
            i0, i1 = max(0, int((min(x1, x2) - rad - X0) / PITCH)), min(NX - 1, int((max(x1, x2) + rad - X0) / PITCH) + 1)
            j0, j1 = max(0, int((min(y1, y2) - rad - Y0) / PITCH)), min(NY - 1, int((max(y1, y2) + rad - Y0) / PITCH) + 1)
            radv = self.via_r + hr + self.clr_plan
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    d = seg_pt(gx[i], gy[j], x1, y1, x2, y2)
                    if d < rad: arr[li, j, i] = True
                    if varr is not None and d < radv: varr[j, i] = True
        def stamp_disc(arr, varr, layers, cx, cy, r):
            rad = r + INF; radv = r + self.via_r + self.clr_plan
            R = max(rad, radv)
            i0, i1 = max(0, int((cx - R - X0) / PITCH)), min(NX - 1, int((cx + R - X0) / PITCH) + 1)
            j0, j1 = max(0, int((cy - R - Y0) / PITCH)), min(NY - 1, int((cy + R - Y0) / PITCH) + 1)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    dd = math.hypot(gx[i] - cx, gy[j] - cy)
                    if dd < rad:
                        for li in layers: arr[li, j, i] = True
                    if varr is not None and dd < radv: varr[j, i] = True
        def in_poly(px, py, pts):
            inside = False
            for i2 in range(len(pts)):
                x1, y1 = pts[i2]; x2, y2 = pts[(i2 + 1) % len(pts)]
                if (y1 > py) != (y2 > py):
                    if px < x1 + (py - y1) * (x2 - x1) / (y2 - y1): inside = not inside
            return inside
        for pts, klayers, no_tracks, no_vias in self.keepouts:
            xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
            i0, i1 = max(0, int((min(xs) - X0) / PITCH)), min(NX - 1, int((max(xs) - X0) / PITCH) + 1)
            j0, j1 = max(0, int((min(ys) - Y0) / PITCH)), min(NY - 1, int((max(ys) - Y0) / PITCH) + 1)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    if in_poly(gx[i], gy[j], pts):
                        if no_tracks:
                            for li in klayers: hard[li, j, i] = True
                        if no_vias: vhard[j, i] = True
        def stamp_pad(layers, cx, cy, kind, a, bb, rot, cr):
            ext = math.hypot(a, bb)
            rad = ext + INF; radv = ext + self.via_r + self.clr_plan
            R = max(rad, radv)
            i0, i1 = max(0, int((cx - R - X0) / PITCH)), min(NX - 1, int((cx + R - X0) / PITCH) + 1)
            j0, j1 = max(0, int((cy - R - Y0) / PITCH)), min(NY - 1, int((cy + R - Y0) / PITCH) + 1)
            co, si = math.cos(rot), math.sin(rot)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    dx, dy = gx[i] - cx, gy[j] - cy
                    lx, ly = co * dx + si * dy, -si * dx + co * dy
                    if kind == 'disc': d = math.hypot(lx, ly) - a
                    elif kind == 'oval':
                        if a >= bb: d = math.hypot(max(abs(lx) - (a - bb), 0.0), ly) - bb
                        else: d = math.hypot(lx, max(abs(ly) - (bb - a), 0.0)) - a
                    else:
                        qx, qy = abs(lx) - (a - cr), abs(ly) - (bb - cr)
                        d = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - cr
                    if d < INF:
                        for li in layers: hard[li, j, i] = True
                    if d < self.via_r + self.clr_plan: vhard[j, i] = True
        # v1.7.1 (issue #2): pads stamped with Grid.build semantics --
        # every net except the target, victims included (a victim's pad
        # can never be ripped, so a cut-set path through it is a lie).
        for net, layers, cx, cy, kind, a, bb, rot, cr in self.pads:
            if net != target: stamp_pad(layers, cx, cy, kind, a, bb, rot, cr)
        owner = {}; via_owner = {}
        nets_here = {r[0] for r in self.segs} | {r[0] for r in self.vias}
        # v1.7.1 (issue #2): per-cell owner BITMASKS over `nets`. The old
        # rule (first net owns; cells stamped by >= 2 nets turn HARD)
        # made every minimum-spaced parallel pair an unpayable wall --
        # two 0.2 mm tracks at 0.20 gap have overlapping 0.33 mm halos
        # along the whole corridor, yet ripping ONE of them frees it.
        # Now every touching net is in the cell's mask and cutset_astar
        # charges phi for each newly touched net.
        nets = sorted(n for n in nets_here if n not in ignore_nets and n != 'GND')
        bit_of = {n: 1 << k for k, n in enumerate(nets)}
        order = list(nets)
        if 'GND' in nets_here and 'GND' not in ignore_nets: order.append('GND')
        for n in order:
            tm = np.zeros((NL, NY, NX), dtype=bool)
            vm = np.zeros((NY, NX), dtype=bool)
            for net, li, x1, y1, x2, y2, hr in self.segs:
                if net == n: stamp_seg(tm, vm, li, x1, y1, x2, y2, hr)
            for net, cx, cy, r in self.vias:
                if net == n: stamp_disc(tm, vm, range(NL), cx, cy, r)
            if n == 'GND':
                hard |= tm; vhard |= vm
                continue
            bn = bit_of[n]
            for li, j, i in zip(*np.nonzero(tm)):
                key = (int(li), int(j), int(i)); owner[key] = owner.get(key, 0) | bn
            for j, i in zip(*np.nonzero(vm)):
                key = (int(j), int(i)); via_owner[key] = via_owner.get(key, 0) | bn
        # v1.12 (#63): skeleton corridors are HARD in the owners
        # grid too -- a cut-set path may rip priced victims, but it
        # may not sever a pour skeleton and call the pour a victim.
        if SKELETON_ON:
            for sk in self.skeletons:
                if sk['net'] == target: continue
                for (x1, y1, x2, y2) in sk['segs']:
                    stamp_seg(hard, vhard, sk['layer_idx'], x1, y1, x2, y2, sk['width'] / 2)
        return hard, vhard, owner, via_owner, nets

    def cutset_astar(self, blocked, via_block, owner, via_owner, start_xy, goal_xy, start_layers, goal_layers, phi, *, nets, extra_layers=()):
        # v1.3.1 phase (b), v3 Stage A: ONE A* over the soft grid.
        # Entering a cell owned by foreign net v is allowed at a
        # one-time rip penalty phi[v] (label = (cell, victim bitmask),
        # Pareto-pruned per cell). The cheapest path's victim set is
        # the minimum-penalty cut-set -- including jointly-blocking
        # sets of size >= 2 that per-victim probing can never find.
        si = self.cell(*start_xy); gi = self.cell(*goal_xy)
        nets = list(nets)
        bit = {v: 1 << k for k, v in enumerate(nets)}
        def _charge(cb, mask):
            # phi for every net in cell mask cb not already in the label
            add = 0.0; new = cb & ~mask
            while new:
                lb = new & -new; add += phi.get(nets[lb.bit_length() - 1], 100.0); new ^= lb
            return add, mask | cb
        def _h(j, i):
            dx, dy = abs(i - gi[0]), abs(j - gi[1])
            return max(dx, dy) + 0.41421356 * min(dx, dy)
        labels = {}
        prev = {}
        pq = []
        nid = [0]
        def push(cost, li, j, i, mask, parent):
            key = (li, j, i)
            lst = labels.setdefault(key, [])
            for (c0, m0) in lst:
                if c0 <= cost and (m0 & mask) == m0: return
            lst[:] = [(c0, m0) for (c0, m0) in lst if not (cost <= c0 and (mask & m0) == m0)]
            if len(lst) >= 8: return
            lst.append((cost, mask))
            nid[0] += 1
            prev[nid[0]] = (parent, li, j, i, mask)
            heapq.heappush(pq, (cost + _h(j, i), cost, nid[0]))
        extra = set(extra_layers)
        allowed = self.routable | extra
        for l in start_layers:
            if l in allowed: push(0.0, l, si[1], si[0], 0, 0)
        goalset = {(l, gi[1], gi[0]) for l in goal_layers if l in allowed and not blocked[l, gi[1], gi[0]]}
        if not goalset: return None, None
        found = None; pops = 0
        while pq:
            _f, dcur, this = heapq.heappop(pq)
            parent, li, j, i, mask = prev[this]
            if (dcur, mask) not in labels.get((li, j, i), []): continue
            pops += 1
            if pops > 600000: return None, None
            if (li, j, i) in goalset: found = this; break
            for dj, di in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nj, ni = j + dj, i + di
                if not (0 <= nj < self.NY and 0 <= ni < self.NX) or blocked[li, nj, ni]: continue
                if dj and di and (blocked[li, j + dj, i] or blocked[li, j, i + di]): continue
                add, nmask = _charge(owner.get((li, nj, ni), 0), mask)
                push(dcur + (1.41421356 if dj and di else 1.0) * (1.0 if li in extra else self._lfac[li]) + add, li, nj, ni, nmask, this)  # v1.11 (#73): priced-layer factor, as in astar; v1.11.2 (#103): extra (own-plane) layers at unit cost, as in astar
            if not via_block[j, i]:
                vo = via_owner.get((j, i), 0)
                for ol in allowed:
                    if ol == li or blocked[ol, j, i]: continue
                    add, nmask = _charge(vo | owner.get((ol, j, i), 0), mask)
                    push(dcur + 25.0 + add, ol, j, i, nmask, this)
        if found is None: return None, None
        chain = []
        cur = found
        while cur:
            _p, li, j, i, mask = prev[cur]
            chain.append((li, j, i, mask))
            cur = _p
        chain.reverse()
        pts = [(li, round(float(self.gx[i]), 2), round(float(self.gy[j]), 2)) for (li, j, i, _m) in chain]
        simp = [pts[0]]
        for a, bp, c in zip(pts, pts[1:], pts[2:]):
            if bp[0] != a[0] or bp[0] != c[0]: simp.append(bp); continue
            v1 = (bp[1] - a[1], bp[2] - a[2]); v2 = (c[1] - bp[1], c[2] - bp[2])
            if abs(v1[0] * v2[1] - v1[1] * v2[0]) > 1e-9: simp.append(bp)
        simp.append(pts[-1])
        victims = tuple(sorted((v for v in nets if chain[-1][3] & bit[v]), key=lambda v: -phi.get(v, 100.0)))
        return simp, victims

def seg_seg_dist(s1, s2):
    def orient(ax, ay, bx, by, cx, cy): return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    x1, y1, x2, y2 = s1; x3, y3, x4, y4 = s2
    d1, d2, d3, d4 = orient(x3, y3, x4, y4, x1, y1), orient(x3, y3, x4, y4, x2, y2), orient(x1, y1, x2, y2, x3, y3), orient(x1, y1, x2, y2, x4, y4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)): return 0.0
    return min(seg_pt(x1, y1, x3, y3, x4, y4), seg_pt(x2, y2, x3, y3, x4, y4),
               seg_pt(x3, y3, x1, y1, x2, y2), seg_pt(x4, y4, x1, y1, x2, y2))

def board_clearance(b):
    """Largest effective netclass clearance among the board's nets --
    the board's own DRC rule (v1.2.3: replaces the hard-coded 0.20)."""
    names = set()
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname(): names.add(p.GetNetname())
    best = 0.0
    try:
        ns = b.GetDesignSettings().m_NetSettings
        for n in names:
            try: best = max(best, mm(ns.GetEffectiveNetClass(n).GetClearance()))
            except Exception: pass
    except Exception:
        pass
    return best if best > 0 else 0.20

def net_class_via(b, netname):
    """(diameter, drill) of the net's effective netclass via."""
    try:
        nc = b.GetDesignSettings().m_NetSettings.GetEffectiveNetClass(netname)
        d, dr = mm(nc.GetViaDiameter()), mm(nc.GetViaDrill())
        if 0.3 <= d <= 1.2 and 0.1 <= dr < d: return d, dr
    except Exception:
        pass
    return 0.60, 0.30

def net_class_width(b, netname, default):
    """Effective netclass track width (KiCad 10 NET_SETTINGS).
    v1.2.2: finish legs, victim re-bridges and heal bridges are
    planned and drawn at the target net's own class width instead
    of a fixed 0.20/0.25 -- a Power-class net gets its 0.5 mm."""
    try:
        nc = b.GetDesignSettings().m_NetSettings.GetEffectiveNetClass(netname)
        wd = mm(nc.GetTrackWidth())
        if 0.10 <= wd <= 1.00: return wd
    except Exception:
        pass
    return default


def add_seg(b, nets, netname, li, x1, y1, x2, y2, w=0.20):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(nm(x1), nm(y1))); t.SetEnd(pcbnew.VECTOR2I(nm(x2), nm(y2)))
    t.SetWidth(nm(w)); t.SetLayer(copper_stack(b)[li])
    t.SetNetCode(nets[netname].GetNetCode()); b.Add(t)

def add_via(b, nets, netname, x, y, w=0.60, drill=0.30):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(nm(x), nm(y)))
    v.SetWidth(nm(w)); v.SetDrill(nm(drill))
    v.SetNetCode(nets[netname].GetNetCode()); b.Add(v)

def path_segs_vias(simp):
    segs = [(a[0], a[1], a[2], bb[1], bb[2]) for a, bb in zip(simp, simp[1:]) if a[0] == bb[0]]
    vias = [(bb[1], bb[2]) for a, bb in zip(simp, simp[1:]) if a[0] != bb[0]]
    return segs, vias

# ---------------------------------------------------------------- finish_leg
def _pad_clusters(b, conn, netname):
    pads = []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() != netname: continue
            nl = b.GetCopperLayerCount()
            layers = set()
            if p.IsOnLayer(pcbnew.F_Cu): layers.add(0)
            if p.IsOnLayer(pcbnew.B_Cu): layers.add(nl - 1)
            if p.GetDrillSize().x > 0: layers = set(range(nl))
            pads.append((fp.GetReference() + '.' + p.GetNumber(), mm(p.GetPosition().x), mm(p.GetPosition().y), layers, p))
    clusters = []
    for (name, x, y, layers, p) in pads:
        ids = set((round(mm(it.GetPosition().x), 2), round(mm(it.GetPosition().y), 2)) for it in conn.GetConnectedItems(p) if it.Type() == pcbnew.PCB_PAD_T)
        key = (round(x, 2), round(y, 2))
        if not any(key in cs for cs in clusters): clusters.append(ids)
    return pads, clusters

def _ncomp(b, conn, netname):
    return len(_pad_clusters(b, conn, netname)[1])

def _comp_of(pads, clusters):
    m = {}
    for (name, x, y, layers, p) in pads:
        for ci, cs in enumerate(clusters):
            if (round(x, 2), round(y, 2)) in cs: m[name] = ci; break
    return m

def _stamp_inner_via_blocks(b, via_block, grid, ignore):
    # Belt-and-braces (kept deliberately, v1.3.3): re-stamps foreign inner
    # tracks into via_block for call sites whose via_block did not come
    # from Grid.build. A via
    # barrel pierces every layer: block via cells near foreign inner tracks.
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T: continue
        lay = t.GetLayer()
        if lay == pcbnew.F_Cu or lay == pcbnew.B_Cu: continue
        if t.GetNetname() in ignore: continue
        x1, y1, x2, y2 = mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y)
        rad = mm(t.GetWidth()) / 2 + grid.via_r + grid.clr_plan
        i0, i1 = max(0, int((min(x1, x2) - rad - grid.X0) / PITCH)), min(grid.NX - 1, int((max(x1, x2) + rad - grid.X0) / PITCH) + 1)
        j0, j1 = max(0, int((min(y1, y2) - rad - grid.Y0) / PITCH)), min(grid.NY - 1, int((max(y1, y2) + rad - grid.Y0) / PITCH) + 1)
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                if seg_pt(grid.gx[i], grid.gy[j], x1, y1, x2, y2) < rad: via_block[j, i] = True

def _bridge_candidates(b, conn, net):
    # v1.8 (#1): bridge endpoints from _comp_points -- pads + vias +
    # surviving track endpoints per fragment -- not pads alone. A
    # ripped victim's severed track ends are usually its closest
    # cross-fragment copper; pad-only pairs forced the search to
    # start from the farthest points on the board. Returns up to 6
    # closest cross-cluster point pairs plus the largest-cluster
    # pair, as (S, Gp) tuples shaped like pad tuples (name, x, y,
    # layers) with name=None. Shared by _bridge_plan and
    # _finish_attempt so the probe and production stay in step.
    _pads, _cof, pts = _comp_points(b, conn, net)
    ends = []
    for ci, plist in pts.items():
        for (x, y, layers) in plist:
            ends.append((ci, (None, x, y, layers)))
    ranked = []
    for (ci1, sp) in ends:
        for (ci2, gp) in ends:
            if ci1 == ci2: continue
            ranked.append((math.hypot(sp[1] - gp[1], sp[2] - gp[2]), sp, gp))
    ranked.sort(key=lambda t: t[0])
    def _pkey(sp, gp):
        # v1.8.1 (#9): canonical UNDIRECTED pair key -- the v1.8.0 key
        # was directional, so (P,Q) and (Q,P) both passed the dedupe
        # and the 6-candidate cap held only 3 distinct pairs, each
        # searched twice on the same undirected grid.
        a = (round(sp[1], 2), round(sp[2], 2)); bq = (round(gp[1], 2), round(gp[2], 2))
        return (a, bq) if a <= bq else (bq, a)
    cand = []; seenp = set(); skipped = []
    for (d, sp, gp) in ranked:
        key = _pkey(sp, gp)
        if key in seenp: continue
        seenp.add(key)
        # Diversity stride: a pair whose both endpoints sit within
        # 1 mm of an already-picked pair (either orientation) adds
        # little; defer it, backfill only if <6 diverse pairs exist.
        if any((math.hypot(sp[1] - a[1], sp[2] - a[2]) < 1.0 and math.hypot(gp[1] - bq[1], gp[2] - bq[2]) < 1.0) or
               (math.hypot(sp[1] - bq[1], sp[2] - bq[2]) < 1.0 and math.hypot(gp[1] - a[1], gp[2] - a[2]) < 1.0)
               for (a, bq) in cand):
            skipped.append((sp, gp)); continue
        cand.append((sp, gp))
        if len(cand) >= 6: break
    for pair in skipped:
        if len(cand) >= 6: break
        cand.append(pair)
    sizes = {ci: len(plist) for ci, plist in pts.items()}
    big = sorted(sizes, key=lambda k: -sizes[k])[:2]
    if len(big) == 2:
        sp = (None, pts[big[0]][0][0], pts[big[0]][0][1], pts[big[0]][0][2])
        gp = (None, pts[big[1]][0][0], pts[big[1]][0][1], pts[big[1]][0][2])
        if _pkey(sp, gp) not in seenp: cand.append((sp, gp))
    return cand

def _bridge_plan(b, net, before, margins=(12, 20)):
    # Plan-only twin of the v1.6 bridge loop inside _finish_attempt:
    # same candidate pairs, window escalation, bump ladder and
    # keepbacks, but returns (segs, vias) for ONE fragment merge
    # instead of drawing. Used by cmd_finish_probe (attempt
    # selection) -- keep the two in step.
    conn = b.GetConnectivity(); conn.Build(b)
    pads, clusters = _pad_clusters(b, conn, net)
    if len(clusters) <= before: return ([], [])
    cand = _bridge_candidates(b, conn, net)
    if not cand: return None
    bw = net_class_width(b, net, W_TRACK); vd, vdr = net_class_via(b, net)
    for (S, Gp) in cand:
        for margin in margins:
            win = (min(S[1], Gp[1]) - margin, max(S[1], Gp[1]) + margin, min(S[2], Gp[2]) - margin, max(S[2], Gp[2]) + margin)
            gtest = Grid(b, win, track_w=bw, via_r=vd / 2)
            if gtest.NX * gtest.NY * gtest.nl > 4000000: continue
            for bump in (0.0, 0.05, 0.10):
                g = Grid(b, win, track_w=bw, via_r=vd / 2)
                if bump:
                    g.clr_plan = board_clearance(b) + 0.03 + bump
                    g.inf = bw / 2 + g.clr_plan
                blocked, via_block = g.build({net}, target=net)
                _stamp_edge_keepback(b, g, blocked, via_block, bw, vd / 2)
                _stamp_hole_keepback(b, g, blocked, via_block, bw, vdr / 2, net=net)
                g.free_disc(blocked, via_block, S[1], S[2], layers=tuple(S[3]))
                g.free_disc(blocked, via_block, Gp[1], Gp[2], layers=tuple(Gp[3]))
                _stamp_inner_via_blocks(b, via_block, g, {net})
                simp, _c = g.astar(blocked, via_block, (S[1], S[2]), (Gp[1], Gp[2]), tuple(S[3]), tuple(Gp[3]))
                if simp is None: break
                segs, vias = path_segs_vias(simp)
                if _new_items_clean(b, net, segs, vias, g.keepouts, w=bw):
                    return (segs, vias)
    return None

def cmd_finish_probe(board_path, net, victims):
    # Bridge-aware attempt selection (v1.7): dry-run one finish
    # attempt -- plan the leg, rip the victims in memory, draw the
    # leg, then require a _bridge_plan for every fragment merge
    # each victim needs. Predicts the dominant failure class
    # measured in v1.5/v1.6: legs that route but strand their
    # victims unbridgeably. Mirrors cmd_finish_attempt's bump
    # ladder: a bump-0 leg can graze post-rip and fail the exact
    # check where a bumped re-plan passes (the /TMS lesson).
    for bump in (0.0, 0.05, 0.10):
        early, A, Bp, simp, window, cost = _plan_path(board_path, net, tuple(victims), bump)
        if early is not None:
            return {'bridgeable': False, 'reason': early.get('status'), 'victims': {}}
        b = pcbnew.LoadBoard(board_path); refill(b)
        conn = b.GetConnectivity(); conn.Build(b)
        before = {v: _ncomp(b, conn, v) for v in victims}
        tw = net_class_width(b, net, W_TRACK)
        leg_segs, leg_vias = path_segs_vias(simp)
        # Rip EXACTLY as _finish_attempt does: only victim copper
        # the leg's geometry violates, two-phase. Ripping ALL of a
        # victim's tracks fully strips the net, which corrupts this
        # KiCad build's net-info (lookups degrade to SwigPyObjects
        # -- the v1.1 lesson recorded in _finish_attempt) and also
        # answers a different question than production asks.
        CLR = board_clearance(b)
        vr_leg = net_class_via(b, net)[0] / 2
        for v in victims:
            hits = []
            for t in list(b.GetTracks()):
                if t.GetNetname() != v: continue
                hit = False
                if t.Type() == pcbnew.PCB_VIA_T:
                    cx, cy = mm(t.GetPosition().x), mm(t.GetPosition().y)
                    vr_v = mm(t.GetWidth(pcbnew.F_Cu)) / 2
                    hit = any(seg_seg_dist((cx, cy, cx, cy), (x1, y1, x2, y2)) < vr_v + tw / 2 + CLR for (_, x1, y1, x2, y2) in leg_segs)
                    hit = hit or any(math.hypot(cx - vx, cy - vy) < vr_v + vr_leg + CLR for (vx, vy) in leg_vias)
                else:
                    x1, y1, x2, y2 = mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y)
                    hr = mm(t.GetWidth()) / 2
                    for (vx, vy) in leg_vias:
                        if seg_pt(vx, vy, x1, y1, x2, y2) < hr + vr_leg + CLR: hit = True
                    li = {lay: k for k, lay in enumerate(copper_stack(b))}.get(t.GetLayer())
                    if li is not None:
                        for (ml, mx1, my1, mx2, my2) in leg_segs:
                            if ml == li and seg_seg_dist((x1, y1, x2, y2), (mx1, my1, mx2, my2)) < hr + tw / 2 + CLR: hit = True
                if hit: hits.append(t)
            for t in hits: b.Remove(t)
        nets = {n: b.FindNet(n) for n in [net] + list(victims)}
        if not _new_items_clean(b, net, leg_segs, leg_vias, Grid(b, window).keepouts, w=tw):
            continue  # grazed leg at this bump; re-plan higher
        for (li, x1, y1, x2, y2) in leg_segs: add_seg(b, nets, net, li, x1, y1, x2, y2, tw)
        _vd, _vdr = net_class_via(b, net)
        for (x, y) in leg_vias: add_via(b, nets, net, x, y, _vd, _vdr)
        verdict = {}
        for v in victims:
            ok = True
            for _m in range(4):
                conn.Build(b)
                if _ncomp(b, conn, v) <= before[v]: break
                plan = _bridge_plan(b, v, before[v])
                if not plan: ok = False; break
                bw_v = net_class_width(b, v, W_TRACK)
                for (li, x1, y1, x2, y2) in plan[0]: add_seg(b, nets, v, li, x1, y1, x2, y2, bw_v)
                _bvd, _bvdr = net_class_via(b, v)
                for (x, y) in plan[1]: add_via(b, nets, v, x, y, _bvd, _bvdr)
            conn.Build(b)
            if _ncomp(b, conn, v) > before[v]: ok = False
            verdict[v] = 'ok' if ok else 'no_bridge'
        res = {'bridgeable': all(x == 'ok' for x in verdict.values()), 'victims': verdict}
        if bump: res['clr_bump'] = bump
        return res
    return {'bridgeable': False, 'reason': 'leg_dirty', 'victims': {}}

def _finish_attempt(board_path, out_path, net, A, Bp, simp, victims, window):
    b = pcbnew.LoadBoard(board_path); refill(b)
    conn = b.GetConnectivity(); conn.Build(b)
    _, clusters_before = _pad_clusters(b, conn, net)
    tw = net_class_width(b, net, W_TRACK)
    victim_before = {v: _ncomp(b, conn, v) for v in victims}
    leg_segs, leg_vias = path_segs_vias(simp)
    CLR = board_clearance(b)
    # v1.3 Y4: rip-up hit tests use REAL via radii -- the leg's vias are
    # placed at the target net's class size and each victim via has its
    # own; the fixed VIA_R under-ripped larger vias, leaving overlaps
    # the exact checker then (correctly) rejected the whole leg for.
    vr_leg = net_class_via(b, net)[0] / 2
    for v in victims:
        # Two-phase delete: collect hits first, remove after the scan.
        # Removing tracks WHILE iterating the GetTracks() snapshot
        # corrupts the board's net-info in KiCad 10's binding (lookups
        # degrade to bare SwigPyObjects) -- measured during v1.1 work.
        hits = []
        for t in list(b.GetTracks()):
            if t.GetNetname() != v: continue
            hit = False
            if t.Type() == pcbnew.PCB_VIA_T:
                cx, cy = mm(t.GetPosition().x), mm(t.GetPosition().y)
                vr_v = mm(t.GetWidth(pcbnew.F_Cu)) / 2
                hit = any(seg_seg_dist((cx, cy, cx, cy), (x1, y1, x2, y2)) < vr_v + tw / 2 + CLR for (_, x1, y1, x2, y2) in leg_segs)
                hit = hit or any(math.hypot(cx - vx, cy - vy) < vr_v + vr_leg + CLR for (vx, vy) in leg_vias)
            else:
                x1, y1, x2, y2 = mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y)
                hr = mm(t.GetWidth()) / 2
                for (vx, vy) in leg_vias:
                    if seg_pt(vx, vy, x1, y1, x2, y2) < hr + vr_leg + CLR: hit = True
                li = {lay: k for k, lay in enumerate(copper_stack(b))}.get(t.GetLayer())
                if li is not None:
                    for (ml, mx1, my1, mx2, my2) in leg_segs:
                        if ml == li and seg_seg_dist((x1, y1, x2, y2), (mx1, my1, mx2, my2)) < hr + tw / 2 + CLR: hit = True
            if hit: hits.append(t)
        for t in hits: b.Remove(t)
    # Fetch the net map only AFTER the deletions above: stripping a
    # victim's copper invalidates a previously fetched NETNAMES_MAP
    # (measured: lookups degrade to bare SwigPyObjects once the victim
    # is fully stripped), which crashed add_seg in v1.1 development.
    nets = b.GetNetsByName()
    # v1.2.4: exact-geometry check of the leg BEFORE materialising it
    # (v3 audit, bug #2) -- a faulting attempt fails here and the caller
    # tries the next victim set, instead of the pipeline reverting the
    # finished board after a full DRC and discarding later attempts.
    if not _new_items_clean(b, net, leg_segs, leg_vias, Grid(b, window).keepouts, w=tw):
        return None
    for (li, x1, y1, x2, y2) in leg_segs: add_seg(b, nets, net, li, x1, y1, x2, y2, tw)
    _vd, _vdr = net_class_via(b, net)
    for (x, y) in leg_vias: add_via(b, nets, net, x, y, _vd, _vdr)
    refill(b)
    # Re-bridge each victim pad-cluster to pad-cluster (up to 4 bridges).
    # v1 bridged a single pair of track stubs, which silently failed
    # whenever the victim was cut into 3+ pieces -- the usual SPI-cluster
    # case -- and the whole finish rolled back.
    # v1.6: the single-closest-pair bridge was the measured wall for
    # the whole displacement class (finish_leg + ILP alike): one
    # blocked pair, one quantisation graze, or one edge/hole-blind
    # plan ended the attempt. Bridges now try up to 6 closest
    # cross-cluster pairs plus the largest-cluster pair, at +-12 then
    # +-20 mm windows, with the clearance-bump ladder and the
    # edge/hole keepbacks long_finish already uses. Every drawn
    # bridge still passes _new_items_clean before materialising,
    # and the final cluster verification below is unchanged.
    for v in victims:
        bw_v = net_class_width(b, v, W_TRACK)
        _bvd, _bvdr = net_class_via(b, v)
        for _round in range(4):
            conn.Build(b)
            vpads, vclusters = _pad_clusters(b, conn, v)
            if len(vclusters) <= victim_before[v]: break
            cand = _bridge_candidates(b, conn, v)
            if not cand: break
            bridged = False
            for (S, Gp) in cand:
                if bridged: break
                for margin in (12, 20):
                    if bridged: break
                    win2 = (min(S[1], Gp[1]) - margin, max(S[1], Gp[1]) + margin, min(S[2], Gp[2]) - margin, max(S[2], Gp[2]) + margin)
                    gtest = Grid(b, win2, track_w=bw_v, via_r=_bvd / 2)
                    if gtest.NX * gtest.NY * gtest.nl > 4000000: continue
                    for bump in (0.0, 0.05, 0.10):
                        g2 = Grid(b, win2, track_w=bw_v, via_r=_bvd / 2)
                        if bump:
                            g2.clr_plan = board_clearance(b) + 0.03 + bump
                            g2.inf = bw_v / 2 + g2.clr_plan
                        blocked, via_block = g2.build({v}, target=v)
                        _stamp_edge_keepback(b, g2, blocked, via_block, bw_v, _bvd / 2)
                        _stamp_hole_keepback(b, g2, blocked, via_block, bw_v, _bvdr / 2, net=v)
                        g2.free_disc(blocked, via_block, S[1], S[2], layers=tuple(S[3]))
                        g2.free_disc(blocked, via_block, Gp[1], Gp[2], layers=tuple(Gp[3]))
                        _stamp_inner_via_blocks(b, via_block, g2, {v})
                        bsimp, _bc = g2.astar(blocked, via_block, (S[1], S[2]), (Gp[1], Gp[2]), tuple(S[3]), tuple(Gp[3]))
                        if bsimp is None: break
                        bsegs, bvias = path_segs_vias(bsimp)
                        if not _new_items_clean(b, v, bsegs, bvias, g2.keepouts, w=bw_v): continue
                        for (li, x1, y1, x2, y2) in bsegs: add_seg(b, nets, v, li, x1, y1, x2, y2, bw_v)
                        for (x, y) in bvias: add_via(b, nets, v, x, y, _bvd, _bvdr)
                        refill(b)
                        bridged = True
                        break
            if not bridged: break
    conn.Build(b)
    if _ncomp(b, conn, net) >= len(clusters_before): return None
    for v in victims:
        if _ncomp(b, conn, v) > victim_before[v]: return None
    pcbnew.SaveBoard(out_path, b)
    return {'status': 'routed', 'net': net, 'from': A[0], 'to': Bp[0], 'displaced_nets': list(victims)}

def _plan_attempts(board_path, net):
    # Probe one board state and return (A, Bp, attempts): the closest
    # cross-cluster pad pair and the ordered victim tuples to try.
    # Read-only (load + refill + grid plans); safe to call per round.
    b0 = pcbnew.LoadBoard(board_path); refill(b0)
    conn0 = b0.GetConnectivity(); conn0.Build(b0)
    pads0, clusters0 = _pad_clusters(b0, conn0, net)
    if len(pads0) < 2 or len(clusters0) <= 1:
        return None
    cof = _comp_of(pads0, clusters0)
    best = None
    for a in pads0:
        for c in pads0:
            if cof.get(a[0]) == cof.get(c[0]): continue
            d = math.hypot(a[1] - c[1], a[2] - c[2])
            if best is None or d < best[0]: best = (d, a, c)
    _, A, Bp = best
    start_xy, goal_xy = (A[1], A[2]), (Bp[1], Bp[2])
    window = (min(start_xy[0], goal_xy[0]) - 6, max(start_xy[0], goal_xy[0]) + 6,
              min(start_xy[1], goal_xy[1]) - 6, max(start_xy[1], goal_xy[1]) + 6)
    grid0 = Grid(b0, window, track_w=net_class_width(b0, net, W_TRACK), via_r=net_class_via(b0, net)[0] / 2)
    def route_plan(ignore):
        blocked, via_block = grid0.build(ignore, target=net)
        _tw = net_class_width(b0, net, W_TRACK)
        _stamp_edge_keepback(b0, grid0, blocked, via_block, _tw, net_class_via(b0, net)[0] / 2)
        _stamp_hole_keepback(b0, grid0, blocked, via_block, _tw, net_class_via(b0, net)[1] / 2, net=net)
        grid0.free_disc(blocked, via_block, *start_xy, layers=tuple(A[3]))
        grid0.free_disc(blocked, via_block, *goal_xy, layers=tuple(Bp[3]))
        # Stamp inner-track via blocks AFTER freeing the pad discs: a
        # via at the pad cell still pierces inner layers, and freeing
        # first let one land on a foreign In2 track (measured short).
        _stamp_inner_via_blocks(b0, via_block, grid0, ignore)
        return grid0.astar(blocked, via_block, start_xy, goal_xy, tuple(A[3]), tuple(Bp[3]))
    cands = set()
    for row in grid0.segs:
        if row[0] != net and row[0] != 'GND': cands.add(row[0])
    for row in grid0.vias:
        if row[0] != net and row[0] != 'GND': cands.add(row[0])
    # victim policy (learned the hard way): A* cost alone once picked
    # V_BAT+ -- a 10-pad 0.5 mm power net -- and the re-bridge failed,
    # leaving it split. Prefer short local signal victims: penalise
    # wide/power nets and many-pad nets heavily.
    width_of, pads_of = {}, {}
    for t in b0.GetTracks():
        if t.Type() != pcbnew.PCB_VIA_T:
            width_of[t.GetNetname()] = max(width_of.get(t.GetNetname(), 0), mm(t.GetWidth()))
    for fp in b0.GetFootprints():
        for p in fp.Pads():
            pads_of[p.GetNetname()] = pads_of.get(p.GetNetname(), 0) + 1
    def victim_score(cand, c2):
        sc = c2 + 10.0 * pads_of.get(cand, 0)
        if width_of.get(cand, 0) >= 0.4: sc += 1000.0
        if cand.startswith('+') or cand in ('V_BAT+', '3V3', 'GND') or cand.endswith('+'): sc += 1000.0
        return sc
    singles = []
    for cand in sorted(cands):
        s2, c2 = route_plan({net, cand})
        if s2 is not None: singles.append((victim_score(cand, c2), cand))
    singles.sort()
    attempts = [()]
    if route_plan({net})[0] is None: attempts = []
    # v1.3.1 phase (b): weighted cut-set A* (v3 Stage A). One search
    # over the soft grid discovers the minimum-penalty victim set of
    # ANY size -- the singles probe above can only ever propose pairs
    # drawn from individually-sufficient victims, so two parallel
    # tracks jointly blocking a corridor yielded no attempt at all.
    def phi_of(v):
        sc = 100.0 + 10.0 * pads_of.get(v, 0)
        if width_of.get(v, 0) >= 0.4: sc += 1000.0
        if v.startswith('+') or v in ('V_BAT+', '3V3') or v.endswith('+'): sc += 1000.0
        return sc
    # Scoped to the case it exists for: when the singles probe found
    # candidates, the legacy order already covers k=1 and pairs from
    # singles; the cut-set search costs ~1-3 min on large boards, so
    # it runs only when singles is EMPTY -- exactly the fallacy case
    # (measured on srambo: /~{CI} has no single victim; the cut-set
    # found {/~{BE}, /AUX6}; legs with singles never needed it).
    if not singles:
        hb, hvb, owner, via_owner, cs_nets = grid0.build_owners({net}, target=net)
        grid0.free_disc(hb, hvb, *start_xy, layers=tuple(A[3]))
        grid0.free_disc(hb, hvb, *goal_xy, layers=tuple(Bp[3]))
        _cs_simp, cs_victims = grid0.cutset_astar(hb, hvb, owner, via_owner, start_xy, goal_xy, tuple(A[3]), tuple(Bp[3]), {v: phi_of(v) for v in cs_nets}, nets=cs_nets)
        if cs_victims and 1 <= len(cs_victims) <= 4 and tuple(cs_victims) not in attempts:
            attempts.append(tuple(cs_victims))
    attempts += [(c,) for _, c in singles[:3]]
    if len(singles) >= 2: attempts.append((singles[0][1], singles[1][1]))
    if len(singles) >= 3: attempts.append((singles[0][1], singles[2][1]))
    return (A, Bp, attempts)

def cmd_finish_leg(board_path, out_path, net, attempt_select=False):
    # A net can sit in 3+ fragments (a plane cut by signal tracks is the
    # usual case): one routed leg merges only two, so iterate rounds,
    # RE-PLANNING on the updated board each round (victim candidacy is
    # local to the fragment pair being merged). Each attempt runs in a
    # CHILD process: this KiCad build's pcbnew degrades under repeated
    # load/mutate cycles in one process (GetTracks()/GetNetInfo()
    # start returning bare SwigPyObjects), which killed multi-attempt
    # runs in-process during v1.1 development.
    import subprocess, tempfile
    probe = pcbnew.LoadBoard(board_path); refill(probe)
    pconn = probe.GetConnectivity(); pconn.Build(probe)
    ppads, pclusters = _pad_clusters(probe, pconn, net)
    if len(ppads) < 2:
        return {'status': 'error', 'detail': 'net has fewer than 2 pads'}
    if len(pclusters) <= 1:
        return {'status': 'already_connected', 'net': net}
    attempted = False
    probed_all = []
    routed_res = None
    board_in = board_path
    last_pair = (ppads[0][0], ppads[-1][0])
    for _round in range(3):
        plan = _plan_attempts(board_in, net)
        if plan is None: break
        A, Bp, attempts = plan
        last_pair = (A[0], Bp[0])
        # v1.7: bridge-aware attempt selection -- probe each attempt
        # (child process, plan-only) and order: bridgeable first,
        # then unknown, then not-bridgeable; original price order
        # kept within each class (stable sort). A probe can crash
        # (this KiCad build's pcbnew degrades under load -- a pair
        # probe died at GetTracks() after _plan_path under a
        # 3-container load, measured 2026-11-22): retry once in a
        # fresh process, and a still-failing probe is UNKNOWN,
        # never promoted -- the first cut's crash->bridgeable
        # fallback promoted exactly the unverifiable attempts.
        if attempt_select and len(attempts) > 1:
            probed = []
            for victims in attempts:
                verdict = None
                for _try in range(2):
                    fd, ppath = tempfile.mkstemp(suffix='.json'); os.close(fd)
                    pline = [sys.executable, os.path.abspath(__file__), 'finish_probe',
                             '--board', board_in, '--net', net,
                             '--victims', ','.join(victims), '--result', ppath]
                    if NO_TRACKS_ON_RAW is not None: pline += ['--no-tracks-on', NO_TRACKS_ON_RAW]  # v1.11.2 (#104): forward an explicit '' too
                    if LAYER_COST_RAW: pline += ['--layer-cost', LAYER_COST_RAW]
                    if SKELETON_ON: pline += ['--skeleton']
                    try:
                        subprocess.run(pline, capture_output=True, text=True, timeout=600)
                        pres = json.load(open(ppath))
                        verdict = bool(pres.get('bridgeable'))
                    except Exception:
                        verdict = None
                    try: os.unlink(ppath)
                    except Exception: pass
                    if verdict is not None: break
                probed.append({'victims': list(victims), 'bridgeable': verdict})
            rank = {True: 0, None: 1, False: 2}
            attempts = sorted(attempts, key=lambda vs: rank[next(p['bridgeable'] for p in probed if p['victims'] == list(vs))])
            probed_all.extend(probed)
        round_routed = False
        for victims in attempts:
            fd, rpath = tempfile.mkstemp(suffix='.json'); os.close(fd)
            cmdline = [sys.executable, os.path.abspath(__file__), 'finish_attempt',
                       '--board', board_in, '--out', out_path, '--net', net,
                       '--victims', ','.join(victims), '--result', rpath]
            if NO_TRACKS_ON_RAW is not None: cmdline += ['--no-tracks-on', NO_TRACKS_ON_RAW]  # v1.11.2 (#104): forward an explicit '' too
            if LAYER_COST_RAW: cmdline += ['--layer-cost', LAYER_COST_RAW]
            if SKELETON_ON: cmdline += ['--skeleton']
            try:
                subprocess.run(cmdline, capture_output=True, text=True, timeout=300)
                res = json.load(open(rpath))
            except Exception:
                res = {'status': 'failed'}
            try: os.unlink(rpath)
            except Exception: pass
            if res.get('status') == 'routed':
                routed_res = res; board_in = out_path; round_routed = True; break
            if res.get('status') != 'no_plan': attempted = True
        if not round_routed: break
    if routed_res is not None:
        routed_res = dict(routed_res); routed_res['attempts_probed'] = probed_all
        return routed_res
    return {'status': 'rolled_back' if attempted else 'no_path', 'net': net, 'from': last_pair[0], 'to': last_pair[1], 'attempts_probed': probed_all}

def _plan_path(board_path, net, victims, bump=0.0):
    # Planning half of cmd_finish_attempt, extracted (v1.5 Stage B) so
    # the ILP repair can price candidate routes without executing
    # them. Returns (early, A, Bp, simp, window, cost): early is a
    # result dict to return immediately (error / already_connected /
    # no_plan); when early is None the rest describe the planned leg.
    b0 = pcbnew.LoadBoard(board_path); refill(b0)
    conn0 = b0.GetConnectivity(); conn0.Build(b0)
    pads0, clusters0 = _pad_clusters(b0, conn0, net)
    if len(pads0) < 2:
        return ({'status': 'error', 'detail': 'net has fewer than 2 pads'}, None, None, None, None, None)
    if len(clusters0) <= 1:
        return ({'status': 'already_connected', 'net': net}, None, None, None, None, None)
    cof = _comp_of(pads0, clusters0)
    best = None
    for a in pads0:
        for c in pads0:
            if cof.get(a[0]) == cof.get(c[0]): continue
            d = math.hypot(a[1] - c[1], a[2] - c[2])
            if best is None or d < best[0]: best = (d, a, c)
    _, A, Bp = best
    start_xy, goal_xy = (A[1], A[2]), (Bp[1], Bp[2])
    window = (min(start_xy[0], goal_xy[0]) - 6, max(start_xy[0], goal_xy[0]) + 6,
              min(start_xy[1], goal_xy[1]) - 6, max(start_xy[1], goal_xy[1]) + 6)
    grid0 = Grid(b0, window, track_w=net_class_width(b0, net, W_TRACK), via_r=net_class_via(b0, net)[0] / 2)
    if bump:
        grid0.clr_plan = board_clearance(b0) + 0.03 + bump
        grid0.inf = net_class_width(b0, net, W_TRACK) / 2 + grid0.clr_plan
    ignore = {net} | set(victims)
    blocked, via_block = grid0.build(ignore, target=net)
    _tw = net_class_width(b0, net, W_TRACK)
    _stamp_edge_keepback(b0, grid0, blocked, via_block, _tw, net_class_via(b0, net)[0] / 2)
    _stamp_hole_keepback(b0, grid0, blocked, via_block, _tw, net_class_via(b0, net)[1] / 2, net=net)
    grid0.free_disc(blocked, via_block, *start_xy, layers=tuple(A[3]))
    grid0.free_disc(blocked, via_block, *goal_xy, layers=tuple(Bp[3]))
    _stamp_inner_via_blocks(b0, via_block, grid0, ignore)
    simp, cost = grid0.astar(blocked, via_block, start_xy, goal_xy, tuple(A[3]), tuple(Bp[3]))
    if simp is None:
        return ({'status': 'no_plan', 'net': net}, None, None, None, None, None)
    return (None, A, Bp, simp, window, cost)


def cmd_finish_attempt(board_path, out_path, net, victims):
    # Single-attempt worker for cmd_finish_leg; runs in its own process.
    # v1.5: clearance-bump ladder, the long_finish lesson applied to
    # displacement legs -- a planned leg can graze exact geometry by
    # ~0.06 mm (measured: /TMS leg via 0.143 mm from an /A13 track vs
    # the 0.200 rule, post-rip), and re-planning at a larger planner
    # clearance is cheaper than discarding the victim set. A no_plan
    # at bump 0 ends the attempt (bumps only shrink the path set).
    for bump in (0.0, 0.05, 0.10):
        early, A, Bp, simp, window, _cost = _plan_path(board_path, net, victims, bump=bump)
        if early is not None:
            if early.get('status') == 'no_plan' and bump == 0.0: return early
            if early.get('status') != 'no_plan': return early
            return {'status': 'failed', 'net': net}
        res = _finish_attempt(board_path, out_path, net, A, Bp, simp, tuple(victims), window)
        if res is not None:
            if bump: res = dict(res); res['clr_bump'] = bump
            return res
    return {'status': 'failed', 'net': net}


def _stamp_edge_keepback(b, g, blocked, via_block, tw, vr):
    # The grid has no notion of the board outline, so a board-wide leg
    # can hug the edge closer than the copper_edge_clearance rule
    # allows (measured: +10 copper_edge_clearance on the first
    # long_finish fixture run, all from edge-hugging legs; the local
    # finisher rarely reaches an edge, which is why this stayed
    # hidden). Stamp a band around every Edge.Cuts segment: track
    # centres must stand off edge_clr + tw/2, via centres edge_clr +
    # vr. Non-line shapes are stamped by expanded bbox
    # (conservative). Applies to every layer.
    try: edge_clr = mm(b.GetDesignSettings().m_CopperEdgeClearance)
    except Exception: edge_clr = 0.5
    if edge_clr <= 0: return
    import numpy as np
    gx, gy = g.gx, g.gy
    def stamp_band(x0, y0, x1, y1, rad, mask2d):
        i0 = max(0, int((min(x0, x1) - rad - g.X0) / PITCH)); i1 = min(g.NX - 1, int((max(x0, x1) + rad - g.X0) / PITCH) + 1)
        j0 = max(0, int((min(y0, y1) - rad - g.Y0) / PITCH)); j1 = min(g.NY - 1, int((max(y0, y1) + rad - g.Y0) / PITCH) + 1)
        if i1 < i0 or j1 < j0: return
        xs = gx[i0:i1 + 1][None, :]; ys = gy[j0:j1 + 1][:, None]
        vx, vy = x1 - x0, y1 - y0
        L2 = vx * vx + vy * vy
        if L2 <= 1e-12:
            d = np.hypot(xs - x0, ys - y0)
        else:
            t = np.clip(((xs - x0) * vx + (ys - y0) * vy) / L2, 0.0, 1.0)
            d = np.hypot(xs - (x0 + t * vx), ys - (y0 + t * vy))
        mask2d[j0:j1 + 1, i0:i1 + 1] |= (d < rad)
    # v1.11.2 (#107): footprint-embedded Edge.Cuts (slots, cutouts)
    # is board outline too -- #75 taught the exact checker to see
    # it; this stamp kept looking only at board drawings, so legs
    # were planned into the graze band the checker then vetoed.
    _drawings = list(b.GetDrawings())
    for _fp in b.GetFootprints(): _drawings.extend(list(_fp.GraphicalItems()))
    for rad, is_via in ((edge_clr + tw / 2, False), (edge_clr + vr, True)):
        band = np.zeros((g.NY, g.NX), dtype=bool)
        for dr in _drawings:
            if dr.GetLayer() != pcbnew.Edge_Cuts: continue
            if dr.Type() == pcbnew.PCB_SHAPE_T and dr.GetShape() == pcbnew.SHAPE_T_SEGMENT:
                stamp_band(mm(dr.GetStart().x), mm(dr.GetStart().y), mm(dr.GetEnd().x), mm(dr.GetEnd().y), rad, band)
            else:
                bb = dr.GetBoundingBox()
                stamp_band(mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetTop()), rad, band)
                stamp_band(mm(bb.GetLeft()), mm(bb.GetBottom()), mm(bb.GetRight()), mm(bb.GetBottom()), rad, band)
                stamp_band(mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetLeft()), mm(bb.GetBottom()), rad, band)
                stamp_band(mm(bb.GetRight()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()), rad, band)
        if is_via: via_block |= band
        else:
            for li in range(g.nl): blocked[li] |= band

def _stamp_hole_keepback(b, g, blocked, via_block, tw, vr_drill, net=None):
    # Drilled holes pierce every layer, and hole_clearance (drill
    # edge to foreign copper) is a separate rule from copper
    # clearance. NPTH pads carry ~no copper, so pad stamping leaves
    # them nearly invisible: the first long_finish fixture run put
    # tracks 0.244/0.245 mm from two NPTH holes vs the 0.250 rule --
    # a miss neither the grid nor the exact checker models. Stamp
    # every drilled pad's hole at drill_r + hole_clr + tw/2 (tracks,
    # all layers) and drill_r + hole_clr + vr_drill (via centres).
    try: hole_clr = mm(b.GetDesignSettings().m_HoleClearance)
    except Exception: hole_clr = 0.25
    if hole_clr <= 0: return
    import numpy as np
    gx, gy = g.gx, g.gy
    def stamp_disc(cx, cy, rad, mask2d):
        i0 = max(0, int((cx - rad - g.X0) / PITCH)); i1 = min(g.NX - 1, int((cx + rad - g.X0) / PITCH) + 1)
        j0 = max(0, int((cy - rad - g.Y0) / PITCH)); j1 = min(g.NY - 1, int((cy + rad - g.Y0) / PITCH) + 1)
        if i1 < i0 or j1 < j0: return
        xs = gx[i0:i1 + 1][None, :]; ys = gy[j0:j1 + 1][:, None]
        mask2d[j0:j1 + 1, i0:i1 + 1] |= (np.hypot(xs - cx, ys - cy) < rad)
    holes = []; holes_all = []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            ds = p.GetDrillSize()
            if ds.x <= 0: continue
            _h = (mm(p.GetPosition().x), mm(p.GetPosition().y), max(mm(ds.x), mm(ds.y)) / 2)
            holes_all.append(_h)
            if net is not None and p.GetNetname() == net: continue
            holes.append(_h)
    # #113 (residual of #107): existing VIA drills are drills too --
    # the #75 checker counts them (adrills/fdrills scan GetTracks),
    # but this stamp never saw them, so a via could be planned
    # inside hole_to_hole of an existing via drill and the finished
    # leg was vetoed.  Same split as pads: every via drill into
    # holes_all, foreign ones into holes.
    for t in b.GetTracks():
        if t.Type() != pcbnew.PCB_VIA_T: continue
        _h = (mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetDrillValue()) / 2)
        holes_all.append(_h)
        if net is not None and t.GetNetname() == net: continue
        holes.append(_h)
    band_t = np.zeros((g.NY, g.NX), dtype=bool); band_v = np.zeros((g.NY, g.NX), dtype=bool)
    for (cx, cy, dr) in holes:
        stamp_disc(cx, cy, dr + hole_clr + tw / 2, band_t)
        stamp_disc(cx, cy, dr + hole_clr + vr_drill, band_v)
    # v1.11.2 (#107): hole_to_hole is a physical drill-separation
    # rule and binds SAME-net drills too -- #75 taught the checker
    # (adrills), but this stamp skipped the planned net's own pads
    # entirely, so a via could be planned inside h2h of a same-net
    # TH pad and the finished leg was vetoed. Via centres now stand
    # off every drill by h2h as well (the checker's own fallback
    # when the design setting is unreadable). Track bands stay
    # foreign-only: hole_clearance is a copper rule and same-net
    # copper is exempt from it.
    try: h2h = mm(b.GetDesignSettings().m_HoleToHoleMin)
    except Exception: h2h = hole_clr
    if h2h > 0:
        for (cx, cy, dr) in holes_all:
            stamp_disc(cx, cy, dr + h2h + vr_drill, band_v)
    for li in range(g.nl): blocked[li] |= band_t
    via_block |= band_v

def cmd_long_attempt(board_path, out_path, net):
    # v1.4 phase (c) Stage B-0, single-round worker: large-window HARD
    # A* between MULTIPLE cross-cluster anchor pairs. finish_leg plans
    # only the single closest pair; the dataset-v3 spike (2026-11-22)
    # showed a class of stranded nets whose components sit tens of mm
    # apart with a legal board-wide hard path the local pair's search
    # never reaches (its window is fine -- the PAIR choice and the
    # victim machinery around it are what fail). No victims here:
    # foreign copper is HARD, so a net with no hard path is
    # congestion-class and belongs to the ILP stage, not this one.
    # Drawing reuses _finish_attempt wholesale (exact check, refill,
    # cluster verification), with an empty victim tuple.
    b0 = pcbnew.LoadBoard(board_path); refill(b0)
    conn0 = b0.GetConnectivity(); conn0.Build(b0)
    pads0, clusters0 = _pad_clusters(b0, conn0, net)
    if len(pads0) < 2:
        return {'status': 'error', 'detail': 'net has fewer than 2 pads'}
    if len(clusters0) <= 1:
        return {'status': 'already_connected', 'net': net}
    cof = _comp_of(pads0, clusters0)
    pairs = []
    for a in pads0:
        for c in pads0:
            if cof.get(a[0]) == cof.get(c[0]): continue
            pairs.append((math.hypot(a[1] - c[1], a[2] - c[2]), a, c))
    pairs.sort(key=lambda t: t[0])
    chosen = []; seen = set()
    for d, a, c in pairs:
        key = tuple(sorted((a[0], c[0])))
        if key in seen: continue
        seen.add(key); chosen.append((d, a, c))
        if len(chosen) >= 6: break
    sizes = {}
    for nm, ci in cof.items(): sizes[ci] = sizes.get(ci, 0) + 1
    big = sorted(sizes, key=lambda k: -sizes[k])[:2]
    if len(big) == 2:
        pa = next(p for p in pads0 if cof.get(p[0]) == big[0])
        pc = next(p for p in pads0 if cof.get(p[0]) == big[1])
        if tuple(sorted((pa[0], pc[0]))) not in seen:
            chosen.append((math.hypot(pa[1] - pc[1], pa[2] - pc[2]), pa, pc))
    tw = net_class_width(b0, net, W_TRACK); vr = net_class_via(b0, net)[0] / 2
    for d, A, Bp in chosen:
        start_xy, goal_xy = (A[1], A[2]), (Bp[1], Bp[2])
        window = None
        for margin in (10.0, 6.0):
            w = (min(start_xy[0], goal_xy[0]) - margin, max(start_xy[0], goal_xy[0]) + margin,
                 min(start_xy[1], goal_xy[1]) - margin, max(start_xy[1], goal_xy[1]) + margin)
            g0 = Grid(b0, w, track_w=tw, via_r=vr)
            if g0.NX * g0.NY * g0.nl <= 4000000: window = w; break
        if window is None: continue
        # Exact-check graze ladder: the 0.1 mm grid quantises obstacle
        # stamping, so a long path can be planner-legal yet fail
        # _new_items_clean by ~0.05 mm at one via (measured on the
        # MaxKiv fixture: via 0.150 mm from a foreign track vs the
        # 0.200 rule). Re-plan with a larger planner clearance; each
        # bump only shrinks the path set, so a no-path at bump 0 ends
        # the pair.
        for bump in (0.0, 0.05, 0.10):
            g = Grid(b0, window, track_w=tw, via_r=vr)
            if bump:
                g.clr_plan = board_clearance(b0) + 0.03 + bump
                g.inf = tw / 2 + g.clr_plan
            blocked, via_block = g.build({net}, target=net)
            _stamp_edge_keepback(b0, g, blocked, via_block, tw, vr)
            _stamp_hole_keepback(b0, g, blocked, via_block, tw, net_class_via(b0, net)[1] / 2, net=net)
            g.free_disc(blocked, via_block, *start_xy, layers=tuple(A[3]))
            g.free_disc(blocked, via_block, *goal_xy, layers=tuple(Bp[3]))
            _stamp_inner_via_blocks(b0, via_block, g, {net})
            simp, _c = g.astar(blocked, via_block, start_xy, goal_xy, tuple(A[3]), tuple(Bp[3]))
            if simp is None: break
            res = _finish_attempt(board_path, out_path, net, A, Bp, simp, (), window)
            if res is not None:
                res = dict(res); res['stage'] = 'long_finish'; res['pair_distance_mm'] = round(d, 1)
                if bump: res['clr_bump'] = bump
                return res
    return {'status': 'no_path', 'net': net}

def cmd_long_leg(board_path, out_path, net):
    # Rounds of child-process long_attempt runs (the pcbnew
    # degradation defence, same pattern as cmd_finish_leg): one leg
    # merges two fragments, so a 3+ fragment net needs rounds,
    # re-planning on the updated board each time.
    import subprocess, tempfile
    probe = pcbnew.LoadBoard(board_path); refill(probe)
    pconn = probe.GetConnectivity(); pconn.Build(probe)
    ppads, pclusters = _pad_clusters(probe, pconn, net)
    if len(ppads) < 2:
        return {'status': 'error', 'detail': 'net has fewer than 2 pads'}
    if len(pclusters) <= 1:
        return {'status': 'already_connected', 'net': net}
    board_in = board_path; routed_res = None
    for _round in range(3):
        fd, rpath = tempfile.mkstemp(suffix='.json'); os.close(fd)
        cmdline = [sys.executable, os.path.abspath(__file__), 'long_attempt',
                   '--board', board_in, '--out', out_path, '--net', net, '--result', rpath]
        if NO_TRACKS_ON_RAW is not None: cmdline += ['--no-tracks-on', NO_TRACKS_ON_RAW]  # v1.11.2 (#104): forward an explicit '' too
        if LAYER_COST_RAW: cmdline += ['--layer-cost', LAYER_COST_RAW]
        if SKELETON_ON: cmdline += ['--skeleton']
        try:
            subprocess.run(cmdline, capture_output=True, text=True, timeout=900)
            res = json.load(open(rpath))
        except Exception:
            res = {'status': 'failed'}
        try: os.unlink(rpath)
        except Exception: pass
        if res.get('status') == 'routed':
            routed_res = res; board_in = out_path; continue
        break
    if routed_res is not None: return routed_res
    return {'status': 'no_path', 'net': net}


# ---------------------------------------------------------------- heal
HEAL_W = 0.25        # bridge track width (plane stitch feed)
HEAL_MAX_MM = 14.0   # max launch->target distance for one bridge

def _comp_points(b, conn, netname):
    # Launch/target points per pad-component of a net: pads, vias and
    # outer-layer track endpoints of the whole connected cluster.
    pads, clusters = _pad_clusters(b, conn, netname)
    comp = _comp_of(pads, clusters)
    pts = {}
    def add(ci, x, y, layers):
        pts.setdefault(ci, []).append((x, y, tuple(sorted(layers))))
    for (name, x, y, layers, p) in pads:
        ci = comp.get(name)
        if ci is not None: add(ci, x, y, layers)
    # Dedupe by geometric key, never id(): pcbnew SWIG wrappers are
    # per-call temporaries whose id() values get reused after GC, which
    # silently dropped a fragment's only via from the point list.
    seen = set()
    for (name, x, y, layers, p) in pads:
        ci = comp.get(name)
        if ci is None: continue
        for it in conn.GetConnectedItems(p):
            if it.Type() == pcbnew.PCB_VIA_T:
                vx, vy = mm(it.GetPosition().x), mm(it.GetPosition().y)
                key = ('V', round(vx, 2), round(vy, 2))
                if key in seen: continue
                seen.add(key); add(ci, vx, vy, tuple(range(b.GetCopperLayerCount())))
            elif it.Type() == pcbnew.PCB_TRACE_T:
                lay = {lay: k for k, lay in enumerate(copper_stack(b))}.get(it.GetLayer())
                if lay is not None:
                    sx, sy = mm(it.GetStart().x), mm(it.GetStart().y)
                    ex, ey = mm(it.GetEnd().x), mm(it.GetEnd().y)
                    key = ('T', lay, round(sx, 2), round(sy, 2), round(ex, 2), round(ey, 2))
                    if key in seen: continue
                    seen.add(key)
                    add(ci, sx, sy, (lay,)); add(ci, ex, ey, (lay,))
    # v1.8.1 (#9): coalesce per (cluster, rounded xy), unioning layer
    # sets -- a polyline bend vertex arrived once per segment and a
    # via + track end at one coordinate arrived as two entries.
    for ci in pts:
        merged = {}
        for (x, y, layers) in pts[ci]:
            k = (round(x, 2), round(y, 2))
            if k in merged: merged[k][2].update(layers)
            else: merged[k] = [x, y, set(layers)]
        pts[ci] = [(x, y, tuple(sorted(ls))) for (x, y, ls) in merged.values()]
    return pads, comp, pts


def _new_items_clean(b, net, segs, vias, keepouts, w=HEAL_W):
    # Exact-geometry DRC check of just the items a heal bridge is about
    # to add, against all foreign copper (the grid planner works at
    # 0.23 planning clearance on a 0.1 pitch and can still land a via
    # 0.19 mm from a track -- one such bridge cost a whole heal its
    # gate revert). Returns True only if every new item clears every
    # foreign item by the board's 0.20 mm rule and respects keepouts.
    CLR = board_clearance(b)
    # v1.2.5: foreign pads are tested by their true-shape SDF (shared
    # with the Grid), foreign vias at their real radius, and the new
    # items' vias at the net's own via radius -- the disc model here
    # silently vetoed bridges the v1.2.3 planner had made legal.
    vr = net_class_via(b, net)[0] / 2
    # v1.3 phase (a): buckets are stack indices -- legs and bridges can
    # run on inner routable layers now, so foreign copper is collected
    # per stack layer, not just the outer pair.
    _stack = copper_stack(b); _lidx = {lay: k for k, lay in enumerate(_stack)}; _nl = len(_stack)
    fsegs = {k: [] for k in range(_nl)}; fdiscs = {k: [] for k in range(_nl)}; fpads = {k: [] for k in range(_nl)}; fall = []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() == net: continue
            prm = _pad_sdf_params(p)
            if p.GetDrillSize().x > 0:
                for k in range(_nl): fpads[k].append(prm)
                fall.append(('pad', prm))
            else:
                if p.IsOnLayer(pcbnew.F_Cu): fpads[0].append(prm); fall.append(('pad', prm))
                if p.IsOnLayer(pcbnew.B_Cu): fpads[_nl - 1].append(prm)
    for t in b.GetTracks():
        if t.GetNetname() == net: continue
        if t.Type() == pcbnew.PCB_VIA_T:
            vx, vy = mm(t.GetPosition().x), mm(t.GetPosition().y)
            vrf = mm(t.GetWidth(pcbnew.F_Cu)) / 2
            for k in range(_nl): fdiscs[k].append((vx, vy, vrf))
            fall.append(('disc', vx, vy, vrf))
        else:
            s = (mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y), mm(t.GetWidth()) / 2)
            _li = _lidx.get(t.GetLayer())
            if _li is not None: fsegs[_li].append(s)
    # v1.10.0 (Phase 2, P2.1): exact keepout / board-edge / drill-hole tests. The
    # keepout test below used to sample the segment centreline every
    # 0.1 mm with no half-width, so a leg whose copper overlapped a
    # rule area while its centreline stayed outside passed here and
    # faulted at the gate (items_not_allowed); Edge.Cuts and drilled
    # holes were not tested at all, so copper_edge_clearance /
    # hole_clearance / hole_to_hole faults reached the gate the same
    # way (the graze band the accept key then reverted wholesale).
    edge_segs = []
    try: edge_clr = mm(b.GetDesignSettings().m_CopperEdgeClearance)
    except Exception: edge_clr = 0.5
    if edge_clr > 0:
        def _add_edge(dr):
            if dr.Type() == pcbnew.PCB_SHAPE_T and dr.GetShape() == pcbnew.SHAPE_T_SEGMENT:
                edge_segs.append((mm(dr.GetStart().x), mm(dr.GetStart().y), mm(dr.GetEnd().x), mm(dr.GetEnd().y)))
            else:
                bb = dr.GetBoundingBox()
                el, et, er, eb = mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())
                edge_segs.extend([(el, et, er, et), (er, et, er, eb), (er, eb, el, eb), (el, eb, el, et)])
        for dr in b.GetDrawings():
            if dr.GetLayer() == pcbnew.Edge_Cuts: _add_edge(dr)
        # v1.11.1 (#75): Edge.Cuts drawn inside a footprint (slots,
        # cutouts) is board outline for DRC too -- collect it as well.
        for fp in b.GetFootprints():
            for dr in fp.GraphicalItems():
                if dr.GetLayer() == pcbnew.Edge_Cuts: _add_edge(dr)
    fdrills = []
    adrills = []   # v1.11.1 (#75): EVERY drill, any net -- hole_to_hole
    # is a physical drill-separation rule and binds same-net pairs too;
    # fdrills stays foreign-only for the hole_clearance (copper) test.
    try: hole_clr = mm(b.GetDesignSettings().m_HoleClearance)
    except Exception: hole_clr = 0.25
    try: h2h = mm(b.GetDesignSettings().m_HoleToHoleMin)
    except Exception: h2h = hole_clr
    if hole_clr > 0 or h2h > 0:
        for fp in b.GetFootprints():
            for p in fp.Pads():
                ds = p.GetDrillSize()
                if ds.x > 0:
                    _dr = (mm(p.GetPosition().x), mm(p.GetPosition().y), max(mm(ds.x), mm(ds.y)) / 2)
                    adrills.append(_dr)
                    if p.GetNetname() != net: fdrills.append(_dr)
        for t in b.GetTracks():
            if t.Type() != pcbnew.PCB_VIA_T: continue
            _dr = (mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetDrillValue()) / 2)
            adrills.append(_dr)
            if t.GetNetname() != net: fdrills.append(_dr)
    vdr = net_class_via(b, net)[1] / 2
    def in_poly(px, py, pts):
        inside = False
        for i in range(len(pts)):
            x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % len(pts)]
            if (y1 > py) != (y2 > py):
                if px < x1 + (py - y1) * (x2 - x1) / (y2 - y1): inside = not inside
        return inside
    hw = w / 2
    for (li, x1, y1, x2, y2) in segs:
        for (sx1, sy1, sx2, sy2, hr) in fsegs[li]:
            if seg_seg_dist((x1, y1, x2, y2), (sx1, sy1, sx2, sy2)) < hr + hw + CLR: return False
        for (cx, cy, r) in fdiscs[li]:
            if seg_pt(cx, cy, x1, y1, x2, y2) < r + hw + CLR: return False
        for prm in fpads[li]:
            if _seg_pad_dist(prm, x1, y1, x2, y2) < hw + CLR: return False
        for (pts, klayers, no_tracks, _nv) in keepouts:
            if no_tracks and li in klayers:
                if in_poly(x1, y1, pts) or in_poly(x2, y2, pts): return False
                for i in range(len(pts)):
                    q = pts[(i + 1) % len(pts)]
                    if seg_seg_dist((x1, y1, x2, y2), (pts[i][0], pts[i][1], q[0], q[1])) < hw + 0.02: return False
        for (ex1, ey1, ex2, ey2) in edge_segs:
            if max(ex1, ex2) < min(x1, x2) - hw - edge_clr or min(ex1, ex2) > max(x1, x2) + hw + edge_clr or max(ey1, ey2) < min(y1, y2) - hw - edge_clr or min(ey1, ey2) > max(y1, y2) + hw + edge_clr: continue
            if seg_seg_dist((x1, y1, x2, y2), (ex1, ey1, ex2, ey2)) < hw + edge_clr: return False
        for (hx, hy, hr_) in fdrills:
            if seg_pt(hx, hy, x1, y1, x2, y2) < hr_ + hw + hole_clr: return False
    for (x, y) in vias:
        for li in range(_nl):
            for (sx1, sy1, sx2, sy2, hr) in fsegs[li]:
                if seg_pt(x, y, sx1, sy1, sx2, sy2) < hr + vr + CLR: return False
            for (cx, cy, r) in fdiscs[li]:
                if math.hypot(x - cx, y - cy) < r + vr + CLR: return False
            for prm in fpads[li]:
                if _pad_sdf_dist(prm, x, y) < vr + CLR: return False
        for it in fall:
            if it[0] == 'pad':
                if _pad_sdf_dist(it[1], x, y) < vr + CLR: return False
            elif it[0] == 'disc':
                if math.hypot(x - it[1], y - it[2]) < it[3] + vr + CLR: return False
            else:
                pass  # 'seg' fall entries retired in phase (a): inner tracks live in fsegs buckets now
        for (pts, _kl, _nt, no_vias) in keepouts:
            if no_vias:
                if in_poly(x, y, pts): return False
                d = min(seg_pt(x, y, pts[i][0], pts[i][1], pts[(i + 1) % len(pts)][0], pts[(i + 1) % len(pts)][1]) for i in range(len(pts)))
                if d < vr + 0.02: return False
        for (ex1, ey1, ex2, ey2) in edge_segs:
            if seg_pt(x, y, ex1, ey1, ex2, ey2) < vr + edge_clr: return False
        for (hx, hy, hr_) in fdrills:
            if math.hypot(x - hx, y - hy) < hr_ + vr + hole_clr: return False
        for (hx, hy, hr_) in adrills:
            if math.hypot(x - hx, y - hy) < hr_ + vdr + h2h: return False
    return True

def cmd_heal_attempt(board_path, out_path, net, frm, dst):
    # Single additive bridge between two copper points of a split pour
    # net; runs in its own process (pcbnew degrades under repeated
    # load/mutate cycles in one process -- see cmd_finish_leg).
    fl, fx, fy = frm; dl, dx, dy = dst
    b = pcbnew.LoadBoard(board_path); refill(b)
    conn0 = b.GetConnectivity(); conn0.Build(b)
    n_before = _ncomp(b, conn0, net)
    if n_before <= 1: return {'status': 'already_connected', 'net': net}
    bw = max(HEAL_W, net_class_width(b, net, HEAL_W))
    window = (min(fx, dx) - 6, max(fx, dx) + 6, min(fy, dy) - 6, max(fy, dy) + 6)
    _vd0, _vdr0 = net_class_via(b, net)
    simp = None; grid = None; extra = set()
    for bump in (0.0, 0.05, 0.10):
        # v1.8 (#2b): clearance-bump ladder + edge/hole keepbacks,
        # ported from the finish planner -- heal was the last planner
        # still on one fixed clearance with no keepbacks, so bridges
        # that only exist in the graze band, and plans the gate would
        # reject at an edge or hole, were invisible to it. A bump's
        # plan is accepted only if the exact checker passes it.
        g = Grid(b, window, track_w=bw, via_r=_vd0 / 2)
        if bump:
            g.clr_plan = board_clearance(b) + 0.03 + bump
            g.inf = bw / 2 + g.clr_plan
        blocked, via_block = g.build({net}, target=net)
        # v1.8.1 (#11): keepbacks BEFORE free_disc (the v1.7.1 order) --
        # stamped after, they re-blocked the freed start/goal discs.
        _stamp_edge_keepback(b, g, blocked, via_block, bw, _vd0 / 2)
        _stamp_hole_keepback(b, g, blocked, via_block, bw, _vdr0 / 2, net=net)
        g.free_disc(blocked, via_block, fx, fy, layers=fl)
        g.free_disc(blocked, via_block, dx, dy, layers=dl)
        _stamp_inner_via_blocks(b, via_block, g, {net})
        # v1.3.2: this net's own plane layers are legal travel for
        # its bridge; every other banned layer stays banned.
        extra = {g.lidx[lay] for z in b.Zones() if z.GetNetname() == net for lay in z.GetLayerSet().Seq() if lay in g.lidx and lay not in (pcbnew.F_Cu, pcbnew.B_Cu)}
        s2, _c = g.astar(blocked, via_block, (fx, fy), (dx, dy), fl, dl, extra_layers=extra)
        if s2 is None: continue
        segs2, vias2 = path_segs_vias(s2)
        if not _new_items_clean(b, net, segs2, vias2, g.keepouts, w=bw): continue
        simp, grid = s2, g
        break
    hunt_via = None
    if simp is None:
        # Via hunt: the fragment's pads can sit in a copper pocket on
        # both outer layers (a measured case: fenced in by routed
        # B.Cu tracks) while its plane-layer island extends somewhere
        # open. A fresh via through the island joins the fragment at
        # that point; plan from there instead. The hunt needs its own
        # grid: candidates can lie well outside the pair window, and a
        # clamped cell would silently misplace the via.
        # v1.8.1 (#10): islands attributed by ownership (anchors = the
        # component's via / TH-pad points); hunt grid bounded to the
        # pair bbox + 12 mm (a board-spanning main polygon made the
        # old grid board-wide -- the multi-minute thrash); if the frm
        # side has no island, hunt symmetrically from dst's island.
        _hp, _hc, _hpts = _comp_points(b, conn0, net)
        def _anchors(xy):
            for ci, plist in _hpts.items():
                if any(abs(x - xy[0]) < 0.011 and abs(y - xy[1]) < 0.011 for (x, y, _l) in plist):
                    out = [(x, y) for (x, y, ls) in plist if len(ls) == b.GetCopperLayerCount()]
                    out += [(x, y) for (_n, x, y, _l, p) in _hp if _hc.get(_n) == ci and p.GetDrillSize().x > 0]
                    return out
            return []
        def _hunt(island, target_xy, target_layers):
            wx = (min(fx, dx) - 12, max(fx, dx) + 12, min(fy, dy) - 12, max(fy, dy) + 12)
            isl = []
            for (pts, ps, oi) in island:
                xs_ = [q[0] for q in pts]; ys_ = [q[1] for q in pts]
                if max(xs_) >= wx[0] and min(xs_) <= wx[1] and max(ys_) >= wx[2] and min(ys_) <= wx[3]: isl.append((pts, ps, oi))
            if not isl: return None
            xs = [fx, dx] + [q[0] for pts, _ps, _oi in isl for q in pts]
            ys = [fy, dy] + [q[1] for pts, _ps, _oi in isl for q in pts]
            g2 = Grid(b, (max(min(xs) - 2, wx[0]), min(max(xs) + 2, wx[1]), max(min(ys) - 2, wx[2]), min(max(ys) + 2, wx[3])), track_w=bw, via_r=net_class_via(b, net)[0] / 2)
            bl, vb = g2.build({net}, target=net)
            _stamp_edge_keepback(b, g2, bl, vb, bw, _vd0 / 2)
            _stamp_hole_keepback(b, g2, bl, vb, bw, _vdr0 / 2, net=net)
            g2.free_disc(bl, vb, fx, fy, layers=fl)
            g2.free_disc(bl, vb, dx, dy, layers=dl)
            _stamp_inner_via_blocks(b, vb, g2, {net})
            hv, sp = _via_hunt(b, net, isl, g2, bl, vb, target_xy, target_layers, extra_layers=extra, scan_win=wx)
            if sp is None: return None
            return (hv, sp, g2)
        hunt = _hunt(_island_polys(b, net, (fx, fy), _anchors((fx, fy))), (dx, dy), dl)
        if hunt is None:
            hunt = _hunt(_island_polys(b, net, (dx, dy), _anchors((dx, dy))), (fx, fy), fl)
        if hunt is None: return {'status': 'no_path', 'net': net}
        hunt_via, simp, grid = hunt
    segs, vias = path_segs_vias(simp)
    planned = list(vias) + ([hunt_via] if hunt_via is not None else [])
    for t in b.GetTracks():
        if t.Type() != pcbnew.PCB_VIA_T: continue
        vx, vy = mm(t.GetPosition().x), mm(t.GetPosition().y)
        if any(math.hypot(x - vx, y - vy) < 0.9 for (x, y) in planned):
            return {'status': 'no_path', 'net': net, 'detail': 'via would crowd an existing via'}
    nets = b.GetNetsByName()
    _vd, _vdr = net_class_via(b, net)
    if hunt_via is not None: add_via(b, nets, net, hunt_via[0], hunt_via[1], _vd, _vdr)
    for (li, x1, y1, x2, y2) in segs: add_seg(b, nets, net, li, x1, y1, x2, y2, bw)
    for (x, y) in vias: add_via(b, nets, net, x, y, _vd, _vdr)
    planned = list(vias) + ([hunt_via] if hunt_via is not None else [])
    if not _new_items_clean(b, net, segs, planned, grid.keepouts, w=bw):
        return {'status': 'fault', 'net': net, 'detail': 'new items would violate clearance/keepout'}
    refill(b)
    conn = b.GetConnectivity(); conn.Build(b)
    n_after = _ncomp(b, conn, net)
    if n_after >= n_before:
        return {'status': 'no_merge', 'net': net, 'components': n_before}
    pcbnew.SaveBoard(out_path, b)
    return {'status': 'routed', 'net': net, 'components_before': n_before,
            'components_after': n_after, 'segs': len(segs), 'vias': len(vias)}



def _island_polys(b, net, frm_xy, anchors=None):
    # Filled zone polygons of the net on inner layers that contain (or
    # nearly contain) the fragment point -- the fragment's plane island.
    # Copper layer IDs are NOT 0..count-1 (KiCad 10: F=0, B=2, In1=4,
    # In2=6); enumerate from the net's own zone layer sets instead.
    # v1.8.1 (#10): with anchors given (the fragment component's via /
    # TH-pad points, which pierce every layer), a polygon counts as
    # the fragment's island ONLY if it contains an anchor -- the old
    # 2D-proximity test also matched the MAIN plane polygon whenever
    # the fragment point sat over or within 1 mm of the main fill, and
    # the via hunt then bridged main to main (discarded as no_merge).
    fx, fy = frm_xy
    def in_poly(px, py, pts):
        inside = False
        for i in range(len(pts)):
            x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % len(pts)]
            if (y1 > py) != (y2 > py):
                if px < x1 + (py - y1) * (x2 - x1) / (y2 - y1): inside = not inside
        return inside
    def pt_poly_dist(px, py, pts):
        return min(seg_pt(px, py, pts[i][0], pts[i][1], pts[(i + 1) % len(pts)][0], pts[(i + 1) % len(pts)][1]) for i in range(len(pts)))
    island = []
    for z in b.Zones():
        if z.GetNetname() != net: continue
        for lay in z.GetLayerSet().Seq():
            if lay in (pcbnew.F_Cu, pcbnew.B_Cu): continue
            polys = z.GetFilledPolysList(lay)
            for oi in range(polys.OutlineCount()):
                o = polys.Outline(oi)
                pts = [(mm(o.CPoint(k).x), mm(o.CPoint(k).y)) for k in range(o.PointCount())]
                # v1.3 Y1: containment is hole-aware (SHAPE_POLY_SET
                # .Contains) -- the outer-ring in_poly test counts a
                # point inside a pour cutout as inside the island.
                if anchors is not None:
                    if any(polys.Contains(pcbnew.VECTOR2I(nm(ax), nm(ay)), oi) for (ax, ay) in anchors): island.append((pts, polys, oi))
                elif polys.Contains(pcbnew.VECTOR2I(nm(fx), nm(fy)), oi) or pt_poly_dist(fx, fy, pts) < 1.0: island.append((pts, polys, oi))
    return island

def _via_hunt(b, net, island, grid, blocked, via_block, dst_xy, dl, extra_layers=(), scan_win=None):
    # Find a legal via site inside the given filled zone polygons (one
    # component's island, attributed by the caller) on an inner layer,
    # from which the planner can reach the target point.
    # Returns (via_xy, simp) or (None, None).
    def in_poly(px, py, pts):
        inside = False
        for i in range(len(pts)):
            x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % len(pts)]
            if (y1 > py) != (y2 > py):
                if px < x1 + (py - y1) * (x2 - x1) / (y2 - y1): inside = not inside
        return inside
    if not island: return None, None
    # via legality: clear of foreign pads, vias and tracks (any layer)
    obs_pads, obs_vias, obs_seg = [], [], []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() == net: continue
            obs_pads.append(_pad_sdf_params(p))
    all_vias = []
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            all_vias.append((mm(t.GetPosition().x), mm(t.GetPosition().y)))
            if t.GetNetname() == net: continue
            obs_vias.append((mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetWidth(pcbnew.F_Cu)) / 2))
        elif t.GetNetname() != net:
            obs_seg.append((mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y), mm(t.GetWidth()) / 2))
    no_via_areas = [pts for (pts, _l, _nt, nv) in grid.keepouts if nv]
    # v1.3 Y1: hole-aware inside test for the no-via areas too (their
    # Outline() sets, holes respected by Contains).
    _all_zones = list(b.Zones())
    for fp in b.GetFootprints(): _all_zones.extend(list(fp.Zones()))
    no_via_sets = [z.Outline() for z in _all_zones if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    def pt_poly_dist(px, py, pts):
        return min(seg_pt(px, py, pts[i][0], pts[i][1], pts[(i + 1) % len(pts)][0], pts[(i + 1) % len(pts)][1]) for i in range(len(pts)))
    def via_legal(x, y):
        # DRC tests the via DISC against keepouts, not the centre: a
        # centre 0.2 mm outside J1_CLIP_CLEARANCE still overlapped it
        # by the via radius and drew items_not_allowed.
        for pts in no_via_areas:
            if pt_poly_dist(x, y, pts) < grid.via_r + 0.05: return False
        for ps in no_via_sets:
            if ps.Contains(pcbnew.VECTOR2I(nm(x), nm(y))): return False
        for prm in obs_pads:
            if _pad_sdf_dist(prm, x, y) < grid.via_r + grid.clr_plan: return False
        for (cx, cy, r) in obs_vias:
            if math.hypot(x - cx, y - cy) < r + grid.via_r + grid.clr_plan: return False
        for (x1, y1, x2, y2, hr) in obs_seg:
            if seg_pt(x, y, x1, y1, x2, y2) < hr + grid.via_r + grid.clr_plan: return False
        for (vx, vy) in all_vias:
            if math.hypot(x - vx, y - vy) < 0.9: return False
        return True
    cands = []
    for pts, pset, oi in island:
        xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
        x_lo, x_hi, y_lo, y_hi = min(xs), max(xs), min(ys), max(ys)
        if scan_win is not None:
            # v1.8.1 (#10): never scan a polygon's full bbox -- a plane
            # polygon can span the board; only the hunt window matters.
            x_lo = max(x_lo, scan_win[0]); x_hi = min(x_hi, scan_win[1])
            y_lo = max(y_lo, scan_win[2]); y_hi = min(y_hi, scan_win[3])
        x = x_lo
        while x <= x_hi:
            y = y_lo
            while y <= y_hi:
                # cheap grid prefilter BEFORE the pcbnew Contains call
                # and the pure-Python via_legal scan over board lists.
                _ci, _cj = grid.cell(x, y)
                if (0 <= _cj < grid.NY and 0 <= _ci < grid.NX) and not via_block[_cj, _ci]:
                    if pset.Contains(pcbnew.VECTOR2I(nm(x), nm(y)), oi) and via_legal(x, y):
                        cands.append((math.hypot(x - dst_xy[0], y - dst_xy[1]), x, y))
                y += 0.5
            x += 0.5
    cands.sort()
    # v1.3 Y2: free_disc MUTATES the shared blocked/via_block -- snapshot
    # once and restore before every candidate, so a failed candidate's
    # punched-out disc cannot leak into the next candidate's search.
    _snap_b = blocked.copy(); _snap_v = via_block.copy()
    for _d, x, y in cands[:6]:
        blocked[...] = _snap_b; via_block[...] = _snap_v
        grid.free_disc(blocked, via_block, x, y, layers=tuple(range(grid.nl)))
        simp, _c = grid.astar(blocked, via_block, (x, y), dst_xy, tuple(range(grid.nl)), dl, extra_layers=extra_layers, max_pops=400000)
        if simp is not None: return (x, y), simp
    blocked[...] = _snap_b; via_block[...] = _snap_v
    return None, None

def cmd_heal(board_path, out_path, only=None):
    # Rejoin split pour-net fragments (plane islands) with additive
    # bridges between the nearest copper points of a minority component
    # and the main component. stitch cannot do this: it only heals
    # floating clusters with no vias/TH pads, and finish_leg plans
    # pad-to-pad with displacement -- the 3V3 fragments each carry
    # vias and stub tracks, so both tools left them split (A2: 4 comps).
    import subprocess, tempfile
    probe = pcbnew.LoadBoard(board_path); refill(probe)
    pour = []
    for z in probe.Zones():
        n = z.GetNetname()
        if n and n not in pour: pour.append(n)
    if only: pour = [n for n in pour if n in only]
    healed, failed, bridges = [], [], 0
    board_in = board_path
    for net in pour:
        was_split = False; net_failed = False
        for _ in range(6):
            probe = pcbnew.LoadBoard(board_in); refill(probe)
            conn = probe.GetConnectivity(); conn.Build(probe)
            pads, comp, pts = _comp_points(probe, conn, net)
            counts = {}
            for ci in comp.values(): counts[ci] = counts.get(ci, 0) + 1
            if len(counts) <= 1: break
            was_split = True
            main_ci = max(counts, key=counts.get)
            pairs = []
            for ci, plist in pts.items():
                if ci == main_ci: continue
                for (x, y, layers) in plist:
                    for (x2, y2, layers2) in pts.get(main_ci, []):
                        d = math.hypot(x - x2, y - y2)
                        if d <= HEAL_MAX_MM: pairs.append((d, (layers, x, y), (layers2, x2, y2)))
            pairs.sort(key=lambda t: t[0])
            if not pairs:
                failed.append({'net': net, 'reason': 'too_far'})
                net_failed = True; break
            routed_one = False; last_status = 'failed'
            for _d, (fl, fx, fy), (dl, dx, dy) in pairs[:8]:
                fd, rpath = tempfile.mkstemp(suffix='.json'); os.close(fd)
                cmdline = [sys.executable, os.path.abspath(__file__), 'heal_attempt',
                           '--board', board_in, '--out', out_path, '--net', net,
                           '--frm', '%s:%.2f:%.2f' % (','.join(map(str, fl)), fx, fy),
                           '--dst', '%s:%.2f:%.2f' % (','.join(map(str, dl)), dx, dy),
                           '--result', rpath]
                if NO_TRACKS_ON_RAW is not None: cmdline += ['--no-tracks-on', NO_TRACKS_ON_RAW]  # v1.11.2 (#104): forward an explicit '' too
                if LAYER_COST_RAW: cmdline += ['--layer-cost', LAYER_COST_RAW]
                if SKELETON_ON: cmdline += ['--skeleton']
                try:
                    subprocess.run(cmdline, capture_output=True, text=True, timeout=300)
                    res = json.load(open(rpath))
                except Exception:
                    res = {'status': 'failed'}
                try: os.unlink(rpath)
                except Exception: pass
                last_status = res.get('status', 'failed')
                if last_status == 'routed':
                    board_in = out_path; bridges += 1; routed_one = True; break
            if routed_one: continue
            failed.append({'net': net, 'reason': last_status})
            net_failed = True; break
        if was_split and not net_failed: healed.append(net)
    status = 'healed' if healed and not failed else ('partial' if bridges else 'no_path')
    return {'status': status, 'healed': healed, 'failed': failed, 'bridges': bridges}

# ---------------------------------------------------------------- stitch
def _bridge_island(b, conn, netname, pad):
    # Track-bridge fallback for stitch: when no legal via fits inside a
    # floating pour island (sliver polygons -- the Q1.1 case), run a
    # short track on the pad's own layer from the pad to the nearest
    # other filled polygon of the same net. Verified by connectivity;
    # the added tracks are removed again on failure.
    px, py = mm(pad.GetPosition().x), mm(pad.GetPosition().y)
    layer = pcbnew.F_Cu if pad.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
    # v1.3: grid layer indices are STACK positions (phase (a)) -- a
    # B.Cu pad's index is nl-1, not 1 (1 is In1.Cu on a 4-layer board,
    # whose blocked plane is mostly fill-free: planning there and
    # drawing on B.Cu would cross live copper unchecked).
    li = 0 if layer == pcbnew.F_Cu else None
    def in_poly(x, y, pts):
        inside = False
        for i in range(len(pts)):
            x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % len(pts)]
            if (y1 > y) != (y2 > y):
                if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1): inside = not inside
        return inside
    def pt_poly_dist(x, y, pts):
        return min(seg_pt(x, y, pts[i][0], pts[i][1], pts[(i + 1) % len(pts)][0], pts[(i + 1) % len(pts)][1]) for i in range(len(pts)))
    stack0 = copper_stack(b)
    lidx0 = {lay: k for k, lay in enumerate(stack0)}
    goals = []  # (layer_index, polys, oi, outline_pts)
    for z in b.Zones():
        if z.GetNetname() != netname: continue
        for glay in z.GetLayerSet().Seq():
            if glay not in lidx0: continue
            polys = z.GetFilledPolysList(glay)
            for oi in range(polys.OutlineCount()):
                # v1.3 Y1: hole-aware -- a goal inside a pour cutout
                # has no copper to bridge to. On the pad's own layer,
                # skip the polygon the pad already sits in; on inner
                # layers every filled polygon of the net is a legal
                # 1-via target (v1.8.1 #12 -- the astar was locked to
                # the pad's layer at both ends, so via counts were
                # parity-locked to 0 or 2 and the 1-via drop into the
                # net's own plane was structurally impossible).
                if glay == layer and polys.Contains(pcbnew.VECTOR2I(nm(px), nm(py)), oi): continue
                _o = polys.Outline(oi)
                _pts = [(mm(_o.CPoint(k).x), mm(_o.CPoint(k).y)) for k in range(_o.PointCount())]
                goals.append((lidx0[glay], polys, oi, _pts))
    if not goals: return False
    # v1.8.1 (#12): length pre-filter -- the bridge is rejected past
    # 8 mm anyway, so when every goal polygon is farther than that,
    # skip the grid build entirely (dense boards carry many host-less
    # floating pads; building a grid per hopeless pad dominated
    # cmd_stitch's runtime there).
    def _goal_dist(_l, _ps, _oi, _pts):
        # v1.10.1 (#47): the pre-filter measured distance to the OUTER
        # RING only, so a pad sitting over its own net's inner plane
        # (true distance 0) or inside a pour cutout (~1 mm to the hole
        # edge) was rejected whenever the board-edge outline was more
        # than 8 mm away -- the 1-via plane drop #12 built in v1.8.1
        # never got to run on exactly the pads it was built for.
        try:
            if _ps.Contains(pcbnew.VECTOR2I(nm(px), nm(py)), _oi): return 0.0
        except Exception: pass
        d = pt_poly_dist(px, py, _pts)
        try:
            _o = _ps.Outline(_oi)
            for _h in range(_o.HoleCount()):
                _hc = _o.Hole(_h)
                _hpts = [(mm(_hc.CPoint(k).x), mm(_hc.CPoint(k).y)) for k in range(_hc.PointCount())]
                if len(_hpts) >= 2: d = min(d, pt_poly_dist(px, py, _hpts))
        except Exception: pass
        return d
    if min(_goal_dist(_l, _ps, _oi, _pts) for (_l, _ps, _oi, _pts) in goals) > 8.0: return False
    def padset():
        return set((round(mm(it.GetPosition().x), 2), round(mm(it.GetPosition().y), 2)) for it in conn.GetConnectedItems(pad) if it.Type() == pcbnew.PCB_PAD_T)
    before = padset()
    window = (px - 9, px + 9, py - 9, py + 9)
    # v1.3 Y3: plan at the width the tracks are DRAWN at (the net's
    # class width), not the 0.25 default -- planning narrower than the
    # drawn track put copper 0.025 mm closer to obstacles than checked.
    draw_w = net_class_width(b, netname, 0.30)
    _vd0, _vdr0 = net_class_via(b, netname)
    grid = Grid(b, window, track_w=draw_w, via_r=_vd0 / 2)
    if li is None: li = grid.nl - 1
    blocked, via_block = grid.build({netname}, target=netname)
    _stamp_edge_keepback(b, grid, blocked, via_block, draw_w, _vd0 / 2)
    _stamp_hole_keepback(b, grid, blocked, via_block, draw_w, _vdr0 / 2, net=netname)
    goal = None
    by_layer = {}
    for (gl, ps, oi, _pts) in goals: by_layer.setdefault(gl, []).append((ps, oi))
    for j in range(grid.NY):
        for i in range(grid.NX):
            x, y = float(grid.gx[i]), float(grid.gy[j])
            pt = None
            for gl, polys in by_layer.items():
                if blocked[gl, j, i]: continue
                if pt is None: pt = pcbnew.VECTOR2I(nm(x), nm(y))
                if any(ps.Contains(pt, oi) for (ps, oi) in polys):
                    d = math.hypot(x - px, y - py)
                    if goal is None or d < goal[0]: goal = (d, x, y, gl)
    if goal is None: return False
    _, gx_, gy_, gli = goal
    grid.free_disc(blocked, via_block, px, py, layers=(li,))
    grid.free_disc(blocked, via_block, gx_, gy_, layers=(gli,))
    _stamp_inner_via_blocks(b, via_block, grid, {netname})
    simp, _c = grid.astar(blocked, via_block, (px, py), (gx_, gy_), (li,), (gli,), extra_layers={gli})
    if simp is None: return False
    segs, vias = path_segs_vias(simp)
    length = sum(math.hypot(x2 - x1, y2 - y1) for (_l, x1, y1, x2, y2) in segs)
    if length > 8.0 or len(vias) > 2: return False  # v1.8 (#2): <=2 vias may hop a trace ring
    # v1.3 Y3: exact-check the bridge before committing it -- the old
    # code verified only connectivity after the fact, so a bridge that
    # connected AND clipped foreign copper stayed in the board.
    if not _new_items_clean(b, netname, segs, vias, grid.keepouts, w=draw_w): return False
    nets = b.GetNetsByName()
    added = []
    stack = copper_stack(b)
    for (_l, x1, y1, x2, y2) in segs:
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(nm(x1), nm(y1))); t.SetEnd(pcbnew.VECTOR2I(nm(x2), nm(y2)))
        t.SetWidth(nm(draw_w)); t.SetLayer(stack[_l]); t.SetNetCode(nets[netname].GetNetCode())
        b.Add(t); added.append(t)
    _vd, _vdr = net_class_via(b, netname)
    for (x, y) in vias:
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(pcbnew.VECTOR2I(nm(x), nm(y)))
        v.SetWidth(nm(_vd)); v.SetDrill(nm(_vdr))
        v.SetNetCode(nets[netname].GetNetCode()); b.Add(v); added.append(v)
    refill(b); conn.Build(b)
    if len(padset()) > len(before): return True
    for t in added: b.Remove(t)
    refill(b); conn.Build(b)
    return False


def cmd_lock_window(board_path, out_path, net, margin=6.0, fx=None, fy=None, tx=None, ty=None):
    # v1.9 (Phase 1.5): prepare a board for a windowed engine re-route:
    # lock every track/via OUTSIDE the window so TraceMaker --reroute
    # removes and re-routes only the window's copper. The window is the
    # stuck connection's endpoint bbox when given, else the net's own
    # copper bbox, inflated by margin (mm).
    b = pcbnew.LoadBoard(board_path)
    nobj = b.FindNet(net)
    if nobj is None: return {'status': 'error', 'detail': 'no such net'}
    code = nobj.GetNetCode()
    if fx is not None:
        xs, ys = [fx, tx], [fy, ty]
    else:
        xs, ys = [], []
        for t in list(b.GetTracks()):
            if t.GetNetCode() == code:
                bb = t.GetBoundingBox(); xs += [pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetRight())]; ys += [pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetBottom())]
        for fp in b.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNetCode() == code:
                    p = pad.GetPosition(); xs.append(pcbnew.ToMM(p.x)); ys.append(pcbnew.ToMM(p.y))
    if not xs: return {'status': 'error', 'detail': 'net has no geometry'}
    win = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)
    # v1.10.1 (#50): record every item's original lock state (restored
    # on the re-route output by cmd_restore_locks) and NEVER unlock an
    # item the user locked -- with strip_existing=False the old loop
    # unlocked the user's own locked copper inside the window and
    # --reroute then ripped it.
    states = {}
    for t in list(b.GetTracks()):
        try: states[t.m_Uuid.AsString()] = bool(t.IsLocked())
        except Exception: pass
    locked = unlocked = kept = 0
    for t in list(b.GetTracks()):
        bb = t.GetBoundingBox()
        l, tp, r, bt = pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())
        inside = not (r < win[0] or l > win[2] or bt < win[1] or tp > win[3])
        if inside and t.IsLocked(): kept += 1; continue
        t.SetLocked(not inside)
        if inside: unlocked += 1
        else: locked += 1
    pcbnew.SaveBoard(out_path, b)
    locks_file = out_path + '.locks.json'
    with open(locks_file, 'w') as f: json.dump(states, f)
    return {'status': 'locked', 'window': [round(v, 2) for v in win], 'locked': locked, 'unlocked': unlocked, 'kept_user_locked': kept, 'locks_file': locks_file}

def cmd_restore_locks(board_path, out_path, locks_file):
    # v1.10.1 (#50): reapply the lock states cmd_lock_window recorded,
    # by UUID, to a re-route output -- the delivered board must not
    # arrive with nearly every track locked.
    with open(locks_file) as f: states = json.load(f)
    b = pcbnew.LoadBoard(board_path)
    n = 0
    for t in list(b.GetTracks()):
        try: u = t.m_Uuid.AsString()
        except Exception: continue
        if u in states and bool(t.IsLocked()) != states[u]:
            t.SetLocked(states[u]); n += 1
    pcbnew.SaveBoard(out_path, b)
    return {'status': 'restored', 'changed': n}

def cmd_stitch(board_path, out_path):
    b = pcbnew.LoadBoard(board_path)
    refill(b)
    conn = b.GetConnectivity(); conn.Build(b)
    def cluster_info(pad):
        items = conn.GetConnectedItems(pad)
        vias = sum(1 for it in items if it.Type() == pcbnew.PCB_VIA_T)
        th = sum(1 for it in items if it.Type() == pcbnew.PCB_PAD_T and it.GetDrillSize().x > 0)
        return items, vias, th
    def in_poly(px, py, pts):
        inside = False
        for i in range(len(pts)):
            x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % len(pts)]
            if (y1 > py) != (y2 > py):
                if px < x1 + (py - y1) * (x2 - x1) / (y2 - y1): inside = not inside
        return inside
    pour_nets = []
    for z in b.Zones():
        n = z.GetNetname()
        if n and n not in pour_nets: pour_nets.append(n)
    if 'GND' in pour_nets:
        pour_nets.remove('GND'); pour_nets.insert(0, 'GND')
    all_stitched, all_failed = [], []
    # v1.3.2: clearance from the board (was a hard-coded 0.23), and
    # no-via rule areas (board + footprint zones) collected once --
    # via_legal_c never checked them, so stitch could plant a via in
    # a keepout and only the pipeline gate (which stitch bypassed)
    # would have known.
    CLR_BOARD = board_clearance(b)
    _ko_zones = list(b.Zones())
    for _fp in b.GetFootprints(): _ko_zones.extend(list(_fp.Zones()))
    ko_areas = []
    for _z in _ko_zones:
        if not (_z.GetIsRuleArea() and _z.GetDoNotAllowVias()): continue
        _ps = _z.Outline()
        for _oi in range(_ps.OutlineCount()):
            _o = _ps.Outline(_oi)
            ko_areas.append((_ps, _oi, [(mm(_o.CPoint(k).x), mm(_o.CPoint(k).y)) for k in range(_o.PointCount())]))
    CLR = CLR_BOARD + 0.03
    for netname in pour_nets:
        refill(b); conn.Build(b)
        floating = []
        for fp in b.GetFootprints():
            for p in fp.Pads():
                if p.GetNetname() != netname: continue
                items, vias, th = cluster_info(p)
                if vias == 0 and th == 0: floating.append((fp.GetReference() + '.' + p.GetNumber(), p))
        if not floating: continue
        net_code = floating[0][1].GetNetCode()
        obs_pads, obs_vias, obs_seg = [], [], []
        for fp in b.GetFootprints():
            for p in fp.Pads():
                if p.GetNetname() == netname: continue
                obs_pads.append(_pad_sdf_params(p))
        tracks = b.GetTracks()
        while tracks:
            t = tracks.pop()
            if t.GetNetname() != netname:
                if t.Type() == pcbnew.PCB_VIA_T: obs_vias.append((mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetWidth(pcbnew.F_Cu)) / 2))
                else: obs_seg.append((mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y), mm(t.GetWidth()) / 2))
            del t
        # v1.3 Y4: stitch places the NET'S class via, not a fixed
        # 0.60/0.30 -- legality tests and the obstacle record use its
        # real radius.
        sv_d, sv_drill = net_class_via(b, netname); sv_r = sv_d / 2
        def via_legal_c(x, y, clr, lp=None, lv=None, ls=None):
            for prm in (obs_pads if lp is None else lp):
                if _pad_sdf_dist(prm, x, y) < sv_r + clr: return False
            for (cx, cy, r) in (obs_vias if lv is None else lv):
                if math.hypot(x - cx, y - cy) < r + sv_r + clr: return False
            for (x1, y1, x2, y2, hr) in (obs_seg if ls is None else ls):
                if seg_pt(x, y, x1, y1, x2, y2) < hr + sv_r + clr: return False
            for (_ps, _oi, _pts) in ko_areas:
                if _ps.Contains(pcbnew.VECTOR2I(nm(x), nm(y)), _oi): return False
                if min(seg_pt(x, y, _pts[i][0], _pts[i][1], _pts[(i + 1) % len(_pts)][0], _pts[(i + 1) % len(_pts)][1]) for i in range(len(_pts))) < sv_r + CLR_BOARD: return False
            return True
        # v1.2.5: containment uses the filled SHAPE_POLY_SETs directly --
        # the old ring extraction (Outline only) ignored interior holes,
        # so a pad inside a pour hole matched the board-wide outline and
        # the candidate scan (and its refill loop) ran board-wide.
        host_sets, inner_sets = [], []
        for z in b.Zones():
            if z.GetNetname() != netname: continue
            for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
                if z.IsOnLayer(layer):
                    _ps = z.GetFilledPolysList(layer)
                    host_sets += [(_ps, _oi) for _oi in range(_ps.OutlineCount())]
            for layer in copper_stack(b)[1:-1]:
                if z.IsOnLayer(layer):
                    _ps = z.GetFilledPolysList(layer)
                    inner_sets += [(_ps, _oi) for _oi in range(_ps.OutlineCount())]
        stitched, failed, done = [], [], []
        for name, pad in floating:
            if any(pad is c for c in done): continue
            items, vias, th = cluster_info(pad)
            if vias or th: continue
            cluster_pads = [it for it in items if it.Type() == pcbnew.PCB_PAD_T]
            px, py = mm(pad.GetPosition().x), mm(pad.GetPosition().y)
            hosts = [(ps, oi) for (ps, oi) in host_sets if ps.Contains(pad.GetPosition(), oi)]
            if not hosts:
                # v1.8.1 (#12): no outer-layer host polygon -- try the
                # track bridge anyway (outer island or 1-via plane drop).
                if _bridge_island(b, conn, netname, pad):
                    stitched.append({'pad': name, 'net': netname, 'bridge': True})
                else:
                    failed.append(name)
                done.extend(cluster_pads)
                continue
            # v1.2.5: scan only a +-6 mm window around the cluster's pads
            # (was: the whole host outline's bbox) and bucket dmin to
            # 0.1 mm so the distance tiebreak actually engages -- float
            # dmin never tied, so far-away candidates were tried first
            # and each cost a refill+rebuild.
            cxs = [mm(cp.GetPosition().x) for cp in cluster_pads]; cys = [mm(cp.GetPosition().y) for cp in cluster_pads]
            wx0, wx1, wy0, wy1 = min(cxs) - 6, max(cxs) + 6, min(cys) - 6, max(cys) + 6
            def attempt(clr, pitch, limit):
                # v1.3.1 P2: slice the obstacle lists to this cluster's
                # window once -- via_legal_c and the dmin pass used to
                # test every candidate point against board-wide lists.
                lp = [prm for prm in obs_pads if wx0 - 3 <= prm[0] <= wx1 + 3 and wy0 - 3 <= prm[1] <= wy1 + 3]
                lv = [(cx, cy, r) for (cx, cy, r) in obs_vias if wx0 - 3 <= cx <= wx1 + 3 and wy0 - 3 <= cy <= wy1 + 3]
                ls = [s for s in obs_seg if not (max(s[0], s[2]) < wx0 - 3 or min(s[0], s[2]) > wx1 + 3 or max(s[1], s[3]) < wy0 - 3 or min(s[1], s[3]) > wy1 + 3)]
                cands = []
                yy = wy0
                while yy <= wy1:
                    xx = wx0
                    while xx <= wx1:
                        pt = pcbnew.VECTOR2I(nm(xx), nm(yy))
                        if any(ps.Contains(pt, oi) for (ps, oi) in hosts) and via_legal_c(xx, yy, clr, lp, lv, ls) and (not inner_sets or any(ps.Contains(pt, oi) for (ps, oi) in inner_sets)):
                            dmin = min([_pad_sdf_dist(prm, xx, yy) for prm in lp] + [math.hypot(xx - cx, yy - cy) - r for (cx, cy, r) in lv] + [seg_pt(xx, yy, s[0], s[1], s[2], s[3]) - s[4] for s in ls] or [9])
                            cands.append((round(dmin, 1), -math.hypot(xx - px, yy - py), xx, yy))
                        xx += pitch
                    yy += pitch
                cands.sort(reverse=True)
                for (_, _, vx, vy) in cands[:limit]:
                    v = pcbnew.PCB_VIA(b)
                    v.SetPosition(pcbnew.VECTOR2I(nm(vx), nm(vy)))
                    v.SetWidth(nm(sv_d)); v.SetDrill(nm(sv_drill)); v.SetNetCode(net_code)
                    b.Add(v); refill(b); conn.Build(b)
                    _i, v2, t2 = cluster_info(pad)
                    if v2 or t2:
                        stitched.append({'pad': name, 'net': netname, 'via': [round(vx, 1), round(vy, 1)]})
                        obs_vias.append((vx, vy, sv_r)); return True
                    b.Remove(v); refill(b); conn.Build(b)
                return False
            if not attempt(CLR, 0.2, 30) and not attempt(board_clearance(b) + 0.005, 0.1, 60):
                if _bridge_island(b, conn, netname, pad):
                    stitched.append({'pad': name, 'net': netname, 'bridge': True})
                else:
                    failed.append(name)
            done.extend(cluster_pads)
        all_stitched += stitched; all_failed += failed
    refill(b)
    pcbnew.SaveBoard(out_path, b)
    return {'stitched': all_stitched, 'failed': all_failed, 'pour_nets': pour_nets}

def cmd_prune(board_path, out_path):
    # Remove dead copper islands: track/via components that touch no
    # pad and no zone. Routers strand these (a via + a stub whose plane
    # connection was cut); they count as unconnected items forever and
    # no pad-cluster tool can see them. Self-verified: if any net's pad
    # component count rises, the board is not saved.
    b = pcbnew.LoadBoard(board_path); refill(b)
    conn = b.GetConnectivity(); conn.Build(b)
    def pad_comps():
        nets = set()
        for fp in b.GetFootprints():
            for p in fp.Pads(): nets.add(p.GetNetname())
        return {n: _ncomp(b, conn, n) for n in nets if n}
    before = pad_comps()
    dead = []
    for t in list(b.GetTracks()):
        items = conn.GetConnectedItems(t)
        if not any(it.Type() == pcbnew.PCB_PAD_T or it.Type() == pcbnew.PCB_ZONE_T for it in items):
            dead.append(t)
    if not dead:
        return {'pruned': 0}
    for t in dead: b.Remove(t)
    refill(b); conn.Build(b)
    after = pad_comps()
    if any(after.get(n, 0) > c for n, c in before.items()):
        return {'pruned': 0, 'status': 'rolled_back'}
    pcbnew.SaveBoard(out_path, b)
    return {'pruned': len(dead)}

# ---------------------------------------------------------------- strip / main
def cmd_refill(board_path, out_path):
    b = pcbnew.LoadBoard(board_path)
    refill(b)
    pcbnew.SaveBoard(out_path, b)
    return {'refilled': True}

def cmd_strip(board_path, out_path):
    b = pcbnew.LoadBoard(board_path)
    tracks = b.GetTracks()  # snapshot ONCE: re-querying Tracks() after a Remove breaks in KiCad 10 pcbnew
    n = len(tracks)
    while tracks:
        t = tracks.pop()
        b.Remove(t)
        del t
    # NO refill here: the stripped board is an internal router input, and refilling it
    # before TraceMaker measurably fragments the pours (measured A/B 2026-11-21: pass 1
    # gate 22 unconnected / DIO1 split with refill vs 5 / 0 splits without). Refill
    # belongs at the gate (kicad-cli --refill-zones) and at finalize (user-facing save).
    pcbnew.SaveBoard(out_path, b)
    return {'removed_tracks_vias': n}

# --------------------------------------------------------------------
# v1.5 phase (c) Stage B: joint candidate selection (0-1 ILP) across
# co-windowed split nets. Stage B-0 (long_finish) takes nets with a
# hard path; finish_leg takes nets whose victims re-bridge locally.
# What neither does is choose JOINTLY: two neighbouring split nets
# whose best legs rip the same victim, or cross each other's only
# corridor, are decided greedily in sequence today. Here each net
# contributes its _plan_attempts victim sets as priced candidates
# (astar cost + the phi victim penalties), an exact geometric
# conflict graph is built (shared victims, or paths closer than the
# DRC rule on a shared layer), and a 0-1 ILP picks at most one
# candidate per net with no conflicting pair, minimising total cost
# plus a large per-unrouted-net penalty. Execution reuses the
# finish_attempt child process per selected leg, cheapest first, on
# the evolving board; a second round re-plans whatever is still
# split (Stage C lite). Solver: scipy/HiGHS when importable, else a
# greedy pass over the same conflict graph (result records which).

def _phi_maps(b0):
    width_of, pads_of = {}, {}
    for t in b0.GetTracks():
        if t.Type() != pcbnew.PCB_VIA_T:
            width_of[t.GetNetname()] = max(width_of.get(t.GetNetname(), 0), mm(t.GetWidth()))
    for fp in b0.GetFootprints():
        for p in fp.Pads():
            pads_of[p.GetNetname()] = pads_of.get(p.GetNetname(), 0) + 1
    return width_of, pads_of


def _phi_of(v, width_of, pads_of):
    sc = 100.0 + 10.0 * pads_of.get(v, 0)
    if width_of.get(v, 0) >= 0.4: sc += 1000.0
    if v.startswith('+') or v in ('V_BAT+', '3V3') or v.endswith('+'): sc += 1000.0
    return sc


def _ilp_candidates(board_path, net):
    plan = _plan_attempts(board_path, net)
    if plan is None: return []
    _A, _Bp, attempts = plan
    b0 = pcbnew.LoadBoard(board_path); refill(b0)
    width_of, pads_of = _phi_maps(b0)
    tw = net_class_width(b0, net, W_TRACK); vr = net_class_via(b0, net)[0] / 2
    cands, seen = [], set()
    for victims in attempts:
        victims = tuple(victims)
        if victims in seen: continue
        seen.add(victims)
        early, _A2, _B2, simp, _window, cost = _plan_path(board_path, net, list(victims))
        if early is not None or simp is None: continue
        segs, vias = path_segs_vias(simp)
        price = float(cost or 0.0) + sum(_phi_of(v, width_of, pads_of) for v in victims)
        cands.append({'net': net, 'victims': victims, 'segs': segs, 'vias': vias,
                      'cost': price, 'tw': tw, 'vr': vr})
    return cands


def _cands_conflict(c1, c2, clr):
    if set(c1['victims']) & set(c2['victims']): return True
    rr = c1['tw'] / 2 + c2['tw'] / 2 + clr
    for (l1, x1, y1, x2, y2) in c1['segs']:
        for (l2, x3, y3, x4, y4) in c2['segs']:
            if l1 == l2 and seg_seg_dist((x1, y1, x2, y2), (x3, y3, x4, y4)) < rr: return True
    rv1 = c1['vr'] + c2['tw'] / 2 + clr
    for (vx, vy) in c1['vias']:
        for (_l2, x3, y3, x4, y4) in c2['segs']:
            if seg_pt(vx, vy, x3, y3, x4, y4) < rv1: return True
    rv2 = c2['vr'] + c1['tw'] / 2 + clr
    for (vx, vy) in c2['vias']:
        for (_l1, x1, y1, x2, y2) in c1['segs']:
            if seg_pt(vx, vy, x1, y1, x2, y2) < rv2: return True
    rvv = c1['vr'] + c2['vr'] + clr
    for (ax, ay) in c1['vias']:
        for (bx, by) in c2['vias']:
            if math.hypot(ax - bx, ay - by) < rvv: return True
    return False


def _ilp_solve(cands, conflicts):
    nets = sorted({c['net'] for c in cands})
    try:
        import numpy as np
        from scipy.optimize import milp, LinearConstraint, Bounds
        n, m = len(cands), len(nets)
        M = 100000.0
        cvec = np.array([c['cost'] for c in cands] + [M] * m)
        rows, lb, ub = [], [], []
        for ni, net in enumerate(nets):
            row = np.zeros(n + m)
            for ci, c in enumerate(cands):
                if c['net'] == net: row[ci] = 1.0
            row[n + ni] = 1.0
            rows.append(row); lb.append(1.0); ub.append(1.0)
        for i in range(n):
            for j in conflicts[i]:
                if j > i:
                    row = np.zeros(n + m); row[i] = 1.0; row[j] = 1.0
                    rows.append(row); lb.append(-np.inf); ub.append(1.0)
        res = milp(cvec, integrality=np.ones(n + m), bounds=Bounds(0.0, 1.0),
                   constraints=LinearConstraint(np.array(rows), np.array(lb), np.array(ub)))
        if res.success and res.x is not None:
            return [i for i in range(n) if res.x[i] > 0.5], 'highs'
    except Exception:
        pass
    chosen, used, blocked_idx = [], set(), set()
    for i in sorted(range(len(cands)), key=lambda k: cands[k]['cost']):
        if cands[i]['net'] in used or i in blocked_idx: continue
        chosen.append(i); used.add(cands[i]['net']); blocked_idx |= conflicts[i]
    return chosen, 'greedy'


def cmd_ilp_repair(board_path, out_path, nets):
    import subprocess, tempfile
    cur = board_path
    results = {}
    solver = None
    rounds = 0
    for round_ in range(2):
        rounds = round_ + 1
        all_cands = []
        cand_counts = {}
        for net in nets:
            if results.get(net) == 'routed': continue
            cs = _ilp_candidates(cur, net)
            cand_counts[net] = len(cs)
            if not cs and net not in results: results[net] = 'no_candidate'
            all_cands += cs
        if not all_cands: break
        b0 = pcbnew.LoadBoard(cur)
        clr = board_clearance(b0); del b0
        conflicts = {i: set() for i in range(len(all_cands))}
        for i in range(len(all_cands)):
            for j in range(i + 1, len(all_cands)):
                if all_cands[i]['net'] != all_cands[j]['net'] and _cands_conflict(all_cands[i], all_cands[j], clr):
                    conflicts[i].add(j); conflicts[j].add(i)
        sel, solver = _ilp_solve(all_cands, conflicts)
        sel_nets = {all_cands[i]['net'] for i in sel}
        for net in cand_counts:
            if cand_counts[net] and net not in sel_nets and results.get(net) != 'routed':
                results[net] = 'not_selected'
        if not sel: break
        progressed = False
        for i in sorted(sel, key=lambda k: all_cands[k]['cost']):
            cand = all_cands[i]
            fd, rpath = tempfile.mkstemp(suffix='.json'); os.close(fd)
            # v1.8.2 (#14): unique leg output -- reusing one path made
            # each leg read AND write the same file, so a failed save
            # corrupted the accumulated state.
            tmp_out = '%s.leg-%d-%d.kicad_pcb' % (out_path, rounds, i)
            cmdline = [sys.executable, os.path.abspath(__file__), 'finish_attempt',
                       '--board', cur, '--out', tmp_out, '--net', cand['net'],
                       '--victims', ','.join(cand['victims']), '--result', rpath]
            if NO_TRACKS_ON_RAW is not None: cmdline += ['--no-tracks-on', NO_TRACKS_ON_RAW]  # v1.11.2 (#104): forward an explicit '' too
            if LAYER_COST_RAW: cmdline += ['--layer-cost', LAYER_COST_RAW]
            if SKELETON_ON: cmdline += ['--skeleton']
            try:
                subprocess.run(cmdline, capture_output=True, text=True, timeout=600)
                res = json.load(open(rpath))
            except Exception:
                res = {'status': 'failed'}
            try: os.unlink(rpath)
            except Exception: pass
            results[cand['net']] = res.get('status')
            if res.get('status') == 'routed':
                cur = tmp_out; progressed = True
        if not progressed: break
    if cur != board_path:
        import shutil; shutil.copy(cur, out_path)
    return {'status': 'done', 'solver': solver, 'rounds': rounds,
            'routed': [n for n, s in results.items() if s == 'routed'], 'nets': results}



def _freerouting_bin():
    for cand in (os.environ.get('FREEROUTING_BIN'), '/opt/freerouting/bin/freerouting'):
        if cand and os.path.exists(cand):
            return cand
    return None


def _run_group(cmd, timeout, cwd=None):
    # v1.10.1 (#55): engines run in their own process group so a
    # timeout kills the whole tree (the Freerouting JVM, KRT's native
    # children) instead of orphaning them in the container.
    import signal as _signal
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try: os.killpg(os.getpgid(p.pid), _signal.SIGKILL)
        except Exception: pass
        out, err = p.communicate()
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)

def cmd_freeroute(board_path, out_path, passes=50):
    # v1.9.5 (Phase 1.2): Freerouting 2.5.0 as a portfolio engine --
    # Specctra DSN out, the JVM distribution routes (the native CLI
    # build crashes on KiCad-exported DSNs: GraalVM serialization
    # config gap, verified 2026-11-23), SES back in. This routes; it
    # does not judge -- the gate census stays the layer-law backstop.
    import shutil, subprocess, tempfile, time
    launcher = _freerouting_bin()
    if not launcher:
        return {'status': 'unavailable', 'detail': 'freerouting launcher not found'}
    t0 = time.time()
    d = tempfile.mkdtemp(prefix='fr-')
    dsn = os.path.join(d, 'board.dsn'); ses = os.path.join(d, 'board.ses')
    b = pcbnew.LoadBoard(board_path)
    if not pcbnew.ExportSpecctraDSN(b, dsn):
        shutil.rmtree(d, ignore_errors=True)
        return {'status': 'dsn_failed'}
    try:
        p = _run_group([launcher, '-de', dsn, '-do', ses, '-mp', str(int(passes)), '-mt', '2'], 2400)
        tail = ((p.stdout or '')[-300:] + (p.stderr or '')[-200:])
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        return {'status': 'run_failed', 'detail': str(e)[:200]}
    if not os.path.exists(ses):
        shutil.rmtree(d, ignore_errors=True)
        return {'status': 'no_ses', 'detail': tail[-200:]}
    b2 = pcbnew.LoadBoard(board_path)
    if not pcbnew.ImportSpecctraSES(b2, ses):
        shutil.rmtree(d, ignore_errors=True)
        return {'status': 'ses_failed'}
    refill(b2)
    pcbnew.SaveBoard(out_path, b2)
    shutil.rmtree(d, ignore_errors=True)
    return {'status': 'routed', 'seconds': round(time.time() - t0, 1)}


def cmd_krt_route(board_path, out_path):
    # v1.9.5 (Phase 1.2): KiCadRoutingTools as a portfolio engine --
    # autoroute.py orchestrates the grid_router binary plus its own
    # step chain and writes a routed board. Needs the KRT tree
    # (AI_ROUTER_KRT env or /opt/krt) and shapely in this python.
    import shutil, subprocess, tempfile, time
    krt = os.environ.get('AI_ROUTER_KRT') or '/opt/krt'
    auto = os.path.join(krt, 'py_router', 'autoroute.py')
    if not os.path.exists(auto):
        return {'status': 'unavailable', 'detail': 'krt not found at %s' % krt}
    t0 = time.time()
    d = tempfile.mkdtemp(prefix='krt-')
    inp = os.path.join(d, 'in.kicad_pcb')
    shutil.copy(board_path, inp)
    pro = os.path.splitext(board_path)[0] + '.kicad_pro'
    if os.path.exists(pro):
        shutil.copy(pro, os.path.join(d, 'in.kicad_pro'))
    dru = os.path.splitext(board_path)[0] + '.kicad_dru'
    if os.path.exists(dru):
        shutil.copy(dru, os.path.join(d, 'in.kicad_dru'))  # v1.9.6 (#40): cmd_freeroute parity
    try:
        p = _run_group([sys.executable, auto, inp, out_path], 3600, cwd=d)
        tail = ((p.stdout or '')[-300:] + (p.stderr or '')[-200:])
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        return {'status': 'run_failed', 'detail': str(e)[:200]}
    if not os.path.exists(out_path):
        shutil.rmtree(d, ignore_errors=True)
        return {'status': 'no_output', 'detail': tail[-200:]}
    refill_note = 'skipped'
    try:
        b2 = pcbnew.LoadBoard(out_path); refill(b2); pcbnew.SaveBoard(out_path, b2); refill_note = 'ok'
    except Exception as e:
        refill_note = 'failed: %s' % str(e)[:80]
    shutil.rmtree(d, ignore_errors=True)
    return {'status': 'routed', 'seconds': round(time.time() - t0, 1), 'refill': refill_note}


def _with_yield(fn, is_fail):
    # #63 yield: the reservation is a planning preference, not a
    # veto -- the ticket's own rule is that it 'yields first under
    # congestion, so it never costs a connection'. A finisher
    # command that fails outright with the reservation on runs once
    # more without it (the measured esp32 case: with corridors hard,
    # finish/heal found no path at all for nets the unreserved
    # finisher connects). The pipeline gate still counts every
    # intrusion the yielded leg leaves behind, so the trade is
    # priced, never free; 'skeleton_yield' in the result records
    # that the reservation was stood down for this command.
    global SKELETON_ON
    res = fn()
    if SKELETON_ON and is_fail(res):
        SKELETON_ON = False
        try:
            res2 = fn()
        finally:
            SKELETON_ON = True
        if not is_fail(res2):
            res2 = dict(res2); res2['skeleton_yield'] = True
            return res2
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd')
    ap.add_argument('--board', required=True)
    ap.add_argument('--out')
    ap.add_argument('--net')
    ap.add_argument('--nets')
    ap.add_argument('--victims')
    ap.add_argument('--attempt-select', action='store_true')
    ap.add_argument('--fast', action='store_true')
    ap.add_argument('--margin', type=float, default=6.0)
    ap.add_argument('--locks')
    ap.add_argument('--fx', type=float); ap.add_argument('--fy', type=float)
    ap.add_argument('--tx', type=float); ap.add_argument('--ty', type=float)
    ap.add_argument('--frm')
    ap.add_argument('--dst')
    ap.add_argument('--result')
    ap.add_argument('--no-tracks-on')
    ap.add_argument('--layer-cost')
    ap.add_argument('--skeleton', action='store_true')  # v1.12 (#63)
    ap.add_argument('--faults')  # v1.12 (#155): JSON fault list for fix_violations
    ap.add_argument('--passes', type=int, default=50)
    a = ap.parse_args()
    if a.no_tracks_on is not None:  # v1.11.2 (#104): '' is an explicit empty ban set, not 'unset' -- the truthiness test silently restored the Grid default {In1_Cu} ban
        global BANNED_LAYER_IDS, NO_TRACKS_ON_RAW, NO_TRACKS_ON_UNRESOLVED
        NO_TRACKS_ON_RAW = a.no_tracks_on
        BANNED_LAYER_IDS, NO_TRACKS_ON_UNRESOLVED = _parse_no_tracks_raw(a.no_tracks_on)
    if a.layer_cost:
        global LAYER_COST_RAW, LAYER_COST_BY_ID, LAYER_COST_UNRESOLVED
        LAYER_COST_RAW = a.layer_cost
        LAYER_COST_BY_ID, LAYER_COST_UNRESOLVED = _parse_layer_cost_raw(a.layer_cost)
    if a.skeleton:
        global SKELETON_ON
        SKELETON_ON = True
    if a.cmd == 'summary': res = cmd_summary(a.board, fast=getattr(a, 'fast', False))
    elif a.cmd == 'scorecard': res = cmd_scorecard(a.board)
    elif a.cmd == 'skeleton': res = cmd_skeleton(a.board)
    elif a.cmd == 'fix_violations': res = _with_yield(lambda: cmd_fix_violations(a.board, a.out, json.load(open(a.faults))), lambda r: not r.get('fixed'))
    elif a.cmd == 'strip': res = cmd_strip(a.board, a.out)
    elif a.cmd == 'finish_leg': res = _with_yield(lambda: cmd_finish_leg(a.board, a.out, a.net, attempt_select=bool(getattr(a, 'attempt_select', False))), lambda r: r.get('status') not in ('routed', 'already_connected'))
    elif a.cmd == 'finish_probe': res = cmd_finish_probe(a.board, a.net, tuple(v for v in (a.victims or '').split(',') if v))
    elif a.cmd == 'finish_attempt': res = cmd_finish_attempt(a.board, a.out, a.net, [v for v in (a.victims or '').split(',') if v])
    elif a.cmd == 'long_leg': res = _with_yield(lambda: cmd_long_leg(a.board, a.out, a.net), lambda r: r.get('status') not in ('routed', 'already_connected'))
    elif a.cmd == 'long_attempt': res = cmd_long_attempt(a.board, a.out, a.net)
    elif a.cmd == 'ilp_repair': res = _with_yield(lambda: cmd_ilp_repair(a.board, a.out, [n for n in (a.nets or '').split(',') if n]), lambda r: not r.get('routed'))
    elif a.cmd == 'stitch': res = _with_yield(lambda: cmd_stitch(a.board, a.out), lambda r: not r.get('stitched') and bool(r.get('failed')))
    elif a.cmd == 'heal': res = _with_yield(lambda: cmd_heal(a.board, a.out, set(a.nets.split(',')) if getattr(a, 'nets', None) else None), lambda r: not r.get('bridges') and bool(r.get('failed')))
    elif a.cmd == 'heal_attempt':
        def _pt(s):
            ls, x, y = s.split(':'); return (tuple(int(v) for v in ls.split(',')), float(x), float(y))
        res = cmd_heal_attempt(a.board, a.out, a.net, _pt(a.frm), _pt(a.dst))
    elif a.cmd == 'lock_window': res = cmd_lock_window(a.board, a.out, a.net, margin=a.margin, fx=a.fx, fy=a.fy, tx=a.tx, ty=a.ty)
    elif a.cmd == 'restore_locks': res = cmd_restore_locks(a.board, a.out, a.locks)
    elif a.cmd == 'freeroute': res = cmd_freeroute(a.board, a.out, passes=a.passes)
    elif a.cmd == 'krt_route': res = cmd_krt_route(a.board, a.out)
    elif a.cmd == 'prune': res = cmd_prune(a.board, a.out)
    elif a.cmd == 'refill': res = cmd_refill(a.board, a.out or a.board)
    else: res = {'status': 'error', 'detail': 'unknown cmd'}
    if a.result:
        with open(a.result, 'w') as f: json.dump(res, f)
    out(res)

if __name__ == '__main__':
    main()
