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
