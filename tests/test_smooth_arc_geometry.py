"""Regression test for #142 (third review B16): cmd_smooth built the
fillet arc around a centre at R/sin(phi/2) from the vertex instead of
R/cos(phi/2), so every bend except 90 degrees got the wrong arc -- a
45-degree bend became a 301-degree loop.  The arc must have radius R
(= 3x track width here), sweep exactly the turn angle, and be tangent
to both legs."""
import math, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

WORKER = os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py')


def _pcbnew_available():
    try:
        r = subprocess.run([sys.executable, '-c', 'import pcbnew'],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _nm(x):
    return int(round(x * 1e6))


def _board_with_bend(turn_deg, path):
    import pcbnew
    b = pcbnew.BOARD(); b.SetCopperLayerCount(2)
    net = pcbnew.NETINFO_ITEM(b, 'RF'); b.Add(net)
    for (x1, y1, x2, y2) in [(2, 2, 58, 2), (58, 2, 58, 58), (58, 58, 2, 58), (2, 58, 2, 2)]:
        s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pcbnew.VECTOR2I(_nm(x1), _nm(y1))); s.SetEnd(pcbnew.VECTOR2I(_nm(x2), _nm(y2)))
        s.SetLayer(pcbnew.Edge_Cuts); b.Add(s)
    vx, vy = 30.0, 30.0
    a = math.radians(turn_deg)
    o2 = (vx + 25 * math.cos(a), vy + 25 * math.sin(a))
    for (x1, y1, x2, y2) in [(vx - 25, vy, vx, vy), (vx, vy, o2[0], o2[1])]:
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(_nm(x1), _nm(y1))); t.SetEnd(pcbnew.VECTOR2I(_nm(x2), _nm(y2)))
        t.SetWidth(_nm(0.36)); t.SetLayer(pcbnew.F_Cu); t.SetNetCode(net.GetNetCode())
        b.Add(t)
    pcbnew.SaveBoard(path, b)


def _arc_measure(path):
    import pcbnew
    b = pcbnew.LoadBoard(path)
    arcs = [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_ARC_T]
    assert len(arcs) == 1, 'expected exactly one smoothed arc, got %d' % len(arcs)
    arc = arcs[0]
    def pt(v): return (v.x / 1e6, v.y / 1e6)
    p1, pm, p2 = pt(arc.GetStart()), pt(arc.GetMid()), pt(arc.GetEnd())
    # circle centre from the perpendicular bisectors of p1-pm and pm-p2
    def mid(a, c): return ((a[0] + c[0]) / 2, (a[1] + c[1]) / 2)
    def perp_bisect(a, c):
        m = mid(a, c); d = (c[0] - a[0], c[1] - a[1])
        return m, (-d[1], d[0])
    m1, d1 = perp_bisect(p1, pm); m2, d2 = perp_bisect(pm, p2)
    det = d1[0] * d2[1] - d1[1] * d2[0]
    t = ((m2[0] - m1[0]) * d2[1] - (m2[1] - m1[1]) * d2[0]) / det
    c = (m1[0] + t * d1[0], m1[1] + t * d1[1])
    R = math.hypot(p1[0] - c[0], p1[1] - c[1])
    def ang(p): return math.atan2(p[1] - c[1], p[0] - c[0])
    def sweep(a1, a2):
        d = (a2 - a1) % (2 * math.pi)
        return math.degrees(d if d <= math.pi else d - 2 * math.pi)
    total = abs(sweep(ang(p1), ang(pm))) + abs(sweep(ang(pm), ang(p2)))
    return R, total, c


def _check(turn_deg):
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, 't.kicad_pcb')
        _board_with_bend(turn_deg, path)
        r = subprocess.run([sys.executable, WORKER, 'smooth', '--board', path, '--net', 'RF'],
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-2000:]
        R, sweep, centre = _arc_measure(path)
    assert abs(R - 1.08) < 0.03, 'turn %g: built radius %.3f mm, expected 1.08' % (turn_deg, R)
    assert abs(sweep - turn_deg) < 2.0, 'turn %g: built sweep %.1f deg' % (turn_deg, sweep)
    # tangency: centre must be exactly R from each leg line (leg 1 is y=30)
    assert abs(abs(centre[1] - 30.0) - R) < 0.03, 'turn %g: arc not tangent to leg 1' % turn_deg


def test_smooth_arc_45_degrees():
    if not _pcbnew_available(): print('skip'); return
    _check(45)


def test_smooth_arc_135_degrees():
    if not _pcbnew_available(): print('skip'); return
    _check(135)


def test_smooth_arc_90_degrees():
    if not _pcbnew_available(): print('skip'); return
    _check(90)
