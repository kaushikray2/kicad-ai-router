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

"""#214 (option (b), owner ruling 2026-11-27): LLM mode used to
apply the engineered SETUP at Pipeline construction (netclasses,
domains) but never run the deterministic stages or produce the
report -- a marked board came back half-engineered with no
indication.  run_llm now runs the AUTO epilogue after the tool
loop (fix_violations -> engineered stages -> finalize -> gate)
and attaches engineered_report + placement_audit exactly as
AUTO does; board_summary surfaces engineered_first_nets."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import orchestrator


class _StubClient:
    def __init__(self, *a, **kw):
        pass

    def chat(self, messages, tools):
        return {'content': '', 'raw': {},
                'tool_calls': [{'name': 'done',
                                'args': {'note': 'stub done'},
                                'id': '1'}]}


class _FakePipe:
    def __init__(self, marked):
        self.board_path = '/tmp/stub.kicad_pcb'
        self.cfg = {'engineered_nets': True}
        self.workdir = '/tmp'
        self.calls = []
        self.ledger = []          # mirrors Pipeline._record
        self._audit = {'footprints': {}} if marked else None
        self._marked = marked

    def _rec(self, name):
        self.calls.append(name)
        self.ledger.append(name)

    def tool_fix_violations(self):
        self._rec('fix_violations')
        return {}

    def tool_rf_treatments(self):
        self._rec('rf_treatments')

    def tool_smooth_marked(self):
        self._rec('smooth_marked')

    def tool_power(self):
        self._rec('power')

    def tool_length_match(self):
        self._rec('length_match')

    def tool_hv_domains(self):
        self._rec('hv_domains')

    def tool_quiet_nets(self):
        self._rec('quiet_nets')

    def finalize(self):
        self._rec('finalize')
        return '/tmp/final.kicad_pcb'

    def tool_gate(self, board=None):
        self._rec('gate')
        return {'unconnected_total': 0}

    def engineered_report(self, final):
        self._rec('engineered_report')
        return {'nets': {'RF1': {}}} if self._marked else None


def _run(monkeypatch, marked):
    monkeypatch.setattr(orchestrator, 'LLMClient', _StubClient)
    pipe = _FakePipe(marked)
    cfg = {'endpoint': 'http://stub', 'model': 'stub'}
    return orchestrator.run_llm(cfg, pipe, lambda m: None), pipe


def test_llm_epilogue_runs_engineered_stages(monkeypatch):
    res, pipe = _run(monkeypatch, marked=True)
    order = pipe.calls
    # epilogue order: fix_violations BEFORE the engineered
    # stages (#210), stages before finalize, gate on the final
    assert order.index('fix_violations') < order.index('rf_treatments')
    for stage in ('rf_treatments', 'power', 'length_match',
                  'hv_domains', 'quiet_nets'):
        assert stage in pipe.ledger, order
    assert order.index('quiet_nets') < order.index('finalize')
    assert order.index('finalize') < order.index('gate')
    assert res['engineered_nets'] == {'nets': {'RF1': {}}}
    assert res['placement_audit'] == {'footprints': {}}
    assert res['final_board'] == '/tmp/final.kicad_pcb'


def test_llm_unmarked_result_shape_unchanged(monkeypatch):
    res, pipe = _run(monkeypatch, marked=False)
    assert 'engineered_nets' not in res
    assert 'placement_audit' not in res
    assert set(res) == {'final_board', 'gate', 'route', 'ledger_path'}


def test_board_summary_surfaces_engineered_first_nets(tmp_path):
    import pcbnew
    V = pcbnew.VECTOR2I
    NM = 1000000
    b = pcbnew.BOARD()
    n = pcbnew.NETINFO_ITEM(b, 'RF1')
    b.Add(n)
    f = pcbnew.FOOTPRINT(b)
    f.SetReference('U1')
    for (num, x) in [('1', 10), ('2', 30)]:
        p = pcbnew.PAD(f)
        p.SetNumber(num)
        p.SetPosition(V(x * NM, 10 * NM))
        p.SetSize(V(NM, NM))
        p.SetShape(pcbnew.PAD_SHAPE_RECT)
        p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu)
        p.SetLayerSet(ls)
        p.SetNet(n)
        f.Add(p)
    b.Add(f)
    path = str(tmp_path / 's.kicad_pcb')
    with open(path.replace('.kicad_pcb', '.kicad_pro'), 'w') as fh:
        fh.write('{}\n')
    pcbnew.SaveBoard(path, b)
    from ai_router.pipeline import Pipeline
    cfg = {'strip_existing': False, 'tracemaker_path': '/nonexistent'}
    pipe = Pipeline(cfg, path, lambda m: None)
    assert 'engineered_first_nets' not in pipe.tool_board_summary()
    with open(path.replace('.kicad_pcb', '.ai-router-markings.json'), 'w') as fh:
        json.dump({'version': 1, 'impedance': [
            {'net': 'RF1', 'z0_ohm': 50}]}, fh)
    pipe2 = Pipeline(cfg, path, lambda m: None)
    summ = pipe2.tool_board_summary()
    assert summ['engineered_first_nets'] == ['RF1'], summ


if __name__ == '__main__':
    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)
    test_llm_epilogue_runs_engineered_stages(_MP())
    test_llm_unmarked_result_shape_unchanged(_MP())
    print('llm engineered epilogue OK')
