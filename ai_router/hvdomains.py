"""High-voltage domain spacing: IPC-2221B Table 6-1 as code plus
clearance / creepage measurement for issue #70.

The table values are the numeric values of IPC-2221B Table 6-1
as published by Sierra Circuits, San Francisco Circuits and EMA
Design Automation (cross-verified identical 2026-11-24; research
annex on issue #70).  IPC-2221 Table 6-1 is a single conductor-
spacing table: it does not split creepage from clearance, so the
required value is applied to BOTH measured quantities and the
report says so.  IEC 62368-1's separate creepage tables are not
implemented (recorded gap on the issue).

Measurement model (draft):
  clearance  -- minimum straight-line distance between the two
                domains' copper on a common layer (exact segment
                geometry); cross-layer pairs are reported via
                the stackup z distance when their XY projections
                overlap, else unmodelled.
  creepage   -- shortest surface path on the layer, computed by
                Dijkstra on a grid: copper of any net, board
                exterior and slot/cutout interiors are not
                surface.  A slot between the conductors therefore
                lengthens the measured path.  Under-component
                paths are unmodelled (no body data).
Above-500 V reading (#128 (B2)): the primary values use the
standard's own para. 6.3 form -- the 500 V band value plus the
excess voltage times the column rate (verified against the
IPC-A-610 appendix worked example).  Some published references
apply the rate to the WHOLE voltage instead, which reads larger;
both readings are computed (required_spacing_alt_mm) and the
report shows both above 500 V.  The table is IPC-2221B's;
IPC-2221C (Dec 2023) supersedes B and these values have not been
re-verified against C.

This is a design aid, not a certification.
"""
import heapq
import math, time

# (band top V, B1, B2, B3, B4, A5, A6, A7) -- IPC-2221B Table 6-1,
# mm.  The A columns (assembly: A5 conformal-coated conductors,
# A6 uncoated leads/terminations, A7 conformal-coated
# leads/terminations) were added for #127 (B1): three independent
# reproductions of the table (Sierra Circuits, Altair PollEx docs,
# protoflow) agree cell-for-cell on all seven columns, and the
# ANSI IPC-2221B preview names columns A5-A7 in sec. 6.3.5-6.3.7.
_BANDS = [
    (15,  0.05, 0.10, 0.10, 0.05, 0.13, 0.13, 0.13),
    (30,  0.05, 0.10, 0.10, 0.05, 0.13, 0.25, 0.13),
    (50,  0.10, 0.60, 0.60, 0.13, 0.13, 0.40, 0.13),
    (100, 0.10, 0.60, 1.50, 0.13, 0.13, 0.50, 0.13),
    (150, 0.20, 0.60, 3.20, 0.40, 0.40, 0.80, 0.40),
    (170, 0.20, 1.25, 3.20, 0.40, 0.40, 0.80, 0.40),
    (250, 0.20, 1.25, 6.40, 0.40, 0.40, 0.80, 0.40),
    (300, 0.20, 1.25, 12.5, 0.40, 0.40, 0.80, 0.80),
    (500, 0.25, 2.50, 12.5, 0.80, 0.80, 1.50, 0.80),
]
_RATES = {'B1': 0.0025, 'B2': 0.005, 'B3': 0.025, 'B4': 0.00305,
          'A5': 0.00305, 'A6': 0.00305, 'A7': 0.00305}
_COLIDX = {'B1': 1, 'B2': 2, 'B3': 3, 'B4': 4, 'A5': 5, 'A6': 6, 'A7': 7}


def required_spacing_mm(delta_v, column):
    """IPC-2221B Table 6-1 minimum spacing for a peak voltage
    difference; above 500 V the band value grows by the column's
    per-volt rate (the standard's own para. 6.3 form, verified
    against the IPC-A-610 appendix worked example: 600 V B1 ->
    0.25 + 100*0.0025 = 0.50 mm)."""
    if column not in _COLIDX:
        raise ValueError('unknown column %r' % (column,))
    dv = abs(float(delta_v))
    for band in _BANDS:
        if dv <= band[0]:
            return band[_COLIDX[column]]
    return _BANDS[-1][_COLIDX[column]] + (dv - 500.0) * _RATES[column]


def required_spacing_alt_mm(delta_v, column):
    """The alternative above-500 V reading (#128 (B2)): the
    column rate applied to the WHOLE voltage.  Identical to
    required_spacing_mm at and below 500 V; larger above it
    (600 V B1: 1.50 mm vs the primary 0.50 mm).  Reported
    alongside the primary reading, never used for the verdict."""
    if column not in _COLIDX:
        raise ValueError('unknown column %r' % (column,))
    dv = abs(float(delta_v))
    if dv <= 500.0:
        return required_spacing_mm(dv, column)
    return dv * _RATES[column]


