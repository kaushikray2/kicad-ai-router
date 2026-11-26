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

"""Issue #179: run_auto kept Variant B whenever B's gate had no
split SIGNAL nets, so a B run whose only splits were POUR nets
(Stage C sample 2: split_pour [GND], split_signal []) never
escalated to Variant A -- the different layer law that might have
closed the pour island never ran. The escalation condition now
covers split pour nets too; A is still kept only when the final
key prefers it.
Run: python3 tests/test_escalation_pour_splits.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import orchestrator as orc

def test_escalation_condition():
    needs = orc._b_needs_escalation
    # the sample-2 shape: pour-only split must escalate
    assert needs({'split_signal_nets': [], 'split_pour_nets': ['GND']}) is True
    # the original trigger still fires
    assert needs({'split_signal_nets': ['SPI_MOSI'], 'split_pour_nets': []}) is True
    assert needs({'split_signal_nets': ['A'], 'split_pour_nets': ['GND']}) is True
    # nothing split: B is kept, no wasted A run
    assert needs({'split_signal_nets': [], 'split_pour_nets': []}) is False
    assert needs({}) is False
    assert needs(None) is False

if __name__ == '__main__':
    test_escalation_condition()
    print('escalation pour splits OK')
