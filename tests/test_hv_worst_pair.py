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

"""#200 regression: the materialise path (_domain_setup) maximised
only over other DECLARED domains and used the CONDUCTOR column
family (B2/B4/A5) for rects stamped around PADS.  Two recorded
discrepancies: (i) a board with exactly one materialise HV domain
generated ZERO keepout rects (no declared neighbour to pair
against; the implicit 0 V rest-of-board domain never entered);
(ii) at 30 V uncoated the radius used B2 = 0.10 mm where pads --
terminations, A6 -- require 0.25 mm.  Plus the one-sidedness
nuance: only the domain's own coating/altitude was consulted,
where the check path takes max over both sides.  All three HV
paths now share _hv_worst_pair_req_mm /
pair_required_symmetric_mm."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import hvdomains as H
from ai_router.pipeline import _hv_worst_pair_req_mm


def _dom(v, coating='uncoated', altitude_m=0, **kw):
    d = {'name': kw.get('name', 'D'), 'nets': ['N'],
         'peak_v': float(v), 'voltage_v': float(v),
         'coating': coating, 'altitude_m': altitude_m}
    d.update(kw)
    return d


def test_symmetric_uncoated_side_dominates():
    # 30 V: conformal side reads A5/A7 = 0.13; the uncoated side
    # reads B2 0.10 / A6 0.25 -> the pair requirement is the
    # uncoated side's 0.25, from BOTH argument orders.
    a = _dom(30, coating='conformal')
    b = _dom(0, coating='uncoated')
    assert H.pair_required_symmetric_mm(30.0, True, a, b, True)[0] == 0.25
    assert H.pair_required_symmetric_mm(30.0, True, b, a, True)[0] == 0.25


def test_single_domain_pairs_against_implicit_rest_of_board():
    # discrepancy (i)+(ii): one domain at 30 V, terminations on:
    # the implicit 0 V pair must produce the A6 value 0.25 mm --
    # pre-fix _domain_setup produced 0.0 (no pairs at all), and
    # with the conductor column alone it would have been 0.10.
    d = _dom(30)
    assert _hv_worst_pair_req_mm(d, [d], terminations=True) == 0.25
    assert _hv_worst_pair_req_mm(d, [d], terminations=False) == 0.10


def test_worst_neighbour_can_be_the_rest_of_board():
    # +325 / +310: the declared pair differs by only 15 V, but
    # each domain's worst neighbour is the 0 V rest of board.
    a, b = _dom(325), _dom(310)
    assert _hv_worst_pair_req_mm(b, [a, b], terminations=True) == 2.50
    assert _hv_worst_pair_req_mm(a, [a, b], terminations=True) == 2.50


def test_implicit_domain_altitude_is_the_set_max():
    # above 3050 m the uncoated conductor column becomes B3;
    # the implicit domain inherits the set's max altitude, so a
    # high-altitude domain's rest-of-board pair is not understated.
    d = _dom(100, altitude_m=4000)
    req = _hv_worst_pair_req_mm(d, [d], terminations=True)
    assert req == H.pair_required_symmetric_mm(100.0, True, d,
                                               {'coating': 'uncoated',
                                                'altitude_m': 4000},
                                               True)[0]
    assert req >= 1.50  # B3 at 100 V


def test_domain_setup_single_materialise_domain_emits_rects(tmp_path):
    # Pipeline-level pin for discrepancy (i): exactly ONE
    # materialise domain on the board.  Pre-fix, _domain_setup
    # returned None and wrote no domains.json; the finisher legs
    # got zero keepouts while the run looked covered.
    import pcbnew
    V = pcbnew.VECTOR2I
    NM = 1000000
    b = pcbnew.BOARD()
    n = pcbnew.NETINFO_ITEM(b, 'HV1')
    b.Add(n)
    for (x1, y1, x2, y2) in [(2, 2, 40, 2), (40, 2, 40, 38),
                             (40, 38, 2, 38), (2, 38, 2, 2)]:
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(int(x1 * NM), int(y1 * NM)))
        s.SetEnd(V(int(x2 * NM), int(y2 * NM)))
        s.SetLayer(pcbnew.Edge_Cuts)
        b.Add(s)
    f = pcbnew.FOOTPRINT(b)
    f.SetReference('U1')
    for (num, x, y) in [('1', 20, 20), ('2', 20, 32)]:
        p = pcbnew.PAD(f)
        p.SetNumber(num)
        p.SetPosition(V(int(x * NM), int(y * NM)))
        p.SetSize(V(int(2 * NM), int(2 * NM)))
        p.SetShape(pcbnew.PAD_SHAPE_RECT)
        p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        ls = pcbnew.LSET()
        ls.AddLayer(pcbnew.F_Cu)
        p.SetLayerSet(ls)
        p.SetNet(n)
        f.Add(p)
    b.Add(f)
    path = str(tmp_path / 'one.kicad_pcb')
    with open(path.replace('.kicad_pcb', '.kicad_pro'), 'w') as fh:
        fh.write('{}\n')
    pcbnew.SaveBoard(path, b)
    mk = {'version': 1, 'hv_domains': [
        {'name': 'HV', 'nets': ['HV1'], 'voltage_v': 30,
         'materialise': True}]}
    with open(path.replace('.kicad_pcb', '.ai-router-markings.json'), 'w') as fh:
        json.dump(mk, fh)
    from ai_router.pipeline import Pipeline
    pipe = Pipeline({'strip_existing': False,
                     'tracemaker_path': '/nonexistent'},
                    path, lambda m: None)
    assert pipe._domains_path and os.path.exists(pipe._domains_path), pipe.ledger
    entries = json.load(open(pipe._domains_path))
    hv = [e for e in entries if e['kind'] == 'hv']
    assert len(hv) == 1 and hv[0]['name'] == 'HV', entries
    # rect half-size = pad half (1.0) + A6 requirement at 30 V (0.25)
    r = hv[0]['rects'][0]['rect']
    assert abs((r[2] - r[0]) / 2.0 - 1.25) < 1e-6, r
    assert abs((r[3] - r[1]) / 2.0 - 1.25) < 1e-6, r


if __name__ == '__main__':
    test_symmetric_uncoated_side_dominates()
    test_single_domain_pairs_against_implicit_rest_of_board()
    test_worst_neighbour_can_be_the_rest_of_board()
    test_implicit_domain_altitude_is_the_set_max()
    print('hv worst pair OK')
