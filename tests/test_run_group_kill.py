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

"""Issue #125: _run_group's timeout kill was POSIX-only. On
Windows os.killpg/os.getpgid do not exist, the except-pass hid
that, and the fallback communicate() had no timeout -- a hung
engine blocked the run forever. The kill is now a helper with a
Windows branch (taskkill /T on the process group created by
start_new_session, p.kill() as last resort); POSIX behaviour is
unchanged. Run: python3 tests/test_run_group_kill.py"""
import os, subprocess, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import worker as w

assert hasattr(w, '_kill_group'), '_kill_group helper missing (pre-fix tree)'

# POSIX: a timed-out sleeper is killed promptly, not waited out
t0 = time.time()
try:
    w._run_group([sys.executable, '-c', 'import time; time.sleep(60)'], 1)
    raise SystemExit('sleeper did not time out')
except subprocess.TimeoutExpired:
    pass
assert time.time() - t0 < 20, 'timeout kill took %.1fs -- the tree was not killed' % (time.time() - t0)

# Windows branch: with os.name patched for the helper call only,
# taskkill is absent on this Linux box, so the p.kill() last
# resort must fire and the helper must not raise.
class FakeP:
    pid = 424242; killed = False
    def kill(self): self.killed = True
fp = FakeP()
real_name = os.name
try:
    os.name = 'nt'
    w._kill_group(fp)
finally:
    os.name = real_name
assert fp.killed, 'Windows fallback never reached p.kill()'
print('PASS test_run_group_kill')
