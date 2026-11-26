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

import os
import pcbnew
import wx
try:
    from .dialog import RouterDialog
except ImportError:
    from dialog import RouterDialog

class AIRouterPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = 'AI Router (LLM orchestrator)'
        self.category = 'Routing'
        self.description = ('Routes the board with TraceMaker + scripted finishers, '
                            'orchestrated by an LLM at any OpenAI-compatible endpoint '
                            '(e.g. a local llama.cpp server). The model makes strategy '
                            'decisions only; deterministic tools do all geometry.')
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(os.path.dirname(__file__), 'icon.png')

    def Run(self):
        board = pcbnew.GetBoard()
        path = board.GetFileName() if board is not None else ''
        parent = wx.FindWindowByName('PcbEditor') or None
        dlg = RouterDialog(parent, path)
        dlg.ShowModal()
        dlg.Destroy()
