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

"""Issue #182: naming.project_stem falls back to the lone
.kicad_pro in a directory, but Pipeline.__init__,
backend_client.submit and dialog._deliver_local each resolved
sidecars as splitext(board)+ext -- a board renamed beside
proj.kicad_pro / proj.kicad_dru routed without its netclasses
and DRC rules and delivered without sidecars. All three now use
naming.sidecar_paths.
Run: python3 tests/test_sidecar_resolution.py"""
import base64, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import naming
import backend_client
import pipeline as pl


def _scenario():
    d = tempfile.mkdtemp()
    open(os.path.join(d, 'proj.kicad_pro'), 'w').write('PRO-CONTENT')
    open(os.path.join(d, 'proj.kicad_dru'), 'w').write('DRU-CONTENT')
    board = os.path.join(d, 'renamed.kicad_pcb')
    open(board, 'w').write('(kicad_pcb)')
    return d, board


def test_helper_scenario():
    d, board = _scenario()
    pro, dru = naming.sidecar_paths(board)
    assert pro == os.path.join(d, 'proj.kicad_pro'), pro
    assert dru == os.path.join(d, 'proj.kicad_dru'), dru


def test_helper_board_stem_wins():
    d, board = _scenario()
    open(os.path.join(d, 'renamed.kicad_pro'), 'w').write('OWN')
    pro, dru = naming.sidecar_paths(board)
    assert pro == os.path.join(d, 'renamed.kicad_pro'), pro
    # the board has its OWN project now: proj.kicad_dru belongs to
    # the other project and must not be mixed in
    assert dru is None, dru
    open(os.path.join(d, 'renamed.kicad_dru'), 'w').write('OWN-DRU')
    pro, dru = naming.sidecar_paths(board)
    assert dru == os.path.join(d, 'renamed.kicad_dru'), dru


def test_helper_none():
    d = tempfile.mkdtemp()
    board = os.path.join(d, 'lonely.kicad_pcb')
    open(board, 'w').write('(kicad_pcb)')
    assert naming.sidecar_paths(board) == (None, None)
    open(os.path.join(d, 'a.kicad_pro'), 'w').write('A')
    open(os.path.join(d, 'b.kicad_pro'), 'w').write('B')
    pro, dru = naming.sidecar_paths(board)
    assert pro is None, pro  # ambiguous: no fallback invention


def test_submit_carries_sidecars():
    d, board = _scenario()
    seen = {}
    orig = backend_client._req
    def fake_req(url, path, token=None, method='GET', payload=None, timeout=60):
        seen.update(payload or {})
        import json as _j
        return _j.dumps({'job_id': 'job1'})
    backend_client._req = fake_req
    try:
        backend_client.submit('http://x', 'tok', board, {})
    finally:
        backend_client._req = orig
    assert base64.b64decode(seen['pro_b64']).decode() == 'PRO-CONTENT', seen.keys()
    assert base64.b64decode(seen['dru_b64']).decode() == 'DRU-CONTENT', seen.keys()


def test_pipeline_init_copies_sidecars():
    d, board = _scenario()
    logs = []
    pipe = pl.Pipeline({'strip_existing': False, 'tracemaker_path': '/x'}, board, logs.append)
    assert open(os.path.join(pipe.workdir, 'input.kicad_pro')).read() == 'PRO-CONTENT'
    assert open(os.path.join(pipe.workdir, 'input.kicad_dru')).read() == 'DRU-CONTENT'


def _dialog_part():
    try:
        import wx  # noqa: F401
    except ImportError:
        import pytest
        pytest.importorskip('wx')
        return
    import importlib.machinery, importlib.util
    ld = importlib.machinery.SourceFileLoader('dlg_uut10', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'dialog.py'))
    sp = importlib.util.spec_from_loader('dlg_uut10', ld); dlg = importlib.util.module_from_spec(sp); sp.loader.exec_module(dlg)
    d, board = _scenario()
    final = os.path.join(d, 'final.kicad_pcb')
    open(final, 'w').write('(kicad_pcb final)')
    dlg_obj = dlg.RouterDialog.__new__(dlg.RouterDialog)
    dlg_obj._log_ui = lambda m: None
    dest = dlg_obj._deliver_local({'final_board': final}, board)
    assert dest and os.path.exists(dest), dest
    stem = os.path.splitext(dest)[0]
    assert open(stem + '.kicad_pro').read() == 'PRO-CONTENT'
    assert open(stem + '.kicad_dru').read() == 'DRU-CONTENT'


def test_dialog_delivery():
    _dialog_part()


if __name__ == '__main__':
    test_helper_scenario(); test_helper_board_stem_wins(); test_helper_none()
    test_submit_carries_sidecars(); test_pipeline_init_copies_sidecars()
    _dialog_part()
    print('sidecar resolution OK')
