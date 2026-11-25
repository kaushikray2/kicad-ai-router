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