def column_for(surface, coating='uncoated', altitude_m=0):
    """Table column for a layer's CONDUCTORS: internal -> B1;
    external with a declared permanent polymer coating -> B4;
    external with conformal coating over the assembly -> A5;
    external uncoated above 3050 m -> B3; else B2."""
    if not surface:
        return 'B1'
    if coating == 'polymer':
        return 'B4'
    if coating == 'conformal':
        return 'A5'
    if altitude_m and altitude_m > 3050:
        return 'B3'
    return 'B2'


def termination_column(coating='uncoated'):
    """Table column for a component lead / termination (a pad):
    A7 under conformal coating, else A6.  Pads are openings in
    solder mask, so a polymer-coated board's pads are NOT B4."""
    return 'A7' if coating == 'conformal' else 'A6'


def _pair_required(delta_v, surface, coating, altitude_m,
                   termination, spacing_fn):
    col = column_for(surface, coating, altitude_m)
    req = spacing_fn(delta_v, col)
    eff = col
    if termination and surface:
        tcol = termination_column(coating)
        treq = spacing_fn(delta_v, tcol)
        if treq > req:
            req, eff = treq, tcol
    return req, eff, col


def pair_required_mm(delta_v, surface, coating='uncoated',
                     altitude_m=0, termination=False):
    """(required_mm, effective_column, conductor_column) for one
    domain on one layer of a pair.  #127 (B1): when the pair's
    closest approach involves a termination on a surface layer,
    the termination column applies as well and the requirement is
    the larger of the conductor and termination values."""
    return _pair_required(delta_v, surface, coating, altitude_m,
                          termination, required_spacing_mm)


def pair_required_alt_mm(delta_v, surface, coating='uncoated',
                         altitude_m=0, termination=False):
    """pair_required_mm under the whole-voltage above-500 V
    reading (#128 (B2)) -- for disclosure in the report."""
    return _pair_required(delta_v, surface, coating, altitude_m,
                          termination, required_spacing_alt_mm)


# ------------------------------------------------------------ geometry
def _pt_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 <= 0:
        t = 0.0
    else:
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy), (qx, qy)


def _seg_seg(a, b):
    """(distance, point on a, point on b) between two segments
    given as (x1, y1, x2, y2)."""
    def orient(px, py, qx, qy, rx, ry):
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    d1 = orient(bx1, by1, bx2, by2, ax1, ay1)
    d2 = orient(bx1, by1, bx2, by2, ax2, ay2)
    d3 = orient(ax1, ay1, ax2, ay2, bx1, by1)
    d4 = orient(ax1, ay1, ax2, ay2, bx2, by2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) \
            and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0:
        # #89: report the actual intersection, not the start points
        t = ((bx1 - ax1) * (by2 - by1) - (by1 - ay1) * (bx2 - bx1)) / float(
            (ax2 - ax1) * (by2 - by1) - (ay2 - ay1) * (bx2 - bx1))
        ix, iy = ax1 + t * (ax2 - ax1), ay1 + t * (ay2 - ay1)
        return 0.0, (ix, iy), (ix, iy)
    best = None
    for (px, py, seg, on_a) in ((ax1, ay1, b, True), (ax2, ay2, b, True),
                                (bx1, by1, a, False), (bx2, by2, a, False)):
        d, q = _pt_seg(px, py, seg[0], seg[1], seg[2], seg[3])
        cand = (d, (px, py), q) if on_a else (d, q, (px, py))
        if best is None or cand[0] < best[0]:
            best = cand
    return best


def _item_dist(p, q):
    """Distance between two copper items minus their extents.
    Items: ('seg', x1, y1, x2, y2, halfwidth) or
    ('disc', x, y, r).  Returns (gap_mm, point_p, point_q)."""
    if p[0] == 'disc' and q[0] == 'disc':
        d = math.hypot(p[1] - q[1], p[2] - q[2])
        return d - p[3] - q[3], (p[1], p[2]), (q[1], q[2])
    if p[0] == 'disc':
        d, pt = _pt_seg(p[1], p[2], q[1], q[2], q[3], q[4])
        return d - p[3] - q[5], (p[1], p[2]), pt
    if q[0] == 'disc':
        d, pt = _pt_seg(q[1], q[2], p[1], p[2], p[3], p[4])
        return d - q[3] - p[5], pt, (q[1], q[2])
    d, pa, pb = _seg_seg(p[1:5], q[1:5])
    return d - p[5] - q[5], pa, pb


def min_clearance(items_a, items_b):
    """(gap_mm, loc_a, loc_b) -- the minimum copper-to-copper gap
    between two item sets on one layer, with the limiting points."""
    best = None
    for p in items_a:
        for q in items_b:
            r = _item_dist(p, q)
            if best is None or r[0] < best[0]:
                best = r
    return best


def _point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def _near_item(x, y, item, extra=0.0):
    if item[0] == 'disc':
        return math.hypot(x - item[1], y - item[2]) <= item[3] + extra
    d, _ = _pt_seg(x, y, item[1], item[2], item[3], item[4])
    return d <= item[5] + extra


