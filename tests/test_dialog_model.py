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

"""v1.12/#168: the dialog's pure model -- config-string <-> control
mapping (backward compatibility with existing config.json files),
board-fact classification + smart defaults, URL validation, and the
backend overrides contract. Pure python.
Run: python3 tests/test_dialog_model.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import ui_model as um

ORDER = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
NOMAD_LAYERS = [{'canonical': 'F.Cu', 'name': 'F.Cu'},
                {'canonical': 'In1.Cu', 'name': 'GND'},
                {'canonical': 'In2.Cu', 'name': '3V3'},
                {'canonical': 'B.Cu', 'name': 'B.Cu'}]
NOMAD_ZONES = {'F.Cu': ['GND', 'GND', 'GND'], 'In1.Cu': ['GND'],
               'In2.Cu': ['3V3'], 'B.Cu': ['GND']}


# ------------------------------------------------- backward compat
def test_owner_config_round_trips():
    # The owner's saved Windows config shape (2026-11-25): llm mode,
    # both inner layers banned by canonical name, empty cost spec.
    assert um.parse_ban_list('In1.Cu,In2.Cu') == ['In1.Cu', 'In2.Cu']
    assert um.format_ban_spec(['In2.Cu', 'In1.Cu'], ORDER) == 'In1.Cu,In2.Cu'
    assert um.parse_cost_map('') == ({}, None)
    m, err = um.parse_cost_map('In1.Cu=10,In2.Cu=1.5')
    assert err is None and m == {'In1.Cu': 10.0, 'In2.Cu': 1.5}
    assert um.format_cost_spec(m, ORDER) == 'In1.Cu=10,In2.Cu=1.5'


def test_non_preset_factor_is_lossless():
    # A factor outside the dropdown presets (7.5) must survive the
    # round trip through the controls unchanged.
    m, err = um.parse_cost_map('In2.Cu=7.5')
    assert err is None and um.format_cost_spec(m, ORDER) == 'In2.Cu=7.5'
    assert um.format_factor(10.0) == '10' and um.format_factor(1.25) == '1.25'


def test_invalid_cost_spec_is_an_error_not_a_silent_off():
    for bad in ('In1.Cu=abc', 'In1.Cu=1.234', 'In1.Cu=10,In1.Cu=5',
                'In1.Cu', 'In1.Cu=0.5', 'In1.Cu=1001'):
        m, err = um.parse_cost_map(bad)
        assert m is None and err, bad


def test_unresolvable_names_are_preserved():
    aliases = um.alias_map(NOMAD_LAYERS)
    res, unres = um.resolve_names(['GND', '3v3', 'In9.Cu'], aliases)
    assert res == ['In1.Cu', 'In2.Cu'] and unres == ['In9.Cu']
    # ...and formatting keeps the foreign name rather than dropping it
    assert um.format_ban_spec(['In1.Cu', 'In9.Cu'], ORDER) == 'In1.Cu,In9.Cu'
    m = {'In2.Cu': 2.0, 'In9.Cu': 5.0}
    assert um.format_cost_spec(m, ORDER) == 'In2.Cu=2,In9.Cu=5'


# ------------------------------------------------- classification
def test_nomad_classification_matches_measured_board():
    c = um.classify_layers(NOMAD_LAYERS, NOMAD_ZONES)
    assert c['In1.Cu']['kind'] == 'plane'      # GND: sole GND-class net, inner
    assert c['In2.Cu']['kind'] == 'pour'       # 3V3: a power pour, NOT a plane
    assert c['F.Cu']['kind'] == 'pour'         # surface GND pours are not a plane
    assert c['B.Cu']['kind'] == 'pour'
    assert um.recommended_ban(c) == {'In1.Cu'}


def test_two_layer_and_six_layer_boards():
    two = [{'canonical': 'F.Cu', 'name': 'F.Cu'}, {'canonical': 'B.Cu', 'name': 'B.Cu'}]
    c = um.classify_layers(two, {'F.Cu': [], 'B.Cu': ['GND']})
    assert um.recommended_ban(c) == set()      # surface layers never banned
    six_layers = [{'canonical': n, 'name': n} for n in
                  ('F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'B.Cu')]
    zones = {'F.Cu': [], 'In1.Cu': ['GND'], 'In2.Cu': ['+5V'],
             'In3.Cu': ['AGND'], 'In4.Cu': [], 'B.Cu': []}
    c = um.classify_layers(six_layers, zones)
    assert um.recommended_ban(c) == {'In1.Cu', 'In3.Cu'}
    assert c['In2.Cu']['kind'] == 'pour' and c['In4.Cu']['kind'] == 'signal'


def test_law_differs():
    c = um.classify_layers(NOMAD_LAYERS, NOMAD_ZONES)
    assert um.law_differs(['In1.Cu'], {}, c) is False
    assert um.law_differs(['In1.Cu', 'In2.Cu'], {}, c) is True   # the owner's saved law
    assert um.law_differs(['In1.Cu'], {'In2.Cu': 1.5}, c) is True
    assert um.law_differs([], {}, c) is True


# ------------------------------------------------- misc contract
def test_url_validation():
    assert um.valid_http_url('http://192.168.1.192:8731')
    assert um.valid_http_url('http://127.0.0.1:8080/v1')
    assert not um.valid_http_url('127.0.0.1:8731')
    assert not um.valid_http_url('http://host-without-port')
    assert not um.valid_http_url('')


def test_overrides_carry_the_llm_budget():
    cfg = {'mode': 'llm', 'max_passes': 4, 'no_tracks_on': 'In1.Cu',
           'layer_cost': '', 'strip_existing': True, 'max_tool_calls': 96,
           'endpoint': 'http://server-owned'}
    ov = um.build_overrides(cfg)
    assert ov['max_tool_calls'] == 96          # decision 12: the dialog value governs
    assert 'endpoint' not in ov                # server-owned keys never ride along
    assert set(ov) == set(um.OVERRIDE_KEYS)


def test_capped_open_size_never_exceeds_the_display():
    # #169: a 768-line laptop -- the owner's case. Natural 966x1057
    # must cap to 93% of the client height, width under 95%.
    w, h, mw, mh = um.capped_open_size(966, 1057, 1366, 768)
    assert (w, h) == (966, 714) and h <= 768
    assert (mw, mh) == (560, 400)
    # a large display: the natural size passes through untouched
    w, h, mw, mh = um.capped_open_size(966, 1057, 2560, 1440)
    assert (w, h) == (966, 1057)
    # a tiny display: the cap wins over the floor
    w, h, mw, mh = um.capped_open_size(966, 1057, 800, 600)
    assert (w, h) == (760, 558) and (mw, mh) == (560, 400)
    # a small natural size still opens at the usable floor
    w, h, mw, mh = um.capped_open_size(300, 200, 1920, 1080)
    assert (w, h) == (560, 400)


if __name__ == '__main__':
    for _n, _f in sorted(list(globals().items())):
        if _n.startswith('test_'):
            _f()
    print('PASS test_dialog_model')
