"""#138 (B13) regression: the engineered_nets switch gates every
engineered treatment stage, not just _engineered_setup."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import orchestrator

STAGES = ['tool_rf_treatments', 'tool_smooth_marked', 'tool_power',
          'tool_length_match', 'tool_hv_domains', 'tool_quiet_nets']


class _Pipe:
    def __init__(self, cfg):
        self.cfg = cfg
        self.calls = []


def _mkpipe(cfg):
    p = _Pipe(cfg)
    for name in STAGES:
        setattr(p, name, (lambda n: lambda: p.calls.append(n))(name))
    return p


def test_switch_off_runs_no_stage():
    p = _mkpipe({'engineered_nets': False})
    logs = []
    orchestrator._run_engineered_stages(p, logs.append)
    assert p.calls == [], p.calls
    assert any('engineered_nets is off' in m for m in logs)


def test_switch_on_runs_all_stages_in_order():
    p = _mkpipe({'engineered_nets': True})
    orchestrator._run_engineered_stages(p, lambda m: None)
    assert p.calls == STAGES, p.calls


def test_switch_absent_defaults_on():
    p = _mkpipe({})
    orchestrator._run_engineered_stages(p, lambda m: None)
    assert p.calls == STAGES, p.calls


class _DoneClient:
    def __init__(self, *a):
        pass

    def chat(self, messages, tools):
        return {'content': '', 'raw': {},
                'tool_calls': [{'name': 'done', 'args': {'note': 'ok'},
                                'id': '1'}]}


class _StubPipe:
    board_path = 'x.kicad_pcb'
    cfg = {}

    def finalize(self):
        return 'board'

    def tool_gate(self, board=None):
        return {}


def test_llm_mode_survives_non_numeric_max_tool_calls(monkeypatch):
    monkeypatch.setattr(orchestrator, 'LLMClient', _DoneClient)
    cfg = {'endpoint': 'http://unused', 'model': 'm',
           'max_tool_calls': 'not-a-number'}
    out = orchestrator.run_llm(cfg, _StubPipe(), lambda m: None)
    assert out['final_board'] == 'board'
    assert orchestrator._cfg_int({'max_tool_calls': '7'},
                                 'max_tool_calls', 48) == 7
    assert orchestrator._cfg_int({}, 'max_tool_calls', 48) == 48