def creepage_path(items_a, items_b, blockers, outline, cutouts,
                  pitch=0.25, filled=(), budget_s=20.0):
    """Shortest surface path (mm) between two copper sets on one
    layer, as (value_mm_or_None, pitch_used_mm) (#146 (B21): the
    pitch actually used is reported -- above the cell cap it is
    enlarged, and a result is only as fine as its pitch; the
    search also honours a wall-clock budget and returns None
    when it is exhausted, which callers must report as
    'not verified', never as a pass).
    Grid Dijkstra: cells outside the board outline or inside a
    cutout are not surface.  Cells covered by FOREIGN copper
    (blockers / filled zones) are traversable at zero cost (#87):
    an intervening conductor bridges the surface, so creepage is
    the sum of the insulating gaps on either side of it --
    treating it as a wall would count its full perimeter and
    overestimate the creepage (the unsafe direction for a
    minimum check).  Start/goal cells are free cells within one pitch
    of the respective domain's copper."""
    t0 = time.monotonic()
    xs = [c for it in items_a + items_b
          for c in ((it[1], it[3]) if it[0] == 'seg' else (it[1],))]
    ys = [c for it in items_a + items_b
          for c in ((it[2], it[4]) if it[0] == 'seg' else (it[2],))]
    x0, x1 = min(xs) - 3.0, max(xs) + 3.0
    y0, y1 = min(ys) - 3.0, max(ys) + 3.0
    if outline:
        # the surface path may detour anywhere on the board (e.g.
        # around the far end of a slot) -- model the whole board
        x0 = min(x0, min(p[0] for p in outline))
        x1 = max(x1, max(p[0] for p in outline))
        y0 = min(y0, min(p[1] for p in outline))
        y1 = max(y1, max(p[1] for p in outline))
    nx = max(2, int((x1 - x0) / pitch) + 1)
    ny = max(2, int((y1 - y0) / pitch) + 1)
    if nx * ny > 400000:
        pitch = pitch * math.sqrt(nx * ny / 400000.0)
        nx = max(2, int((x1 - x0) / pitch) + 1)
        ny = max(2, int((y1 - y0) / pitch) + 1)

    def centre(i, j):
        return (x0 + i * pitch, y0 + j * pitch)

    blocked = [[False] * nx for _ in range(ny)]
    metal = [[False] * nx for _ in range(ny)]
    in_a = [[False] * nx for _ in range(ny)]
    in_b = [[False] * nx for _ in range(ny)]
    for j in range(ny):
        for i in range(nx):
            cx, cy = centre(i, j)
            if any(_near_item(cx, cy, it) for it in items_a):
                in_a[j][i] = True
            if any(_near_item(cx, cy, it) for it in items_b):
                in_b[j][i] = True
            if outline and not _point_in_poly(cx, cy, outline):
                blocked[j][i] = True
                continue
            if any(_point_in_poly(cx, cy, c) for c in cutouts):
                blocked[j][i] = True
                continue
            if any(_point_in_poly(cx, cy, f) for f in filled):
                metal[j][i] = True
                continue
            if any(_near_item(cx, cy, it) for it in blockers):
                metal[j][i] = True
    # start/goal: free cells 4-adjacent to the domain's own copper,
    # so the path measures edge-to-edge within ~one pitch
    starts, goals = [], set()
    for j in range(ny):
        for i in range(nx):
            if blocked[j][i]:
                continue
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ni, nj = i + di, j + dj
                if 0 <= ni < nx and 0 <= nj < ny:
                    if in_a[nj][ni]:
                        starts.append((i, j))
                    if in_b[nj][ni]:
                        goals.add((i, j))
    if not starts or not goals:
        return None, pitch
    dist = {s: 0.0 for s in starts}
    pq = [(0.0, s) for s in starts]
    heapq.heapify(pq)
    diag = pitch * math.sqrt(2.0)
    if time.monotonic() - t0 > budget_s:
        return None, pitch  # setup alone exhausted the budget
    pops = 0
    while pq:
        d, (i, j) = heapq.heappop(pq)
        pops += 1
        if pops % 1024 == 0 and time.monotonic() - t0 > budget_s:
            return None, pitch  # budget exhausted: not verified
        if (i, j) in goals:
            return d, pitch
        if d > dist.get((i, j), 1e18):
            continue
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == 0 and dj == 0:
                    continue
                ni, nj = i + di, j + dj
                if not (0 <= ni < nx and 0 <= nj < ny) \
                        or blocked[nj][ni]:
                    continue
                if di and dj and (blocked[j][ni] or blocked[nj][i]):
                    continue   # #88: no diagonal corner cuts -- the
                    # step would squeeze through the point where two
                    # blocked cells touch, a sub-pitch surface gap
                nd = d + (0.0 if metal[nj][ni]
                          else (diag if di and dj else pitch))
                if nd < dist.get((ni, nj), 1e18):
                    dist[(ni, nj)] = nd
                    heapq.heappush(pq, (nd, (ni, nj)))
    return None, pitch
