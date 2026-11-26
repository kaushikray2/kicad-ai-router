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

"""#199: custom backend servers for the Run on group -- the pure
ui_model list logic (validate / dedupe / add / remove / token sync /
combo mapping), the config round-trip of cfg['backend_servers'],
and the dialog wiring (source level, like test_config_page_controls).
Pure python. Run: python3 tests/test_backend_servers.py"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import ui_model as um
import config as cfgmod

PRESETS = ['http://192.168.1.192:8731',   # ML350
           'http://192.168.22.209:8732',  # zorinbox-1
           'http://127.0.0.1:8731']       # this machine
BENCH = {'name': 'Bench', 'url': 'http://192.168.1.50:8731', 'token': 'tok'}


# ------------------------------------------------------------- validate
def test_validate_accepts_and_normalises():
    e, err = um.validate_new_server(' Bench ', 'http://192.168.1.50:8731/', 'tok', PRESETS, [])
    assert err is None
    assert e == BENCH  # name stripped, trailing slash dropped, token kept


def test_validate_token_optional():
    e, err = um.validate_new_server('Bench', 'https://example.local:8731', '', PRESETS, [])
    assert err is None and e['token'] == ''


def test_validate_rejects_empty_name():
    e, err = um.validate_new_server('   ', 'http://192.168.1.50:8731', '', PRESETS, [])
    assert e is None and 'name' in err


def test_validate_rejects_bad_urls():
    for bad in ('', '  ', 'ftp://host:8731', 'host:8731', 'http://', 'http:///path'):
        e, err = um.validate_new_server('Bench', bad, '', PRESETS, [])
        assert e is None and err, bad


def test_validate_rejects_duplicate_of_preset():
    e, err = um.validate_new_server('ML350 again', 'http://192.168.1.192:8731/', '', PRESETS, [])
    assert e is None and 'built-in preset' in err


def test_validate_rejects_duplicate_of_custom():
    e, err = um.validate_new_server('Bench 2', 'http://192.168.1.50:8731/', '', PRESETS, [BENCH])
    assert e is None and 'already exists' in err


# ------------------------------------------------------------- list ops
def test_add_find_remove_round_trip():
    servers = um.add_server([], BENCH)
    assert servers == [BENCH]
    assert um.find_server(servers, 'http://192.168.1.50:8731/') == BENCH
    assert um.find_server(servers, 'http://192.168.1.99:8731') is None
    assert um.remove_server(servers, BENCH['url']) == []
    assert um.remove_server(servers, 'http://unknown:1') == [BENCH]


def test_list_ops_never_mutate_the_input():
    before = [dict(BENCH)]
    um.add_server(before, {'name': 'X', 'url': 'http://x:1', 'token': ''})
    um.remove_server(before, BENCH['url'])
    um.sync_server_token(before, BENCH['url'], 'changed')
    assert before == [BENCH]


def test_sanitize_drops_junk_and_defaults_token():
    raw = [{'name': ' a ', 'url': 'http://h:1/'}, {'name': '', 'url': 'http://h:2'},
           {'url': 'http://h:3'}, 'junk', None, 42]
    assert um.sanitize_servers(raw) == [{'name': 'a', 'url': 'http://h:1', 'token': ''}]
    assert um.sanitize_servers(None) == []


def test_sync_server_token_updates_only_the_match():
    servers = [dict(BENCH), {'name': 'Lab', 'url': 'http://192.168.1.51:8731', 'token': 'old'}]
    out = um.sync_server_token(servers, 'http://192.168.1.51:8731/', 'new')
    assert out[0]['token'] == 'tok' and out[1]['token'] == 'new'
    # a preset URL is not a custom entry: nothing to sync into
    assert um.sync_server_token(servers, PRESETS[0], 'nope') == servers


def test_combo_labels_and_entries_are_parallel():
    servers = [dict(BENCH)]
    labels = um.server_combo_labels(PRESETS, servers)
    entries = um.server_combo_entries(PRESETS, servers)
    assert labels == PRESETS + ['Bench']  # presets keep their URL labels
    assert len(entries) == len(labels)
    assert entries[:len(PRESETS)] == [None] * len(PRESETS)
    assert entries[len(PRESETS)] == BENCH


# ------------------------------------------------------------- config io
def test_config_round_trips_backend_servers(tmp_path=None):
    d = tempfile.mkdtemp() if tmp_path is None else str(tmp_path)
    path = os.path.join(d, 'config.json')
    old = cfgmod.CONFIG_PATH
    cfgmod.CONFIG_PATH = path
    try:
        cfg = cfgmod.load()
        assert cfg['backend_servers'] == []  # the new default
        cfg['backend_servers'] = [dict(BENCH)]
        cfgmod.save(cfg)
        with open(path) as f:
            raw = json.load(f)
        assert raw['backend_servers'] == [BENCH]
        assert cfgmod.load()['backend_servers'] == [BENCH]
    finally:
        cfgmod.CONFIG_PATH = old


def test_config_without_the_key_still_loads():
    d = tempfile.mkdtemp()
    path = os.path.join(d, 'config.json')
    with open(path, 'w') as f:
        json.dump({'backend_url': 'http://192.168.1.192:8731'}, f)
    old = cfgmod.CONFIG_PATH
    cfgmod.CONFIG_PATH = path
    try:
        assert cfgmod.load()['backend_servers'] == []
    finally:
        cfgmod.CONFIG_PATH = old


# ------------------------------------------------------------- dialog wiring
def test_dialog_wires_the_feature():
    src = open(os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'dialog.py')).read()
    for needle in ("label='Add server…'", "label='Remove server'", 'class AddServerDialog',
                   'validate_new_server', 'server_combo_labels', "cfg['backend_servers']",
                   'sync_server_token'):
        assert needle in src, needle


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
    print('test_backend_servers: %d tests passed' % len(fns))
