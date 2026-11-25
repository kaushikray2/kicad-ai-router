"""#72 item 1: the via-on-pad census behind the gate's reported
metric.  Same-net centre-in-pad, foreign-net centre-in-pad and
annulus-overlap are counted separately; a clear via counts
nowhere.  (Tested on the in-memory board: pcbnew's save/load
renumbers programmatically-created nets, which is outside the
unit under test; cmd_summary forwards the helper's dict.)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _require_pcbnew():
    try:
        import pcbnew  # noqa: F401
    except Exception:
        import pytest
        pytest.skip('pcbnew unavailable')


def _nm(x):
    return int(round(x * 1e6))


def test_via_on_pad_census():
    _require_pcbnew()
    import pcbnew
    from ai_router import worker
    b = pcbnew.BOARD(); b.SetCopperLayerCount(2)
    nets = {}
    for name in ('A', 'B', 'C'):
        nets[name] = pcbnew.NETINFO_ITEM(b, name); b.Add(nets[name])
    fp = pcbnew.FOOTPRINT(b)
    pad = pcbnew.PAD(fp)
    pad.SetSize(pcbnew.VECTOR2I(_nm(1.0), _nm(1.0)))
    pad.SetPosition(pcbnew.VECTOR2I(_nm(20), _nm(20)))
    _ls = pcbnew.LSET(); _ls.AddLayer(pcbnew.F_Cu)
    pad.SetLayerSet(_ls)
    pad.SetNetCode(nets['A'].GetNetCode())
    fp.Add(pad); b.Add(fp)
    for net, x, y in (('A', 20, 20), ('B', 20, 20),
                      ('C', 20.7, 20), ('C', 30, 30)):
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(pcbnew.VECTOR2I(_nm(x), _nm(y)))
        v.SetWidth(_nm(0.6)); v.SetDrill(_nm(0.3))
        v.SetNetCode(nets[net].GetNetCode()); b.Add(v)
    got = worker._via_on_pad_census(b, list(b.GetTracks()))
    assert got == {'centre_same_net': 1, 'centre_foreign_net': 1,
                   'annulus_overlap': 1}, got
