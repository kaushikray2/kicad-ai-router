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

"""Backend parity for engineered nets: the client payload carries
the markings sidecar AND a netlist (sibling .net, else a kicad-cli
export from the sibling .kicad_sch), and the server places both
beside the job's input board where the pipeline looks for them."""
import base64, importlib.util, json, os, shutil, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import pytest
from ai_router import backend_client

BOARD = b'(kicad_pcb fixture)'
SIDECAR = json.dumps({'impedance': [{'net': 'RF_IN', 'z0_ohm': 50}]}).encode()
NETLIST = b'(export (version "E") (nets (net (code "1") (name "VCC5"))))'


def _board_dir(tmp_path, with_net=True, with_sch=False):
    (tmp_path / 'fixture.kicad_pcb').write_bytes(BOARD)
    (tmp_path / 'fixture.ai-router-markings.json').write_bytes(SIDECAR)
    if with_net:
        (tmp_path / 'fixture.net').write_bytes(NETLIST)
    if with_sch:
        (tmp_path / 'fixture.kicad_sch').write_text(
            '(kicad_sch (version 20231120) (generator "test") '
            '(uuid "12345678-1234-1234-1234-123456789012") (paper "A4"))')
    return str(tmp_path / 'fixture.kicad_pcb')


def test_payload_carries_markings_and_netlist(tmp_path):
    payload = backend_client.build_payload(_board_dir(tmp_path), {})
    assert base64.b64decode(payload['markings_b64']) == SIDECAR
    assert base64.b64decode(payload['netlist_b64']) == NETLIST


def test_payload_without_netlist_or_schematic(tmp_path):
    payload = backend_client.build_payload(_board_dir(tmp_path, with_net=False), {})
    assert 'netlist_b64' not in payload
    assert base64.b64decode(payload['markings_b64']) == SIDECAR


def test_payload_unchecked_switch_sends_no_markings(tmp_path):
    # The dialog's engineered_nets switch travels in the overrides;
    # when it is off, the payload must not carry the sidecar or the
    # netlist -- the backend run is then genuinely unmarked.
    payload = backend_client.build_payload(_board_dir(tmp_path), {'engineered_nets': False})
    assert 'markings_b64' not in payload
    assert 'netlist_b64' not in payload


def test_payload_exports_netlist_from_schematic(tmp_path):
    if not shutil.which('kicad-cli'):
        pytest.skip('kicad-cli not on PATH')
    payload = backend_client.build_payload(
        _board_dir(tmp_path, with_net=False, with_sch=True), {})
    assert 'netlist_b64' in payload
    assert b'export' in base64.b64decode(payload['netlist_b64'])


def _load_server(tmp_path, monkeypatch):
    monkeypatch.setenv('AIR_DATA_DIR', str(tmp_path / 'data'))
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'docker', 'server.py')
    spec = importlib.util.spec_from_file_location('air_server_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_server_places_netlist_and_markings(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    jid, dup = server.submit_job(BOARD, None, 'fixture.kicad_pcb', {},
                                 markings_bytes=SIDECAR, netlist_bytes=NETLIST)
    assert not dup
    jd = server.job_dir(jid)
    with open(os.path.join(jd, 'input.net'), 'rb') as fh:
        assert fh.read() == NETLIST
    with open(os.path.join(jd, 'input.ai-router-markings.json'), 'rb') as fh:
        assert fh.read() == SIDECAR
    # a different netlist is a different job (digest covers it)
    jid2, dup2 = server.submit_job(BOARD, None, 'fixture.kicad_pcb', {},
                                   markings_bytes=SIDECAR, netlist_bytes=b'(export other)')
    assert jid2 != jid and not dup2
