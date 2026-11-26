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

"""#203 regression: on LOCAL runs the placement audit's apply side
(materialised #42 keepouts + first_nets contribution) follows the
engineered_nets switch.  Before the fix, sidecar presence alone
set apply, so unchecking "Enable engineered nets" still stamped
keepouts locally while a backend run (upload gated on the switch)
did not.  The audit REPORT itself is switch-independent by design
(#149 D14); the explicit placement_audit == 'apply' opt-in is too.

Needs pcbnew (the worker subprocess); skips where unavailable."""
import os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

FIX = os.path.join(os.path.dirname(__file__), 'eng_gui_fixture')


def _pcbnew_available():
    try:
        r = subprocess.run([sys.executable, '-c', 'import pcbnew'],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _fake_engine(tmp):
    path = os.path.join(tmp, 'fake-tm')
    with open(path, 'w') as f:
        f.write('#!/bin/sh\n'
                'inp="$2"\n'
                'while [ $# -gt 0 ]; do if [ "$1" = "-o" ]; then out="$2"; fi; shift; done\n'
                'cp "$inp" "$out"\n'
                'echo "routed 1/1 connections"\n')
    os.chmod(path, 0o755)
    return path


def _fixture_board(tmp):
    for name in ('fixture.kicad_pcb', 'fixture.kicad_pro',
                 'fixture.ai-router-markings.json'):
        shutil.copy(os.path.join(FIX, name), os.path.join(tmp, name))
    return os.path.join(tmp, 'fixture.kicad_pcb')


def _pipe(tmp, cfg):
    from ai_router.pipeline import Pipeline
    cfg = dict(cfg)
    cfg['tracemaker_path'] = _fake_engine(tmp)
    return Pipeline(cfg, _fixture_board(tmp), lambda m: None)


def test_switch_off_sidecar_present_does_not_apply():
    if not _pcbnew_available():
        print('skip: pcbnew unavailable')
        return
    with tempfile.TemporaryDirectory() as tmp:
        pipe = _pipe(tmp, {'engineered_nets': False})
        assert pipe._audit is not None and pipe._audit.get('status') == 'ok', pipe._audit
        assert pipe._audit_apply is False
        assert pipe._audit['applied'] is False
        assert pipe._audit['materialised'] == []
        extra = set(pipe._audit.get('first_nets_extra') or [])
        assert not (extra & set(pipe._engineered_first_nets()))


def test_switch_on_sidecar_present_applies_as_before():
    if not _pcbnew_available():
        print('skip: pcbnew unavailable')
        return
    with tempfile.TemporaryDirectory() as tmp:
        pipe = _pipe(tmp, {})
        assert pipe._audit_apply is True
        assert pipe._audit['applied'] is True


def test_explicit_apply_opt_in_is_switch_independent():
    if not _pcbnew_available():
        print('skip: pcbnew unavailable')
        return
    with tempfile.TemporaryDirectory() as tmp:
        pipe = _pipe(tmp, {'engineered_nets': False,
                           'placement_audit': 'apply'})
        assert pipe._audit_apply is True
