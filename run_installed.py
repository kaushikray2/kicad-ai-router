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

import sys, os, json
sys.path.insert(0, os.path.expanduser('~/.local/share/kicad/10.0/scripting/plugins'))
from ai_router.config import load
from ai_router.pipeline import Pipeline
from ai_router import orchestrator
board = sys.argv[1]
cfg = load(); cfg['mode'] = 'llm'
def log(m):
    print('[air] ' + str(m), flush=True)
pipe = Pipeline(cfg, board, log)
res = orchestrator.run_llm(cfg, pipe, log)
print('RESULT ' + json.dumps(res, default=str)[:1000], flush=True)
