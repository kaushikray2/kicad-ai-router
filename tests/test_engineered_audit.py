"""#42 integration: placement audit on a synthetic board with a
real fp-lib-table + .pretty library on disk.

U1 (GNSS module) carries an F.Cu keepout on its board copy; the
library footprint carries the same F.Cu zone PLUS a B.Cu zone
the board copy lacks -> exactly one zone materialises, with
library provenance in the ledger.  U2's pad intrudes into U1's
keepout.  Y1's crystal library footprint carries no rules ->
library gap.  No markings sidecar: the audit is independent."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest

LIB_MOD = '''(footprint "GNSS_Module"
  (property "AIRuleEdgeClearanceMm" "5")
  (zone (net 0) (net_name "") (layers "F.Cu") (hatch full)
    (keepout (tracks not_allowed) (vias not_allowed) (pads not_allowed) (copperpour not_allowed) (footprints not_allowed))
    (polygon (pts (xy 0 0) (xy 10 0) (xy 10 8) (xy 0 8))))
  (zone (net 0) (net_name "") (layers "B.Cu") (hatch full)
    (keepout (tracks not_allowed) (vias not_allowed) (pads not_allowed) (copperpour not_allowed) (footprints not_allowed))
    (polygon (pts (xy 0 0) (xy 12 0) (xy 12 10) (xy 0 10)))))
'''
LIB_XTAL = '(footprint "Crystal_SMD" (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))' + chr(10)


def _nm(x):
    return int(round(x * 1e6))


def _build(tmp):
    import pcbnew
    def V(x, y):
        return pcbnew.VECTOR2I(_nm(x), _nm(y))
    libs = tmp / 'libs'
    (libs / 'Mod.pretty').mkdir(parents=True)
    (libs / 'Xtal.pretty').mkdir(parents=True)
    (libs / 'Mod.pretty' / 'GNSS_Module.kicad_mod').write_text(LIB_MOD)
    (libs / 'Xtal.pretty' / 'Crystal_SMD.kicad_mod').write_text(LIB_XTAL)
    (tmp / 'fp-lib-table').write_text(
        '(fp_lib_table\n'
        '  (lib (name "Mod") (type "KiCad") (uri "${KIPRJMOD}/libs/Mod.pretty") (options "") (descr ""))\n'
        '  (lib (name "Xtal") (type "KiCad") (uri "${KIPRJMOD}/libs/Xtal.pretty") (options "") (descr "")))\n')
    board = str(tmp / 'au.kicad_pcb')
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(2)
    nets = {}
    for name in ('GND', 'RF_OUT', 'V5', 'XTAL'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n
    for (x1, y1, x2, y2) in [(2, 2, 58, 2), (58, 2, 58, 38), (58, 38, 2, 38), (2, 38, 2, 2)]:
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(x1, y1)); s.SetEnd(V(x2, y2))
        s.SetLayer(pcbnew.Edge_Cuts)
        b.Add(s)
    def fp(ref, fpid, x, y, pads):
        f = pcbnew.FOOTPRINT(b)
        f.SetReference(ref)
        f.SetFPIDAsString(fpid)
        f.SetPosition(V(x, y))
        for (num, net, px, py) in pads:
            p = pcbnew.PAD(f)
            p.SetNumber(num); p.SetPosition(V(px, py))
            p.SetSize(V(1.0, 1.0))
            p.SetShape(pcbnew.PAD_SHAPE_RECT)
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu)
            p.SetLayerSet(ls)
            p.SetNetCode(nets[net].GetNetCode())
            f.Add(p)
        b.Add(f)
        return f
    u1 = fp('U1', 'Mod:GNSS_Module', 30, 20, [('1', 'RF_OUT', 31, 21), ('2', 'GND', 31, 23)])
    z = pcbnew.ZONE(u1)
    ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu)
    z.SetLayerSet(ls); z.SetIsRuleArea(True)
    z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True); z.SetDoNotAllowZoneFills(True)
    out = z.Outline(); out.NewOutline()
    ch = out.Outline(0)
    for (lx, ly) in [(0, 0), (10, 0), (10, 8), (0, 8)]:
        ch.Append(_nm(30 + lx), _nm(20 + ly))
    u1.Add(z)
    s = pcbnew.PCB_SHAPE(u1)
    s.SetShape(pcbnew.SHAPE_T_RECT)
    s.SetStart(V(30, 20)); s.SetEnd(V(41, 29))
    s.SetLayer(pcbnew.F_CrtYd)
    u1.Add(s)
    fp('U2', 'Misc:MCU', 35, 24, [('1', 'V5', 35, 24)])
    fp('Y1', 'Xtal:Crystal_SMD', 48, 32, [('1', 'XTAL', 48, 32)])
    pcbnew.SaveBoard(board, b)
    return board


@pytest.mark.skipif(not os.path.exists('/usr/lib/python3/dist-packages/pcbnew.py'),
                    reason='pcbnew unavailable')
def test_placement_audit_end_to_end(tmp_path):
    board = _build(tmp_path)
    from ai_router.pipeline import Pipeline
    pipe = Pipeline({'strip_existing': False, 'tracemaker_path': '/nonexistent'},
                    board, lambda m: None)
    rep = pipe._audit
    assert rep and rep['status'] == 'ok', rep
    mats = rep['materialise']
    assert len(mats) == 1 and mats[0]['ref'] == 'U1' and mats[0]['layers'] == ['B.Cu'], mats
    assert mats[0]['outline'][2] == [42.0, 30.0]
    assert rep['materialised'] == [{'ref': 'U1', 'layers': ['B.Cu']}]
    kinds = [c['kind'] for c in rep['footprints']['U1']['checks']]
    assert 'keepout_intrusion' in kinds
    assert rep['footprints']['U1']['properties_from'] == 'library'
    assert rep['gaps'] == [{'ref': 'Y1', 'fpid': 'Xtal:Crystal_SMD', 'class': 'crystal'}]
    assert 'RF_OUT' in pipe._engineered_first_nets()
    steps = [e.get('step') for e in pipe.ledger]
    assert 'placement_materialise' in steps and 'placement_audit' in steps
    # the materialised zone is really on the working copy
    import pcbnew
    b2 = pcbnew.LoadBoard(pipe.current)
    rule_areas = [z for z in b2.Zones() if z.GetIsRuleArea()]
    assert len(rule_areas) == 1
    assert [b2.GetLayerName(l) for l in rule_areas[0].GetLayerSet().CuStack()] == ['B.Cu']
