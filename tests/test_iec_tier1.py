# Copyright (C) 2026 SiliconBrane Inc.
#
# This file is part of KiCad AI Router.
#
# KiCad AI Router is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# KiCad AI Router is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with KiCad AI Router. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""IEC 62368-1 / 60664-1 Tier 1 (#70 follow-up): per-domain
insulation / material_group / rms_v / explicit min_clearance_mm
and min_creepage_mm.  Validated at the markings gate, rms_v
defaulted (declared > AC peak/sqrt(2) > DC peak), and a declared
override GOVERNS its comparison in place of the IPC-2221B table
value, reported as 'explicit override'.  Tier 2 (applying
pollution_degree / material_group / insulation to an IEC table
estimator) is deliberately NOT here -- it is kill-gated on
cross-verified vendor references."""
import json, math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import hvdomains as H, markings, markings_model as mm


def test_effective_rms_v():
    assert H.effective_rms_v({'rms_v': 100, 'peak_v': 400, 'ac': True}) == 100.0
    assert abs(H.effective_rms_v({'peak_v': 325, 'ac': True})
               - 325 / math.sqrt(2.0)) < 1e-9
    assert H.effective_rms_v({'peak_v': -48}) == 48.0
    assert H.effective_rms_v({'peak_v': 48, 'ac': False}) == 48.0


def _doc(**kw):
    d = {'name': 'HV', 'nets': ['HV1'], 'peak_v': 400}
    d.update(kw)
    return {'version': 1, 'hv_domains': [d]}


def test_tier1_validation():
    out = markings.validate(_doc(insulation='reinforced',
                                 material_group='IIIb', rms_v=230,
                                 min_clearance_mm=2.0,
                                 min_creepage_mm=3.2))
    e = out['hv_domains'][0]
    assert e['rms_v'] == 230.0 and e['min_creepage_mm'] == 3.2
    for bad in ({'insulation': 'double'}, {'material_group': 'III'},
                {'material_group': 'IV'}, {'rms_v': 0}, {'rms_v': -5},
                {'min_clearance_mm': 0}, {'min_creepage_mm': -1},
                {'min_clearance_mm': 'wide'}):
        try:
            markings.validate(_doc(**bad))
        except markings.MarkingsError:
            pass
        else:
            raise AssertionError('domain %r accepted' % (bad,))


def test_tier1_model_roundtrip():
    e = mm.hv_entry({'name': 'HV', 'nets': 'HV1', 'peak_v': '400',
                     'insulation': 'basic', 'material_group': 'II',
                     'rms_v': '230', 'min_clearance_mm': '2.0',
                     'min_creepage_mm': '3.2'})
    assert e['insulation'] == 'basic' and e['material_group'] == 'II'
    assert e['rms_v'] == 230.0 and e['min_creepage_mm'] == 3.2
    f = mm.hv_form(e)
    assert f['insulation'] == 'basic' and f['rms_v'] == 230.0


def _build(path, lv_extra):
    import pcbnew
    V = pcbnew.VECTOR2I
    NM = 1000000
    b = pcbnew.BOARD()
    nets = {}
    for name in ('HV1', 'LV1'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n
    for (x1, y1, x2, y2) in [(2, 2, 70, 2), (70, 2, 70, 38),
                             (70, 38, 2, 38), (2, 38, 2, 2)]:
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(x1 * NM, y1 * NM)); s.SetEnd(V(x2 * NM, y2 * NM))
        s.SetLayer(pcbnew.Edge_Cuts)
        b.Add(s)
    def fp(ref, net, x, y):
        f = pcbnew.FOOTPRINT(b)
        f.SetReference(ref)
        p = pcbnew.PAD(f)
        p.SetNumber('1'); p.SetPosition(V(x * NM, y * NM))
        p.SetSize(V(2 * NM, 2 * NM))
        p.SetShape(pcbnew.PAD_SHAPE_RECT)
        p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu)
        p.SetLayerSet(ls)
        p.SetNet(nets[net])
        f.Add(p)
        b.Add(f)
    fp('U1', 'HV1', 15, 20)
    fp('U2', 'LV1', 19, 20)      # 2.0 mm pad gap
    with open(path.replace('.kicad_pcb', '.kicad_pro'), 'w') as fh:
        fh.write('{}\n')
    stack = ('(stackup (layer "F.Cu" (type "copper") (thickness 0.035)) '
             '(layer "dielectric 1" (type "core") (thickness 1.51) '
             '(material "FR4") (epsilon_r 4.5) (loss_tangent 0.02)) '
             '(layer "B.Cu" (type "copper") (thickness 0.035)))')
    pcbnew.SaveBoard(path, b)
    t = open(path).read()
    i = t.index('(setup')
    open(path, 'w').write(t[:i + 6] + '\n    ' + stack + t[i + 6:])
    lv = {'name': 'LV', 'nets': ['LV1'], 'peak_v': 0}
    lv.update(lv_extra)
    mk = {'version': 1, 'hv_domains': [
        {'name': 'HV', 'nets': ['HV1'], 'peak_v': 400, 'ac': True}, lv]}
    with open(path.replace('.kicad_pcb', '.ai-router-markings.json'), 'w') as fh:
        json.dump(mk, fh)


def _rep(tmp_path, lv_extra):
    from ai_router.pipeline import Pipeline
    board = str(tmp_path / 'iec.kicad_pcb')
    _build(board, lv_extra)
    pipe = Pipeline({'strip_existing': False,
                     'tracemaker_path': '/nonexistent'},
                    board, lambda m: None)
    return pipe.tool_hv_domains()


def test_override_governs_clearance(tmp_path):
    # both overrides declared: the creepage grid reads 1.5 mm on
    # this fixture and the checker holds a one-pitch margin, so
    # the creepage side needs its own governing value too
    rep = _rep(tmp_path, {'min_clearance_mm': 1.5,
                          'min_creepage_mm': 1.0})
    pair = rep['pairs'][0]
    fc = [e for e in pair['layers'] if e['layer'] == 'F.Cu'][0]
    # table value at 400 V is 2.5 mm and the gap is 2.0 mm; the
    # declared 1.5 mm override governs -> no violation
    assert fc['required_mm'] == 1.5, fc
    assert fc['column'] == 'explicit override', fc
    assert fc['requirement_source'] == 'explicit override', fc
    # #236: creepage is FLOORED at the (overridden) clearance
    # -- the declared 1.0 mm creepage override cannot require
    # less than the 1.5 mm clearance beside it (the checker's
    # own spec comment); this assertion pinned the pre-fix
    # substitution and read 1.0 before #236.
    assert fc['creepage_required_mm'] == 1.5, fc
    # ...and the fixture's measured creepage path is exactly
    # 1.5 mm, so the floored requirement (held with the
    # checker's one-pitch margin) now flags a creepage
    # violation where the pre-fix substitution reported none.
    # The clearance override still governs its own comparison:
    # no clearance violation at the 2.5 mm table value.
    kinds = {(v['kind'], v['required_mm']) for v in pair['violations']}
    assert kinds == {('creepage', 1.5)}, pair['violations']
    # rms reporting: HV is AC 400 peak -> 282.8 derived
    a = [x for x in rep['assumptions'] if x['domain'] == 'HV'][0]
    assert abs(a['rms_v'] - 400 / math.sqrt(2.0)) < 0.01, a
    assert a['rms_v_source'] == 'derived_ac_peak', a


def test_override_governs_creepage_only(tmp_path):
    rep = _rep(tmp_path, {'min_creepage_mm': 5.0})
    pair = rep['pairs'][0]
    fc = [e for e in pair['layers'] if e['layer'] == 'F.Cu'][0]
    assert fc['required_mm'] == 2.5, fc           # clearance: table
    assert fc['creepage_required_mm'] == 5.0, fc  # creepage: override
    kinds = {(v['kind'], v['required_mm']) for v in pair['violations']}
    assert ('clearance', 2.5) in kinds, kinds
    assert ('creepage', 5.0) in kinds, kinds


def test_creepage_never_below_clearance(tmp_path):
    # #236 (5a): clearance override 4.0 with a SMALLER creepage
    # override 2.5. The checker's own spec comment states
    # creepage never requires less than the (possibly
    # overridden) clearance in this single-table model --
    # before the fix creepage_required_mm read 2.5, so a pair
    # 3 mm apart passed a 4 mm requirement.
    rep = _rep(tmp_path, {'min_clearance_mm': 4.0,
                          'min_creepage_mm': 2.5})
    pair = rep['pairs'][0]
    fc = [e for e in pair['layers'] if e['layer'] == 'F.Cu'][0]
    assert fc['required_mm'] == 4.0, fc
    assert fc['creepage_required_mm'] == 4.0, fc


def test_domain_setup_honours_tier1_overrides(tmp_path):
    # #236 (5b): _domain_setup's materialise radius must take
    # the same Tier 1 override maximum _eng_patch_pro_hv
    # takes (#200: the paths can never disagree). A 5.0 mm
    # override on a domain whose table requirement is smaller
    # must set the rect radius; before the fix the override
    # was ignored here.
    import types
    from ai_router import pipeline as pl
    from ai_router.pipeline import Pipeline
    board = str(tmp_path / 'iec.kicad_pcb')
    _build(board, {})
    pipe = Pipeline({'strip_existing': False,
                     'tracemaker_path': '/nonexistent'},
                    board, lambda m: None)
    doms = [{'name': 'HV', 'nets': ['HV1'], 'peak_v': 400,
             'ac': True, 'materialise': True,
             'min_clearance_mm': 5.0},
            {'name': 'LV', 'nets': ['LV1'], 'peak_v': 0}]
    table = pl._hv_worst_pair_req_mm(doms[0], doms,
                                     terminations=True)
    assert 0 < table < 5.0, table   # fixture sanity
    facts = {'layers': ['F.Cu'],
             'nets': {'HV1': {'pads': [
                 {'x': 15.0, 'y': 20.0, 'w': 2.0, 'h': 2.0,
                  'layers': ['F.Cu']}]}}}
    pipe._worker = lambda *a, **k: facts
    mk = types.SimpleNamespace(hv_domains=doms, quiet=[])
    out = pipe._domain_setup(mk)
    assert out and out['entries'] == 1, out
    entries = json.load(open(pipe._domains_path))
    rect = entries[0]['rects'][0]['rect']
    # pad half-diagonal term is max(w, h)/2 = 1.0; radius must
    # be 1.0 + 5.0 (the override), not 1.0 + table.
    assert rect == [9.0, 14.0, 21.0, 26.0], rect


if __name__ == '__main__':
    test_effective_rms_v()
    test_tier1_validation()
    test_tier1_model_roundtrip()
    print('iec tier1 (unit) OK')
