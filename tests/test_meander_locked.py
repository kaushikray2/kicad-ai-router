"""Issue #100: cmd_meander picked the net's longest straight track
and replaced it without checking IsLocked -- locked copper was
silently deleted and redrawn unlocked.  Needs pcbnew."""
import os, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def _require_pcbnew():
    # #130 (B5): a missing pcbnew must be a REAL skip under pytest,
    # not a print-and-return that pytest counts as a pass while
    # running nothing.  Standalone runs keep the print behaviour.
    try:
        import pcbnew  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import pytest
        pytest.skip('pcbnew unavailable')
    except ImportError:
        print('skip: pcbnew unavailable')
    return False



def _nm(x):
    return int(round(x * 1e6))


def _build(path, lock_long=True, lock_short=False):
    import pcbnew
    b = pcbnew.BOARD(); b.SetCopperLayerCount(2)
    n = pcbnew.NETINFO_ITEM(b, 'RF'); b.Add(n)
    def seg(x1, x2, y, locked):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(_nm(x1), _nm(y))); t.SetEnd(pcbnew.VECTOR2I(_nm(x2), _nm(y)))
        t.SetWidth(_nm(0.35)); t.SetLayer(pcbnew.F_Cu); t.SetNetCode(n.GetNetCode())
        t.SetLocked(locked); b.Add(t)
    seg(5, 45, 10, lock_long)     # 40 mm, the longest
    seg(5, 35, 20, lock_short)    # 30 mm
    pcbnew.SaveBoard(path, b)


def _locked_tracks(path):
    import pcbnew
    b = pcbnew.LoadBoard(path)
    return [(t.GetStart().x / 1e6, t.GetEnd().x / 1e6, t.GetStart().y / 1e6)
            for t in b.GetTracks() if t.IsLocked()]


def test_meander_never_touches_locked_tracks():
    if not _require_pcbnew(): return
    from ai_router import worker
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, 't.kicad_pcb')
        _build(path)
        res = worker.cmd_meander(path, 'RF', 2.0)
        assert res.get('added_mm', 0) > 0, res          # the 30 mm track took the bump
        assert _locked_tracks(path) == [(5.0, 45.0, 10.0)], _locked_tracks(path)
        # every candidate locked: nothing changes
        path2 = os.path.join(td, 't2.kicad_pcb')
        _build(path2, lock_long=True, lock_short=True)
        res2 = worker.cmd_meander(path2, 'RF', 2.0)
        assert res2.get('added_mm', 0) == 0 and 'lock' in (res2.get('why') or ''), res2
        assert len(_locked_tracks(path2)) == 2

if __name__ == '__main__':
    test_meander_never_touches_locked_tracks(); print('meander locked OK')
