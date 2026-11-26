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

"""xvfb smoke for #210 (b): after a backend run, the job's ledger
is persisted beside the downloaded result and the run result
carries ledger_path -- what _show_verdict stores as _last_ledger
and the Report dialog's 'Open ledger.json' button consumes.
The backend client is faked at the module boundary; everything
else (dialog, naming, file write) is the real code.  Standalone:

    xvfb-run -a python3 tests/test_dialog_backend_ledger_smoke.py
"""
import json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wx
from ai_router import backend_client
from ai_router import dialog as dlgmod

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'eng_gui_fixture')
LEDGER = [{'stage': 'route', 'note': 'fake winning pipeline'}]


def main():
    app = wx.App(False)
    d = tempfile.mkdtemp(prefix='ledger-smoke-')
    for f in ('fixture.kicad_pcb', 'fixture.kicad_pro'):
        shutil.copy(os.path.join(FIX, f), d)
    board = os.path.join(d, 'fixture.kicad_pcb')
    dlg = dlgmod.RouterDialog(None, board)
    saved = {}
    try:
        def fake_download(url, tok, jid, dest):
            with open(dest, 'w') as fh:
                fh.write('(kicad_pcb)')
        backend_client.health = lambda url: {'backend_version': 'test',
                                             'kicad_cli': '10.0.8',
                                             'tracemaker_selected': 'fake'}
        backend_client.submit = lambda url, tok, b, ov: 'job-1'
        backend_client.get_job = lambda url, tok, jid: {
            'status': 'done', 'gate': {'unconnected_total': 0},
            'ledger': LEDGER}
        backend_client.download_result = fake_download
        backend_client.download_sidecar = lambda *a: False
        out = dlg._run_backend({'backend_url': 'http://127.0.0.1:9',
                                'backend_token': ''}, board)
        lp = out.get('ledger_path')
        assert lp and os.path.exists(lp), out
        with open(lp) as fh:
            assert json.load(fh) == LEDGER
        assert os.path.dirname(lp) == os.path.dirname(out['final_board'])
        print('BACKEND LEDGER SMOKE PASS')
    finally:
        dlg.Destroy()


if __name__ == '__main__':
    main()
