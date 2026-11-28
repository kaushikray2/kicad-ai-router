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

"""Pre-Exam Polish (plan.md Section C), RepeatGuard exit tests:
stuck LLM tool loops must stop EXECUTING the stuck call within
<= 6 dispatches (repeat tier: REPEAT_LIMIT = 2 executions of an
identical failing/no-progress call; streak tier: STREAK_LIMIT = 4
executions against one target however the parameters dither),
the refusal must carry an explicit strategy-switch hint, and
progress resets only the progressing call's own counters. Run:
python3 tests/test_repeat_guard.py"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import orchestrator
from orchestrator import RepeatGuard, REPEAT_LIMIT, STREAK_LIMIT


class _ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.tool_payloads = []

    def chat(self, messages, tools):
        for m in messages:
            if m.get('role') == 'tool':
                self.tool_payloads.append(m['content'])
        if self.script:
            call = self.script.pop(0)
        else:
            call = {'name': 'done', 'args': {'note': 'end'}, 'id': 'd'}
        return {'content': '', 'raw': {}, 'tool_calls': [call]}


class _StubPipe:
    board_path = 'x.kicad_pcb'

    def __init__(self):
        self.executions = []
        self.gate_seq = 0

    def tool_finish_leg(self, net):
        self.executions.append(('finish_leg', net))
        raise RuntimeError('cannot close %s' % net)

    def tool_long_finish(self, net):
        self.executions.append(('long_finish', net))
        return {'ok': True, 'net': net}

    def tool_gate(self, board=None):
        self.gate_seq += 1
        self.executions.append(('gate', self.gate_seq))
        return {'unconnected_total': self.gate_seq}  # new info every call

    def finalize(self):
        return 'board'


def _run(script):
    client = _ScriptedClient(script)
    pipe = _StubPipe()
    saved = orchestrator.LLMClient
    orchestrator.LLMClient = lambda *a: client
    try:
        cfg = {'endpoint': 'http://unused', 'model': 'm', 'max_tool_calls': 48}
        orchestrator.run_llm(cfg, pipe, lambda m: None)
    finally:
        orchestrator.LLMClient = saved
    return pipe, client


def _call(name, args):
    return {'name': name, 'args': args, 'id': 'x'}


def test_limits_are_the_plans():
    assert REPEAT_LIMIT == 2 and STREAK_LIMIT == 4


def test_identical_failing_loop_stops_executing():
    pipe, client = _run([_call('finish_leg', {'net': 'X'})] * 10)
    ran = [e for e in pipe.executions if e[0] == 'finish_leg']
    assert len(ran) == REPEAT_LIMIT, ran  # 2 <= 6: the plan's exit bound
    refusals = [p for p in client.tool_payloads if 'RepeatGuard refused' in p]
    assert refusals, client.tool_payloads
    assert 'strategy_switch_hint' in refusals[0]
    assert json.loads(refusals[0])['repeat_guard']['tier'] == 'repeat'


def test_dithered_same_target_loop_stops_executing():
    script = [_call('finish_leg', {'net': 'X', 'work': 100 + i}) for i in range(10)]
    pipe, client = _run(script)
    ran = [e for e in pipe.executions if e[0] == 'finish_leg']
    assert len(ran) == STREAK_LIMIT, ran  # 4 <= 6: the plan's exit bound
    refusals = [p for p in client.tool_payloads if 'RepeatGuard refused' in p]
    assert refusals
    assert json.loads(refusals[0])['repeat_guard']['tier'] == 'streak'


def test_progress_resets_only_its_own_counters():
    # #239 (6c): an unrelated successful call no longer resets
    # the guard (the pre-fix test_progress_resets_both_tiers
    # pinned the global clear: a gate call re-armed a stuck
    # finish_leg loop, and alternating one cheap success with
    # failing calls was never refused).
    script = ([_call('finish_leg', {'net': 'X'})] * 2      # repeat count reaches 2
              + [_call('gate', {})]                        # unrelated progress: NO reset
              + [_call('finish_leg', {'net': 'X'})] * 3)   # refused at once
    pipe, client = _run(script)
    ran = [e for e in pipe.executions if e[0] == 'finish_leg']
    assert len(ran) == REPEAT_LIMIT, ran
    assert any('RepeatGuard refused' in p for p in client.tool_payloads)


def test_status_dict_failures_count():
    # #239 (6a): the pipeline tools report failure as status
    # dicts with no 'error' key -- those payloads must arm the
    # guard exactly like error payloads do.
    for payload in ({'status': 'reverted_faults'},
                    {'status': 'failed'}, {'status': 'no_board'},
                    {'status': 'no_path'}, {'status': 'reverted'},
                    {'status': 'done', 'routed': []}):
        g = RepeatGuard()
        for _ in range(REPEAT_LIMIT):
            g.record('route', {'first_nets': ['A']}, json.dumps(payload))
        assert g.check('route', {'first_nets': ['A']}) is not None, payload
    # a route that actually routed something is progress
    g = RepeatGuard()
    for _ in range(REPEAT_LIMIT):
        g.record('route', {'first_nets': ['A']},
                 json.dumps({'status': 'done', 'routed': ['A']}))
    assert g.check('route', {'first_nets': ['A']}) is None


def test_streak_is_per_tool():
    # #239 (6b): the streak key carries the tool name, so the
    # refusal hint's own advice -- switch tools on the same
    # target -- is followable instead of instantly refused.
    g = RepeatGuard()
    for i in range(STREAK_LIMIT):
        g.record('finish_leg', {'net': 'X', 'work': i},
                 json.dumps({'status': 'failed'}))
    assert g.check('finish_leg', {'net': 'X', 'work': 99}) is not None
    assert g.check('long_finish', {'net': 'X'}) is None
    assert g.check('ilp_repair', {'net': 'X'}) is None


def test_same_call_progress_rearms_and_others_do_not():
    # #239 (6c) at guard level: progress on the SAME call pops
    # its counters; progress anywhere else leaves them armed.
    g = RepeatGuard()
    for i in range(STREAK_LIMIT - 1):
        g.record('finish_leg', {'net': 'X', 'work': i},
                 json.dumps({'status': 'failed'}))
    g.record('gate', {}, json.dumps({'unconnected_total': 3}))  # unrelated
    g.record('finish_leg', {'net': 'X', 'work': 99},
             json.dumps({'status': 'failed'}))
    assert g.check('finish_leg', {'net': 'X', 'work': 100}) is not None
    g2 = RepeatGuard()
    for i in range(STREAK_LIMIT):
        g2.record('finish_leg', {'net': 'X', 'work': i},
                  json.dumps({'status': 'failed'}))
    assert g2.check('finish_leg', {'net': 'X', 'work': 42}) is not None
    g2.record('finish_leg', {'net': 'X', 'work': 42},
              json.dumps({'status': 'done', 'routed': ['X']}))
    assert g2.check('finish_leg', {'net': 'X', 'work': 43}) is None


def test_refusal_does_not_wedge_other_tools():
    script = ([_call('finish_leg', {'net': 'X'})] * 3      # third is refused
              + [_call('long_finish', {'net': 'Y'})])      # a strategy switch still runs
    pipe, client = _run(script)
    assert ('long_finish', 'Y') in pipe.executions, pipe.executions


def test_changing_successful_calls_never_refused():
    pipe, client = _run([_call('gate', {})] * 6)
    assert not any('RepeatGuard refused' in p for p in client.tool_payloads)


def test_guard_canonicalises_args_and_net_lists():
    g = RepeatGuard()
    for _ in range(REPEAT_LIMIT):
        g.record('finish_leg', {'net': 'X', 'b': 1, 'a': 2},
                 json.dumps({'error': 'boom'}))
    assert g.check('finish_leg', {'a': 2, 'b': 1, 'net': 'X'}) is not None
    g2 = RepeatGuard()
    for i in range(STREAK_LIMIT):
        g2.record('ilp_repair', {'nets': ['B', 'A'], 'work': i},
                  json.dumps({'error': 'boom'}))
    assert g2.check('ilp_repair', {'nets': ['A', 'B'], 'work': 99}) is not None


if __name__ == '__main__':
    test_limits_are_the_plans()
    test_identical_failing_loop_stops_executing()
    test_dithered_same_target_loop_stops_executing()
    test_progress_resets_only_its_own_counters()
    test_status_dict_failures_count()
    test_streak_is_per_tool()
    test_same_call_progress_rearms_and_others_do_not()
    test_refusal_does_not_wedge_other_tools()
    test_changing_successful_calls_never_refused()
    test_guard_canonicalises_args_and_net_lists()
    print('repeat guard OK')
