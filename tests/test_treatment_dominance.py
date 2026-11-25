"""#134 (B9) regressions: treatment adoption is dominance-based
(no worse in faults AND no worse in connectivity AND no worse in
splits), and cmd_power_widen / cmd_smooth never modify locked
copper."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import pipeline as pl


def _gate(faults, unc, splits=0):
    return {'classes': {'clearance': faults}, 'unconnected_total': unc,
            'split_nets': ['n'] * splits, 'banned_layer_tracks': 0}


def _pipe(tmp_path, before, after):
    p = pl.Pipeline.__new__(pl.Pipeline)
    cur = str(tmp_path / 'cur.kicad_pcb')
    open(cur, 'w').write('board')
    p.current = cur
    p.last_gate = before
    p.workdir = str(tmp_path)
    p._oseq = 0
    p.cfg = {}
    p.log = lambda *a: None
    p._worker = lambda *a, **k: {'status': 'ok'}
    p.tool_gate = lambda: after
    return p


def test_fault_gain_bought_with_connectivity_is_reverted(tmp_path):
    # the B9 trade: -1 fault, +2 unconnected -- lexicographic
    # order adopted this; dominance must not
    p = _pipe(tmp_path, _gate(5, 10), _gate(4, 12))
    r = p._eng_gate_try('whatever', [], 't1')
    assert r['adopted'] is False and r['status'] == 'reverted_dominance', r


def test_dominating_treatment_is_adopted(tmp_path):
    p = _pipe(tmp_path, _gate(5, 10), _gate(4, 9))
    r = p._eng_gate_try('whatever', [], 't2')
    assert r['adopted'] is True, r


def test_connectivity_gain_bought_with_faults_is_reverted(tmp_path):
    p = _pipe(tmp_path, _gate(5, 10), _gate(6, 8))
    r = p._eng_gate_try('whatever', [], 't3')
    assert r['adopted'] is False and r['status'] == 'reverted_faults', r


def test_equal_key_is_adopted(tmp_path):
    p = _pipe(tmp_path, _gate(5, 10), _gate(5, 10))
    r = p._eng_gate_try('whatever', [], 't4')
    assert r['adopted'] is True, r


def _nm(x):
    return int(round(x * 1e6))


def _track_board(path, lock_second, lock_only=False):
    import pcbnew
    b = pcbnew.BOARD(); b.SetCopperLayerCount(2)
    net = pcbnew.NETINFO_ITEM(b, 'RF'); b.Add(net)
    legs = [(5, 30, 30, 30), (30, 30, 30, 55)]
    if lock_only:
        legs = [(5, 5, 25, 5)]
    for i, (x1, y1, x2, y2) in enumerate(legs):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(_nm(x1), _nm(y1)))
        t.SetEnd(pcbnew.VECTOR2I(_nm(x2), _nm(y2)))
        t.SetWidth(_nm(0.36)); t.SetLayer(pcbnew.F_Cu)
        t.SetNetCode(net.GetNetCode())
        if (lock_only and i == 0) or (not lock_only and lock_second and i == 1):
            t.SetLocked(True)
        b.Add(t)
    pcbnew.SaveBoard(path, b)


def test_smooth_skips_locked_copper(tmp_path):
    from ai_router import worker
    locked = str(tmp_path / 'locked.kicad_pcb')
    _track_board(locked, lock_second=True)
    r = worker.cmd_smooth(locked, 'RF')
    assert r['smoothed'] == [], r
    assert any(s.get('why') == 'locked' for s in r['skipped']), r
    free = str(tmp_path / 'free.kicad_pcb')
    _track_board(free, lock_second=False)
    r2 = worker.cmd_smooth(free, 'RF')
    assert len(r2['smoothed']) == 1, r2


def test_power_widen_refuses_locked_copper(tmp_path):
    import pcbnew
    from ai_router import worker
    path = str(tmp_path / 'w.kicad_pcb')
    _track_board(path, lock_second=False, lock_only=True)
    b = pcbnew.LoadBoard(path)
    uuid = [t for t in b.GetTracks()][0].m_Uuid.AsString()
    r = worker.cmd_power_widen(path, {'RF': {uuid: 0.9}})
    assert r['applied'] == [], r
    assert any(x.get('why') == 'locked' for x in r['refused']), r
    b2 = pcbnew.LoadBoard(path)
    assert abs(b2.GetTracks()[0].GetWidth() / 1e6 - 0.36) < 1e-9
