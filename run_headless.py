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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_router'))
from config import load
from pipeline import Pipeline
import orchestrator
board = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else 'auto'
cfg = load(); cfg['mode'] = mode
def log(m):
    print('[air] ' + str(m), flush=True)
pipe = Pipeline(cfg, board, log)
res = orchestrator.run_auto(cfg, pipe, log) if mode == 'auto' else orchestrator.run_llm(cfg, pipe, log)
print('RESULT ' + json.dumps(res, default=str)[:1000], flush=True)
