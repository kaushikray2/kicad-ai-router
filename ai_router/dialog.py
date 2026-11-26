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

"""wx dialog for the AI Router plugin: configuration + run + live log.

v1.12 (#168): the dialog is built from the open board, not from
free text. Layer bans and costs are per-layer controls generated
from the board's stackup (a layer name can no longer be mistyped
and a cost spec can no longer be malformed); board-derived smart
defaults are computed at open and, when the saved config differs
on mode / layer law, a one-line notice offers them instead of
silently inheriting a stale experiment; a status lamp band
(colour + word + detail) reports IDLE / RUNNING / DONE / FAILED /
CANCELLED; and the config keys the dialog used to hide live in an
Advanced collapsible. Result boards are named by naming.py (#167).
Config keys and the strings the pipeline consumes are unchanged,
so existing config.json files load losslessly.
"""
import os, shutil, subprocess, threading, time
import wx
try:
    from . import config as cfgmod
    from .pipeline import Pipeline
    from . import orchestrator
    from . import backend_client
    from . import naming
    from . import ui_model
    from . import board_facts
except ImportError:
    import config as cfgmod
    from pipeline import Pipeline
    import orchestrator
    import backend_client
    import naming
    import ui_model
    import board_facts

class Cancelled(Exception):
    pass

BACKEND_PRESETS = ['http://192.168.1.192:8731',   # ML350
                   'http://192.168.22.209:8732',  # zorinbox-1
                   'http://127.0.0.1:8731']       # this machine
ENDPOINT_PRESETS = ['http://127.0.0.1:8080/v1', 'http://192.168.1.192:8080/v1']
MODEL_PRESETS = ['qwen3-coder-30b', 'qwen3.8-27b-liberated']
PORTFOLIO_PRESETS = ['', 'tm_relax', 'tm_relax,tm_global',
                     'tm_relax,tm_global,tm_escape', 'tm_relax,freerouting,krt']
COST_LABELS = ['Off'] + [ui_model.format_factor(f) for f in ui_model.COST_PRESETS]

# The lamp never carries meaning by colour alone: every state also
# shows its word and a detail line (WCAG 1.4.1; red/green is the
# colour-vision-deficiency confusion pair).
LAMP_COLOURS = {'IDLE': wx.Colour(0, 176, 80), 'RUNNING': wx.Colour(220, 0, 0),
                'DONE': wx.Colour(0, 176, 80), 'FAILED': wx.Colour(255, 102, 0),
                'CANCELLED': wx.Colour(140, 140, 140)}

class AddServerDialog(wx.Dialog):
    """#199: add a custom backend server (name / URL / token) for the
    Run on group. Validation is ui_model.validate_new_server's; this
    dialog only renders the reason inline and refuses to close on
    error. On success .entry holds the validated server dict."""
    def __init__(self, parent, preset_urls, servers):
        super().__init__(parent, title='Add backend server', style=wx.DEFAULT_DIALOG_STYLE)
        self._preset_urls = preset_urls
        self._servers = servers
        self.entry = None
        panel = wx.Panel(self)
        self.panel = panel
        vbox = wx.BoxSizer(wx.VERTICAL)
        g = wx.FlexGridSizer(0, 2, 6, 8); g.AddGrowableCol(1, 1)
        def add(label, ctrl):
            g.Add(wx.StaticText(panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            g.Add(ctrl, 1, wx.EXPAND)
        self.name_ctrl = wx.TextCtrl(panel, value='')
        add('Name', self.name_ctrl)
        self.url_ctrl = wx.TextCtrl(panel, value='')
        self.url_ctrl.SetToolTip('The backend base URL, e.g. http://192.168.1.50:8731')
        add('Backend URL', self.url_ctrl)
        self.tok_ctrl = wx.TextCtrl(panel, value='', style=wx.TE_PASSWORD)
        self.tok_ctrl.SetToolTip('Optional. The backend’s auth token (its config.json, key auth_token). Stored with this server in your plugin config.')
        add('Backend token', self.tok_ctrl)
        vbox.Add(g, 0, wx.EXPAND | wx.ALL, 10)
        self.err_label = wx.StaticText(panel, label='')
        self.err_label.SetForegroundColour(wx.Colour(220, 0, 0))
        vbox.Add(self.err_label, 0, wx.LEFT | wx.RIGHT, 10)
        brow = wx.BoxSizer(wx.HORIZONTAL)
        self.ok_btn = wx.Button(panel, label='Add server')
        self.ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        brow.Add(self.ok_btn, 0)
        cxl = wx.Button(panel, label='Cancel')
        cxl.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        brow.Add(cxl, 0, wx.LEFT, 8)
        vbox.Add(brow, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        panel.SetSizer(vbox)
        self.SetMinSize((430, -1))
        vbox.Fit(self)
        self.name_ctrl.SetFocus()

    def _on_ok(self, _evt):
        entry, err = ui_model.validate_new_server(self.name_ctrl.GetValue(),
                                                  self.url_ctrl.GetValue(),
                                                  self.tok_ctrl.GetValue(),
                                                  self._preset_urls, self._servers)
        if err:
            self.err_label.SetLabel(err)
            self.err_label.Wrap(400)
            self.panel.GetSizer().Layout()
            self.panel.GetSizer().Fit(self)
            return
        self.entry = entry
        self.EndModal(wx.ID_OK)


class RouterDialog(wx.Dialog):
    def __init__(self, parent, board_path):
        super().__init__(parent, title='AI Router — LLM-orchestrated routing', size=(800, 600), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.cfg = cfgmod.load()
        self.custom_servers = ui_model.sanitize_servers(self.cfg.get('backend_servers'))
        self._combo_entries = []
        self.cancel_event = threading.Event()
        self.worker_thread = None
        self.result = None
        self._running = False
        self._lamp_state = 'IDLE'
        self._config_controls = []
        self.facts = None
        self.classif = {}
        self.aliases = {}
        self.layer_rows = {}
        self.extra_bans = []
        self.extra_costs = {}
        self.cost_spec_error = None
        # Layout (#169): the status band, the action buttons and the
        # log live on the outer panel, always visible. The settings
        # stack (board / strategy / layers / run-on / advanced) lives
        # in a scrolled panel -- at natural size it is ~950 px tall,
        # more than a 768-line laptop display, so it must scroll
        # instead of clipping its lower groups off-screen.
        outer = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        self._outer_sizer = vbox
        vbox.Add(self._build_status_band(outer), 0, wx.EXPAND | wx.ALL, 8)
        self.scroll = wx.ScrolledWindow(outer, style=wx.VSCROLL)
        self.scroll.SetScrollRate(0, 10)
        self.scroll.SetMinSize((-1, 160))
        panel = wx.Panel(self.scroll)
        self.panel = panel
        settings_box = wx.BoxSizer(wx.VERTICAL)
        settings_box.Add(self._build_board_group(panel, board_path or ''), 0, wx.EXPAND)
        settings_box.Add(self._build_strategy_group(panel), 0, wx.EXPAND | wx.TOP, 8)
        self.notice_label = wx.StaticText(panel, label='')
        self.use_rec_btn = wx.Button(panel, label='Use recommended')
        self.use_rec_btn.Hide(); self.notice_label.Hide()
        nb = wx.BoxSizer(wx.HORIZONTAL)
        nb.Add(self.notice_label, 1, wx.ALIGN_CENTER_VERTICAL)
        nb.Add(self.use_rec_btn, 0, wx.LEFT, 8)
        settings_box.Add(nb, 0, wx.EXPAND | wx.TOP, 8)
        settings_box.Add(self._build_layers_group(panel), 0, wx.EXPAND | wx.TOP, 8)
        settings_box.Add(self._build_runon_group(panel), 0, wx.EXPAND | wx.TOP, 8)
        settings_box.Add(self._build_advanced(panel), 0, wx.EXPAND | wx.TOP, 8)
        panel.SetSizer(settings_box)
        scroll_box = wx.BoxSizer(wx.VERTICAL)
        scroll_box.Add(panel, 1, wx.EXPAND)
        self.scroll.SetSizer(scroll_box)
        vbox.Add(self.scroll, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.run_btn = wx.Button(outer, label='Run routing pipeline')
        self.cancel_btn = wx.Button(outer, label='Cancel')
        self.cancel_btn.Disable()
        self.open_btn = wx.Button(outer, label='Open result board')
        self.open_btn.Disable()
        btns.Add(self.run_btn, 0, wx.RIGHT, 8)
        btns.Add(self.cancel_btn, 0, wx.RIGHT, 8)
        btns.Add(self.open_btn, 0)
        vbox.Add(btns, 0, wx.LEFT | wx.TOP, 10)
        self.log_ctrl = wx.TextCtrl(outer, style=wx.TE_MULTILINE | wx.TE_READONLY)
        self.log_ctrl.SetMinSize((-1, 90))
        vbox.Add(self.log_ctrl, 1, wx.EXPAND | wx.ALL, 10)
        outer.SetSizer(vbox)
        self.run_btn.Bind(wx.EVT_BUTTON, self.on_run)
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.open_btn.Bind(wx.EVT_BUTTON, self.on_open)
        self.use_rec_btn.Bind(wx.EVT_BUTTON, self._on_use_recommended)
        self._load_board_facts(board_path or '')
        self._populate_all()
        self.set_status('IDLE', 'Ready — no run yet')
        self._relayout()
        self._fit_to_display()

    # ---------------------------------------------------------- layout
    def _relayout(self):
        # Content changed size (layer rows rebuilt, Advanced pane
        # expanded, ...): relayout the settings stack and re-fit the
        # scroll region's virtual size so new content stays reachable.
        sizer = self.panel.GetSizer()
        if sizer is not None:
            sizer.Layout()
        if getattr(self, 'scroll', None) is not None:
            self.scroll.FitInside()

    def _fit_to_display(self):
        # Open at the natural size, but never larger than the
        # display's usable client area (#169: the old fixed opening
        # overflowed 768-line displays and clipped the dialog's
        # bottom with no way to reach it). The cap is a ceiling --
        # on a large display the dialog opens at its natural size.
        ca = wx.GetClientDisplayRect()
        # Natural size: the outer sizer's min counts the scroll
        # region at its floor height, so swap in the settings
        # stack's real natural height (and width, plus room for
        # the scroll region's padding and scrollbar).
        fixed = self._outer_sizer.CalcMin()
        content = self.panel.GetSizer().CalcMin()
        want_w = max(fixed.x, content.x + 34)
        want_h = fixed.y - self.scroll.GetMinSize().y + content.y
        w, h, min_w, min_h = ui_model.capped_open_size(
            want_w, want_h, ca.width, ca.height)
        self.SetMinSize((min_w, min_h))
        self.SetSize(wx.Size(w, h))
        self.Centre(wx.BOTH)

    # ---------------------------------------------------------- status lamp
    def _build_status_band(self, panel):
        band = wx.BoxSizer(wx.HORIZONTAL)
        self.lamp_dot = wx.StaticText(panel, label='●')
        f = self.lamp_dot.GetFont(); f.SetPointSize(20); f.SetWeight(wx.FONTWEIGHT_BOLD)
        self.lamp_dot.SetFont(f)
        self.lamp_state_ctrl = wx.StaticText(panel, label='IDLE')
        f2 = self.lamp_state_ctrl.GetFont(); f2.SetPointSize(14); f2.SetWeight(wx.FONTWEIGHT_BOLD)
        self.lamp_state_ctrl.SetFont(f2)
        self.lamp_detail = wx.StaticText(panel, label='')
        band.Add(self.lamp_dot, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        band.Add(self.lamp_state_ctrl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        band.Add(self.lamp_detail, 1, wx.ALIGN_CENTER_VERTICAL)
        return band

    def set_status(self, state, detail=''):
        # UI thread only; worker paths go through _set_status.
        self._lamp_state = state
        self.lamp_dot.SetForegroundColour(LAMP_COLOURS[state])
        self.lamp_state_ctrl.SetLabel(state)
        self.lamp_detail.SetLabel(detail)

    def _set_status(self, state, detail=''):
        wx.CallAfter(self.set_status, state, detail)

    def _on_settings_changed(self, _evt=None):
        # After a finished run, touching a consequential setting
        # returns the lamp to IDLE (the DONE no longer describes
        # what a Run would do now).
        if not self._running and self._lamp_state in ('DONE', 'FAILED', 'CANCELLED'):
            self.set_status('IDLE', 'Ready')
        # (#183): keep the stale-settings notice in step with the
        # controls -- a manual fix must clear it without a reopen.
        # v1.12.2 (#176): the mode radios land here too -- keep the
        # Reproducible checkbox's enabled state in step with the mode.
        try:
            self._refresh_notice()
            self._apply_det_state()
        except Exception: pass

    # ---------------------------------------------------------- Board group
    def _build_board_group(self, panel, board_path):
        box = wx.StaticBoxSizer(wx.StaticBox(panel, label='Board'), wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.board_ctrl = wx.TextCtrl(panel, value=board_path, style=wx.TE_READONLY)
        self.board_ctrl.SetToolTip('The board to route. Opens on the board currently loaded in the PCB Editor.')
        self.change_btn = wx.Button(panel, label='Change…')
        self.change_btn.Bind(wx.EVT_BUTTON, self.on_change_board)
        row.Add(self.board_ctrl, 1, wx.ALIGN_CENTER_VERTICAL)
        row.Add(self.change_btn, 0, wx.LEFT, 8)
        box.Add(row, 0, wx.EXPAND | wx.ALL, 6)
        self.facts_label = wx.StaticText(panel, label='')
        box.Add(self.facts_label, 0, wx.LEFT | wx.BOTTOM, 6)
        self._config_controls += [self.board_ctrl, self.change_btn]
        return box

    # ---------------------------------------------------------- Strategy group
    def _build_strategy_group(self, panel):
        box = wx.StaticBoxSizer(wx.StaticBox(panel, label='Strategy'), wx.VERTICAL)
        self.mode_auto_rb = wx.RadioButton(panel, label='Auto — rule-based, deterministic', style=wx.RB_GROUP)
        self.mode_llm_rb = wx.RadioButton(panel, label='LLM — the model picks strategy step by step via the endpoint')
        for rb in (self.mode_auto_rb, self.mode_llm_rb):
            rb.Bind(wx.EVT_RADIOBUTTON, self._on_settings_changed)
            box.Add(rb, 0, wx.LEFT | wx.TOP, 4)
        box.Add(wx.StaticText(panel, label='Auto is the measured path — every published bench number comes from it. LLM is non-deterministic and stops at the tool-call budget (Advanced).'), 0, wx.LEFT | wx.BOTTOM, 4)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(panel, label='Max route passes'), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.passes_ctrl = wx.SpinCtrl(panel, value='4', min=1, max=8)
        self.passes_ctrl.SetToolTip('Engine passes over the board. 4 is the measured default; more passes help hard boards and cost time.')
        row.Add(self.passes_ctrl, 0)
        self.strip_ctrl = wx.CheckBox(panel, label='Strip existing routing first')
        self.strip_ctrl.SetToolTip('Removes unlocked tracks/arcs and dangling vias; locked routing and free/stitching vias are kept.')
        row.Add(self.strip_ctrl, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 16)
        box.Add(row, 0, wx.LEFT | wx.BOTTOM, 6)
        self.finish_ctrl = wx.CheckBox(panel, label='Finish this board — route only what is still open')
        self.finish_ctrl.SetToolTip('Engine --route-open-only: routes the connections still open and never touches existing copper. Strip is disabled in this mode — the copper is the point.')
        self.finish_ctrl.Bind(wx.EVT_CHECKBOX, self._on_finish_toggle)
        box.Add(self.finish_ctrl, 0, wx.LEFT | wx.TOP, 4)
        self.det_ctrl = wx.CheckBox(panel, label='Reproducible run — deterministic engine, bounded by the work budget')
        self._det_tip = 'Engine --deterministic: the same board and settings produce the same result board. No time limit is used in this mode; the work budget (Advanced) bounds the run.'
        self.det_ctrl.SetToolTip(self._det_tip)
        self.det_ctrl.Bind(wx.EVT_CHECKBOX, self._on_settings_changed)
        box.Add(self.det_ctrl, 0, wx.LEFT | wx.BOTTOM, 4)
        self._config_controls += [self.mode_auto_rb, self.mode_llm_rb, self.passes_ctrl, self.strip_ctrl,
                                  self.finish_ctrl, self.det_ctrl]
        return box

    # ---------------------------------------------------------- Layers group
    def _build_layers_group(self, panel):
        box = wx.StaticBoxSizer(wx.StaticBox(panel, label='Layers'), wx.VERTICAL)
        lrow = wx.BoxSizer(wx.HORIZONTAL)
        self.law_auto_rb = wx.RadioButton(panel, label='Layer law: Automatic (recommended)', style=wx.RB_GROUP)
        self.law_custom_rb = wx.RadioButton(panel, label='Custom')
        self.law_auto_rb.Bind(wx.EVT_RADIOBUTTON, self._on_law)
        self.law_custom_rb.Bind(wx.EVT_RADIOBUTTON, self._on_law)
        lrow.Add(self.law_auto_rb, 0, wx.ALIGN_CENTER_VERTICAL)
        lrow.Add(self.law_custom_rb, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 12)
        box.Add(lrow, 0, wx.LEFT | wx.TOP, 4)
        self.law_caption = wx.StaticText(panel, label='Custom law is applied exactly as set — the pipeline will not escalate or second-guess it, in either mode.')
        box.Add(self.law_caption, 0, wx.LEFT, 4)
        self.rows_sizer = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.rows_sizer, 0, wx.EXPAND | wx.ALL, 4)
        self.cost_err_label = wx.StaticText(panel, label='')
        self.cost_err_label.Hide()
        box.Add(self.cost_err_label, 0, wx.LEFT | wx.BOTTOM, 4)
        self._config_controls += [self.law_auto_rb, self.law_custom_rb]
        return box

    def _layer_display(self, lay):
        if lay['name'] != lay['canonical']:
            return '%s (%s)' % (lay['name'], lay['canonical'])
        return lay['canonical']

    def _rebuild_layer_rows(self):
        # (Re)build the per-layer rows from self.facts; with no facts,
        # fall back to the pre-v1.12 free-text pair (the saved config
        # then round-trips exactly as it always did).
        self.rows_sizer.Clear(True)
        self.layer_rows = {}
        self.fallback_layers_ctrl = None
        self.fallback_cost_ctrl = None
        if not self.facts:
            g = wx.FlexGridSizer(0, 2, 6, 8); g.AddGrowableCol(1, 1)
            self.fallback_layers_ctrl = wx.TextCtrl(self.panel, value='')
            self.fallback_cost_ctrl = wx.TextCtrl(self.panel, value='')
            g.Add(wx.StaticText(self.panel, label='No tracks on layers'), 0, wx.ALIGN_CENTER_VERTICAL)
            g.Add(self.fallback_layers_ctrl, 1, wx.EXPAND)
            g.Add(wx.StaticText(self.panel, label='Layer costs (e.g. In1.Cu=10,In2.Cu=1.5)'), 0, wx.ALIGN_CENTER_VERTICAL)
            g.Add(self.fallback_cost_ctrl, 1, wx.EXPAND)
            self.rows_sizer.Add(g, 0, wx.EXPAND)
            self._config_controls += [self.fallback_layers_ctrl, self.fallback_cost_ctrl]
            return
        hdr = wx.BoxSizer(wx.HORIZONTAL)
        for txt, w in (('Layer', 170), ('No tracks', 70), ('Cost', 90), ('Note', 0)):
            t = wx.StaticText(self.panel, label=txt)
            hdr.Add(t, 1 if w == 0 else 0, wx.EXPAND if w == 0 else 0, w)
            if w: t.SetMinSize((w, -1))
        self.rows_sizer.Add(hdr, 0, wx.EXPAND | wx.BOTTOM, 2)
        for lay in self.facts['layers']:
            canon = lay['canonical']
            row = wx.BoxSizer(wx.HORIZONTAL)
            name = wx.StaticText(self.panel, label=self._layer_display(lay))
            name.SetMinSize((170, -1))
            ban = wx.CheckBox(self.panel)
            ban.SetToolTip('Checked layers get vias only — no tracks. A layer that carries a solid plane is normally checked.')
            cost = wx.Choice(self.panel, choices=list(COST_LABELS))
            cost.SetToolTip('A priced layer is discouraged, not banned: routing there costs more, so the tools avoid it unless it buys completion. 1x = normal (Off). Pricing a layer un-checks its ban.')
            note = wx.StaticText(self.panel, label=self.classif.get(canon, {}).get('note', ''))
            ban.Bind(wx.EVT_CHECKBOX, lambda e, c=canon: self._on_ban(c))
            cost.Bind(wx.EVT_CHOICE, lambda e, c=canon: self._on_cost(c))
            row.Add(name, 0, wx.ALIGN_CENTER_VERTICAL)
            row.Add(ban, 0, wx.ALIGN_CENTER_VERTICAL)
            ban.SetMinSize((70, -1))
            row.Add(cost, 0, wx.ALIGN_CENTER_VERTICAL)
            cost.SetMinSize((90, -1))
            row.Add(note, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
            self.rows_sizer.Add(row, 0, wx.EXPAND | wx.BOTTOM, 2)
            self.layer_rows[canon] = {'ban': ban, 'cost': cost, 'custom': None}
            self._config_controls += [ban, cost]
        self._relayout()

    def _on_ban(self, canon):
        # Structural form of the pipeline's own rule: a layer is
        # banned XOR priced, never both.
        row = self.layer_rows[canon]
        if row['ban'].GetValue() and self._row_factor(canon) is not None:
            row['cost'].SetSelection(0); row['custom'] = None
        self._on_settings_changed()

    def _on_cost(self, canon):
        row = self.layer_rows[canon]
        if self._row_factor(canon) is not None and row['ban'].GetValue():
            row['ban'].SetValue(False)
        self._on_settings_changed()

    def _row_factor(self, canon):
        row = self.layer_rows[canon]
        sel = row['cost'].GetSelection()
        if sel <= 0:
            return None
        labels = [row['cost'].GetString(i) for i in range(row['cost'].GetCount())]
        lab = labels[sel]
        if row['custom'] is not None and lab == ui_model.format_factor(row['custom']):
            return row['custom']
        try:
            return float(lab)
        except ValueError:
            return None

    def _set_row_factor(self, canon, factor):
        row = self.layer_rows[canon]
        row['custom'] = None
        if factor is None:
            row['cost'].SetSelection(0); return
        lab = ui_model.format_factor(factor)
        labels = list(COST_LABELS)
        if lab not in labels:
            labels.append(lab); row['custom'] = factor
            row['cost'].SetItems(labels)
        row['cost'].SetSelection(labels.index(lab))

    def _on_law(self, _evt=None):
        auto = self.law_auto_rb.GetValue()
        if auto:
            rec = ui_model.recommended_ban(self.classif)
            for canon, row in self.layer_rows.items():
                row['ban'].SetValue(canon in rec)
                self._set_row_factor(canon, None)
        self._apply_row_enable()
        self.law_caption.SetLabel('Automatic sends the detected plane-layer law and the pipeline escalation stays armed.' if auto
                                  else 'Custom law is applied exactly as set — the pipeline will not escalate or second-guess it, in either mode.')
        self._on_settings_changed()

    def _on_finish_toggle(self, _evt=None):
        self._apply_finish_state()
        self._on_settings_changed()

    def _apply_finish_state(self):
        # v1.12.2 (#171): Finish-this-board keeps the board's copper;
        # Strip would remove it, so Strip is off and disabled while
        # Finish is on (the pipeline forces no-strip in this mode too).
        if self.finish_ctrl.GetValue():
            self.strip_ctrl.SetValue(False)
            self.strip_ctrl.Enable(False)
        elif not getattr(self, '_running', False):
            self.strip_ctrl.Enable(True)

    def _apply_det_state(self):
        # v1.12.2 (#176): Reproducible is an Auto-mode property --
        # the LLM path picks each step as it goes and varies run to
        # run by design, so the checkbox is disabled while LLM mode
        # is selected (the Finish/Strip disable-with-reason pattern).
        if self.mode_llm_rb.GetValue():
            self.det_ctrl.SetValue(False)  # #185: clear, not just disable -- a disabled-but-checked box still reads True in _collect_config (the _apply_finish_state pattern)
            self.det_ctrl.Enable(False)
            self.det_ctrl.SetToolTip('Reproducible run needs Auto mode: the LLM path picks each step as it goes, so it varies run to run.')
        elif not getattr(self, '_running', False):
            self.det_ctrl.Enable(True)
            self.det_ctrl.SetToolTip(getattr(self, '_det_tip', 'Engine --deterministic: the same board and settings produce the same result board.'))

    def _apply_row_enable(self):
        auto = self.facts and self.law_auto_rb.GetValue()
        for row in self.layer_rows.values():
            row['ban'].Enable(not auto); row['cost'].Enable(not auto)

    # ---------------------------------------------------------- board facts + population
    def _load_board_facts(self, board_path):
        self.facts = board_facts.load_facts(board_path)
        if self.facts:
            self.classif = ui_model.classify_layers(self.facts['layers'], self.facts['zones_by_layer'])
            self.aliases = ui_model.alias_map(self.facts['layers'])
        else:
            self.classif = {}; self.aliases = {}
        self._rebuild_layer_rows()
        self._refresh_facts_line()

    def _refresh_facts_line(self):
        if not self.facts:
            self.facts_label.SetLabel('Board facts unavailable — settings are shown as saved.')
            return
        planes = [self._layer_display(l) for l in self.facts['layers']
                  if self.classif.get(l['canonical'], {}).get('kind') == 'plane']
        txt = 'Detected: %d copper layers · %d zones' % (self.facts['copper_count'], self.facts['zone_count'])
        if planes:
            txt += ' · plane layer: %s' % ', '.join(planes)
        self.facts_label.SetLabel(txt)

    def _populate_all(self):
        cfg = self.cfg
        self.endpoint_ctrl.SetValue(cfg.get('endpoint', ''))
        self._set_combo(self.model_ctrl, cfg.get('model', ''))
        self.key_ctrl.SetValue(cfg.get('api_key', ''))
        self.tm_ctrl.SetValue(cfg.get('tracemaker_path', ''))
        self._refresh_tm_note()
        if cfg.get('mode') == 'llm':
            self.mode_llm_rb.SetValue(True)
        else:
            self.mode_auto_rb.SetValue(True)
        self.passes_ctrl.SetValue(int(cfg.get('max_passes', 4)))
        self.strip_ctrl.SetValue(bool(cfg.get('strip_existing', True)))
        self.finish_ctrl.SetValue(bool(cfg.get('route_open_only', False)))
        self.det_ctrl.SetValue(bool(cfg.get('deterministic', False)))
        self._apply_finish_state()
        self._apply_det_state()
        if cfg.get('use_backend'):
            self.run_backend_rb.SetValue(True)
        else:
            self.run_local_rb.SetValue(True)
        self._on_where()
        self.backend_url_ctrl.SetValue(cfg.get('backend_url', 'http://127.0.0.1:8731'))
        self.backend_tok_ctrl.SetValue(cfg.get('backend_token', ''))
        self._refresh_remove_btn()
        self.adv_finish_signal.SetValue(int(cfg.get('finish_signal_cap', 8)))
        self.adv_finish_pour.SetValue(int(cfg.get('finish_pour_cap', 4)))
        self.adv_long_finish.SetValue(int(cfg.get('long_finish_cap', 6)))
        self.adv_reroute_window.SetValue(int(cfg.get('reroute_window_cap', 0)))
        self.adv_ilp_repair.SetValue(int(cfg.get('ilp_repair_cap', 0)))
        self.adv_portfolio.SetValue(cfg.get('engine_portfolio', 'tm_relax'))
        self.adv_law_policy.SetStringSelection(cfg.get('layer_law_policy', 'auto'))
        self.adv_pass_advice.SetStringSelection(cfg.get('pass_advice', 'promote'))
        self.adv_max_tool_calls.SetValue(int(cfg.get('max_tool_calls', 48)))
        self.adv_work.SetValue(int(cfg.get('work', 40000000)))
        self.adv_timeout.SetValue(int(cfg.get('request_timeout_s', 600)))
        self._populate_layers()

    def _set_combo(self, combo, value):
        items = [combo.GetString(i) for i in range(combo.GetCount())]
        if value and value not in items:
            combo.Insert(value, 0)
        combo.SetValue(value or '')

    def _populate_layers(self):
        self.law_auto_rb.Enable(); self.law_custom_rb.Enable()
        if not self.facts:
            if self.fallback_layers_ctrl is not None:
                self.fallback_layers_ctrl.SetValue(self.cfg.get('no_tracks_on', 'In1.Cu'))
                self.fallback_cost_ctrl.SetValue(self.cfg.get('layer_cost', ''))
            self.law_auto_rb.Disable(); self.law_custom_rb.Disable()
            return
        saved_ban_raw = ui_model.parse_ban_list(self.cfg.get('no_tracks_on', 'In1.Cu'))
        saved_ban, self.extra_bans = ui_model.resolve_names(saved_ban_raw, self.aliases)
        saved_cost, self.cost_spec_error = ui_model.parse_cost_map(self.cfg.get('layer_cost', ''))
        if saved_cost is None:
            saved_cost = {}
        cost_canon, self.extra_costs = {}, {}
        for name, f in saved_cost.items():
            res, _un = ui_model.resolve_names([name], self.aliases)
            if res: cost_canon[res[0]] = f
            else: self.extra_costs[name] = f
        for canon, row in self.layer_rows.items():
            f = cost_canon.get(canon)
            self._set_row_factor(canon, f)
            # The pipeline's rule, reflected: priced => not banned.
            row['ban'].SetValue(canon in saved_ban and f is None)
        rec = ui_model.recommended_ban(self.classif)
        is_auto = (set(saved_ban) == rec and not cost_canon and not self.extra_costs
                   and not self.extra_bans)
        if self.facts['copper_count'] <= 2:
            # No inner layers: the law radio has nothing to govern;
            # leave the rows editable (Custom) per proposal 4.4.
            self.law_custom_rb.SetValue(True); self.law_auto_rb.Disable()
            self.law_caption.SetLabel('Layer law applies to boards with inner layers.')
            for row in self.layer_rows.values():
                row['ban'].Enable(); row['cost'].Enable()
        elif is_auto:
            self.law_auto_rb.SetValue(True); self._on_law()
        else:
            self.law_custom_rb.SetValue(True); self._on_law()
        if self.cost_spec_error:
            self.cost_err_label.SetLabel('Saved layer-cost spec could not be parsed (%s) — treated as off, as the pipeline treats it.' % self.cost_spec_error)
            self.cost_err_label.Show()
        else:
            self.cost_err_label.Hide()
        self._refresh_notice()

    def _saved_law_text(self, saved_ban, cost_canon):
        disp = {l['canonical']: self._layer_display(l) for l in self.facts['layers']}
        parts = []
        if saved_ban:
            parts.append('custom law banning %s' % ' + '.join(disp.get(c, c) for c in saved_ban))
        else:
            parts.append('custom law banning nothing')
        if cost_canon or self.extra_costs:
            parts.append('layers priced')
        return ', '.join(parts)

    def _current_law(self):
        # (#183): the layer law the Run button would send right now
        # -- the same computation _collect_config performs.
        # Automatic IS the recommendation; Custom is the rows plus
        # the unresolved extras.
        if self.law_auto_rb.GetValue():
            return set(ui_model.recommended_ban(self.classif)), {}
        ban = {c for c, r in self.layer_rows.items() if r['ban'].GetValue()}
        cost = {}
        for c in self.layer_rows:
            f = self._row_factor(c)
            if f is not None and c not in ban:
                cost[c] = f
        cost.update(self.extra_costs)
        return ban | set(self.extra_bans), cost

    def _refresh_notice(self):
        # (#183): ONE comparison basis -- the current UI state
        # (live mode radio + the law Run would send) against the
        # recommendation. The pre-fix version mixed the live mode
        # with the SAVED law and ran only at populate time, so a
        # manual fix never cleared the notice.
        if not self.facts:
            self.notice_label.Hide(); self.use_rec_btn.Hide(); return
        ban, cost = self._current_law()
        diffs = []
        mode = 'llm' if self.mode_llm_rb.GetValue() else 'auto'
        if mode != 'auto':
            diffs.append('LLM mode')
        if ui_model.law_differs(ban, cost, self.classif):
            diffs.append(self._saved_law_text(ban, cost))
        if diffs:
            self.notice_label.SetLabel('Settings differ from this board’s recommended start: %s.' % '; '.join(diffs))
            self.notice_label.Show(); self.use_rec_btn.Show()
        else:
            self.notice_label.Hide(); self.use_rec_btn.Hide()
        self._relayout()

    def _on_use_recommended(self, _evt):
        self.mode_auto_rb.SetValue(True)
        if self.facts and self.facts['copper_count'] > 2:
            self.law_auto_rb.SetValue(True)
            self._on_law()
        self.extra_bans = []; self.extra_costs = {}
        self.notice_label.Hide(); self.use_rec_btn.Hide()
        self._relayout()
        self._on_settings_changed()

    # ---------------------------------------------------------- Run-on group
    def _build_runon_group(self, panel):
        box = wx.StaticBoxSizer(wx.StaticBox(panel, label='Run on'), wx.VERTICAL)
        rrow = wx.BoxSizer(wx.HORIZONTAL)
        self.run_backend_rb = wx.RadioButton(panel, label='Docker backend', style=wx.RB_GROUP)
        self.run_local_rb = wx.RadioButton(panel, label='This computer')
        for rb in (self.run_backend_rb, self.run_local_rb):
            rb.Bind(wx.EVT_RADIOBUTTON, self._on_where)
            rrow.Add(rb, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        box.Add(rrow, 0, wx.LEFT | wx.TOP, 4)
        box.Add(wx.StaticText(panel, label='Backend runs are queued on the server and watched from here. Local runs use this machine’s TraceMaker and endpoint.'), 0, wx.LEFT, 4)
        # Backend fields
        self.backend_box = wx.BoxSizer(wx.VERTICAL)
        brow = wx.BoxSizer(wx.HORIZONTAL)
        brow.Add(wx.StaticText(panel, label='Backend URL'), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.backend_url_ctrl = wx.ComboBox(panel)
        self._refresh_server_combo()
        self.backend_url_ctrl.Bind(wx.EVT_COMBOBOX, self._on_server_pick)
        self.backend_url_ctrl.Bind(wx.EVT_TEXT, lambda e: self._refresh_remove_btn())
        self.test_btn = wx.Button(panel, label='Test connection')
        self.test_btn.Bind(wx.EVT_BUTTON, self.on_test)
        brow.Add(self.backend_url_ctrl, 1, wx.ALIGN_CENTER_VERTICAL)
        brow.Add(self.test_btn, 0, wx.LEFT, 8)
        self.backend_box.Add(brow, 0, wx.EXPAND | wx.TOP, 4)
        self.test_label = wx.StaticText(panel, label='')
        self.backend_box.Add(self.test_label, 0, wx.LEFT | wx.TOP, 2)
        trow = wx.BoxSizer(wx.HORIZONTAL)
        trow.Add(wx.StaticText(panel, label='Backend token'), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.backend_tok_ctrl = wx.TextCtrl(panel, value='', style=wx.TE_PASSWORD)
        self.backend_tok_ctrl.SetToolTip('The backend’s auth token (its config.json, key auth_token). Stored in your plugin config.')
        self.page_btn = wx.Button(panel, label='Backend config page')
        self.page_btn.Bind(wx.EVT_BUTTON, self.on_page)
        trow.Add(self.backend_tok_ctrl, 1, wx.ALIGN_CENTER_VERTICAL)
        trow.Add(self.page_btn, 0, wx.LEFT, 8)
        self.backend_box.Add(trow, 0, wx.EXPAND | wx.TOP, 4)
        srow = wx.BoxSizer(wx.HORIZONTAL)
        self.add_server_btn = wx.Button(panel, label='Add server…')
        self.add_server_btn.Bind(wx.EVT_BUTTON, self._on_add_server)
        srow.Add(self.add_server_btn, 0)
        self.remove_server_btn = wx.Button(panel, label='Remove server')
        self.remove_server_btn.Bind(wx.EVT_BUTTON, self._on_remove_server)
        srow.Add(self.remove_server_btn, 0, wx.LEFT, 8)
        self.backend_box.Add(srow, 0, wx.TOP, 4)
        box.Add(self.backend_box, 0, wx.EXPAND | wx.LEFT, 12)
        # Local fields
        self.local_box = wx.BoxSizer(wx.VERTICAL)
        g = wx.FlexGridSizer(0, 2, 6, 8); g.AddGrowableCol(1, 1)
        def add(label, ctrl):
            g.Add(wx.StaticText(panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            g.Add(ctrl, 1, wx.EXPAND)
        self.endpoint_ctrl = wx.ComboBox(panel, choices=list(ENDPOINT_PRESETS))
        self.endpoint_ctrl.SetToolTip('OpenAI-compatible LLM endpoint. Applies to local runs only — the backend uses its own endpoint.')
        add('LLM endpoint (OpenAI-compatible)', self.endpoint_ctrl)
        self.model_ctrl = wx.ComboBox(panel, choices=list(MODEL_PRESETS))
        self.model_ctrl.SetToolTip('Must match a model your endpoint serves.')
        add('Model', self.model_ctrl)
        self.key_ctrl = wx.TextCtrl(panel, value='', style=wx.TE_PASSWORD)
        add('API key (if needed)', self.key_ctrl)
        self.local_box.Add(g, 0, wx.EXPAND | wx.TOP, 4)
        tmrow = wx.BoxSizer(wx.HORIZONTAL)
        tmrow.Add(wx.StaticText(panel, label='TraceMaker binary'), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.tm_ctrl = wx.TextCtrl(panel, value='')
        self.tm_ctrl.SetToolTip('The TraceMaker executable used by local runs. Not used in backend mode.')
        self.tm_ctrl.Bind(wx.EVT_TEXT, lambda e: self._refresh_tm_note())
        self.tm_browse_btn = wx.Button(panel, label='Browse…')
        self.tm_browse_btn.Bind(wx.EVT_BUTTON, self.on_tm_browse)
        tmrow.Add(self.tm_ctrl, 1, wx.ALIGN_CENTER_VERTICAL)
        tmrow.Add(self.tm_browse_btn, 0, wx.LEFT, 8)
        self.local_box.Add(tmrow, 0, wx.EXPAND | wx.TOP, 4)
        self.tm_note = wx.StaticText(panel, label='')
        self.local_box.Add(self.tm_note, 0, wx.LEFT | wx.TOP, 2)
        self.local_box.Add(wx.StaticText(panel, label='The backend uses its own LLM endpoint and TraceMaker — these fields apply to local runs only.'), 0, wx.TOP, 2)
        box.Add(self.local_box, 0, wx.EXPAND | wx.LEFT, 12)
        self._config_controls += [self.run_backend_rb, self.run_local_rb, self.backend_url_ctrl,
                                  self.test_btn, self.backend_tok_ctrl, self.page_btn,
                                  self.add_server_btn, self.remove_server_btn,
                                  self.endpoint_ctrl, self.model_ctrl, self.key_ctrl,
                                  self.tm_ctrl, self.tm_browse_btn]
        return box

    def _on_where(self, _evt=None):
        backend = self.run_backend_rb.GetValue()
        for c in (self.backend_url_ctrl, self.test_btn, self.backend_tok_ctrl, self.page_btn,
                  self.add_server_btn):
            c.Enable(backend)
        for c in (self.endpoint_ctrl, self.model_ctrl, self.key_ctrl, self.tm_ctrl, self.tm_browse_btn):
            c.Enable(not backend)
        self._refresh_remove_btn()
        self._on_settings_changed()

    # ------------------------------------------------- custom servers (#199)
    def _refresh_server_combo(self):
        # Rebuild the Backend URL choices: the preset URLs, then the
        # custom servers by name. _combo_entries maps each choice
        # index to its custom entry (None for a preset). The field's
        # current text (a URL, or a half-typed one) is preserved.
        keep = self.backend_url_ctrl.GetValue()
        self._combo_entries = ui_model.server_combo_entries(BACKEND_PRESETS, self.custom_servers)
        self.backend_url_ctrl.SetItems(ui_model.server_combo_labels(BACKEND_PRESETS, self.custom_servers))
        if keep:
            self.backend_url_ctrl.SetValue(keep)

    def _selected_custom_server(self):
        # The custom server the URL field currently names, if any --
        # selection is by URL, however the URL got into the field.
        return ui_model.find_server(self.custom_servers, self.backend_url_ctrl.GetValue())

    def _refresh_remove_btn(self):
        btn = getattr(self, 'remove_server_btn', None)
        if btn is not None:
            btn.Enable(self.run_backend_rb.GetValue() and self._selected_custom_server() is not None)

    def _on_server_pick(self, evt):
        idx = evt.GetSelection()
        entry = self._combo_entries[idx] if 0 <= idx < len(self._combo_entries) else None
        if entry is not None:
            self.backend_url_ctrl.SetValue(entry['url'])
            self.backend_tok_ctrl.SetValue(entry.get('token', ''))
        self._refresh_remove_btn()
        self._on_settings_changed()
        evt.Skip()

    def _on_add_server(self, _evt):
        dlg = AddServerDialog(self, BACKEND_PRESETS, self.custom_servers)
        try:
            if dlg.ShowModal() == wx.ID_OK and dlg.entry is not None:
                self.custom_servers = ui_model.add_server(self.custom_servers, dlg.entry)
                self._refresh_server_combo()
                self.backend_url_ctrl.SetValue(dlg.entry['url'])
                self.backend_tok_ctrl.SetValue(dlg.entry.get('token', ''))
                self._refresh_remove_btn()
                self.collect_cfg()  # persist now, the on_page precedent
                self.test_label.SetLabel('Added backend server %s.' % dlg.entry['name'])
        finally:
            dlg.Destroy()

    def _on_remove_server(self, _evt):
        entry = self._selected_custom_server()
        if entry is None:
            return
        if wx.MessageBox('Remove the custom server "%s" (%s)?' % (entry['name'], entry['url']),
                         'AI Router', wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
            return
        self.custom_servers = ui_model.remove_server(self.custom_servers, entry['url'])
        self._refresh_server_combo()
        self.backend_url_ctrl.SetValue(BACKEND_PRESETS[0])
        if self.backend_tok_ctrl.GetValue() == entry.get('token', ''):
            self.backend_tok_ctrl.SetValue('')
        self._refresh_remove_btn()
        self.collect_cfg()  # persist now, the on_page precedent

    def _refresh_tm_note(self):
        p = self.tm_ctrl.GetValue().strip()
        self.tm_note.SetLabel('' if (p and os.path.exists(p)) else 'Not found — local runs will fail until this path exists.')

    def on_tm_browse(self, _evt):
        dlg = wx.FileDialog(self, 'Choose the TraceMaker binary', style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                self.tm_ctrl.SetValue(dlg.GetPath())
        finally:
            dlg.Destroy()

    def on_test(self, _evt):
        url = self.backend_url_ctrl.GetValue().strip().rstrip('/')
        if not ui_model.valid_http_url(url):
            self.test_label.SetLabel('Enter a backend URL like http://host:port first.')
            return
        self.test_btn.Disable(); self.test_label.SetLabel('Testing…')
        def probe():
            try:
                h = backend_client.health(url)
                txt = 'Backend %s · KiCad %s · TraceMaker %s' % (
                    h.get('backend_version'), h.get('kicad_cli'), h.get('tracemaker_selected') or 'none')
            except Exception as e:
                txt = 'Failed: %s' % self._plain_error(e, backend=True)
            wx.CallAfter(self._test_done, txt)
        threading.Thread(target=probe, daemon=True).start()

    def _test_done(self, txt):
        self.test_label.SetLabel(txt); self.test_btn.Enable()

    def on_change_board(self, _evt):
        dlg = wx.FileDialog(self, 'Choose a board', wildcard='KiCad boards (*.kicad_pcb)|*.kicad_pcb',
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        try:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()
        finally:
            dlg.Destroy()
        self.collect_cfg()  # persist the current control state first
        self.board_ctrl.SetValue(path)
        self._load_board_facts(path)
        self._populate_layers()
        self._on_settings_changed()

    # ---------------------------------------------------------- Advanced
    def _build_advanced(self, panel):
        pane = wx.CollapsiblePane(panel, label='Advanced — finisher caps, engine portfolio, LLM tool-call budget, layer-law policy')
        pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, lambda e: self._relayout())
        win = pane.GetPane()
        g = wx.FlexGridSizer(0, 4, 6, 8)
        def spin(val, lo, hi, tip):
            s = wx.SpinCtrl(win, value=str(val), min=lo, max=hi)
            s.SetToolTip(tip)
            return s
        def add2(l1, c1, l2, c2):
            g.Add(wx.StaticText(win, label=l1), 0, wx.ALIGN_CENTER_VERTICAL)
            g.Add(c1, 0)
            g.Add(wx.StaticText(win, label=l2), 0, wx.ALIGN_CENTER_VERTICAL)
            g.Add(c2, 0)
        self.adv_finish_signal = spin(8, 0, 999, 'Max signal finish_leg attempts per run (auto mode).')
        self.adv_finish_pour = spin(4, 0, 999, 'Max pour-net finish_leg attempts per run (auto mode).')
        add2('Finish signal cap', self.adv_finish_signal, 'Finish pour cap', self.adv_finish_pour)
        self.adv_long_finish = spin(6, 0, 999, 'Max large-window long_finish attempts per run (auto mode).')
        self.adv_reroute_window = spin(0, 0, 999, 'Windowed engine re-route attempts on split signal nets (0 = off).')
        add2('Long finish cap', self.adv_long_finish, 'Re-route window cap', self.adv_reroute_window)
        self.adv_ilp_repair = spin(0, 0, 999, 'Joint ILP repair attempts per run (0 = off).')
        self.adv_max_tool_calls = spin(48, 1, 1000, 'LLM mode stops when this budget is spent — the run ends wherever it is. Sent to the backend too, where it governs backend LLM runs.')
        add2('ILP repair cap', self.adv_ilp_repair, 'LLM tool-call budget', self.adv_max_tool_calls)
        self.adv_work = spin(40000000, 0, 2147483647, 'Engine work budget per pass (TraceMaker expansions).')
        self.adv_timeout = spin(600, 10, 86400, 'HTTP request timeout (seconds) for local LLM calls.')
        add2('Engine work budget', self.adv_work, 'Request timeout (s)', self.adv_timeout)
        g.Add(wx.StaticText(win, label='Engine portfolio'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.adv_portfolio = wx.ComboBox(win, choices=list(PORTFOLIO_PRESETS))
        self.adv_portfolio.SetToolTip('Alternative engines run from the base and gate-selected. Empty = TraceMaker only; tm_relax is the measured default.')
        g.Add(self.adv_portfolio, 0)
        g.Add(wx.StaticText(win, label='Layer-law policy'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.adv_law_policy = wx.Choice(win, choices=['auto', 'off'])
        self.adv_law_policy.SetToolTip('auto: on a 4+ layer board with an In2 pour, the pipeline tries banning In2 first and escalates if signal nets split. Only engages under the default (Automatic) law with no layer costs.')
        g.Add(self.adv_law_policy, 0)
        g.Add(wx.StaticText(win, label='Pass advice'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.adv_pass_advice = wx.Choice(win, choices=['promote', 'priority'])
        self.adv_pass_advice.SetToolTip('promote: failing signal nets join the protected first_nets set. priority: first_nets stays the initial critical set; failures are ordered via advice priority pairs.')
        g.Add(self.adv_pass_advice, 0)
        g.Add(wx.StaticText(win, label=''), 0)
        g.Add(wx.StaticText(win, label='These govern local runs. On backend runs the server’s own configuration applies, except the LLM tool-call budget, which the dialog sends.'), 0)
        win.SetSizer(g)
        self._config_controls += [self.adv_finish_signal, self.adv_finish_pour, self.adv_long_finish,
                                  self.adv_reroute_window, self.adv_ilp_repair, self.adv_max_tool_calls,
                                  self.adv_work, self.adv_timeout, self.adv_portfolio,
                                  self.adv_law_policy, self.adv_pass_advice]
        return pane

    # ---------------------------------------------------------- collect
    def collect_cfg(self):
        cfg = self.cfg
        cfg['endpoint'] = self.endpoint_ctrl.GetValue().strip()
        cfg['model'] = self.model_ctrl.GetValue().strip()
        cfg['api_key'] = self.key_ctrl.GetValue()
        cfg['tracemaker_path'] = self.tm_ctrl.GetValue().strip()
        cfg['mode'] = 'llm' if self.mode_llm_rb.GetValue() else 'auto'
        cfg['max_passes'] = self.passes_ctrl.GetValue()
        cfg['strip_existing'] = self.strip_ctrl.GetValue()
        cfg['route_open_only'] = self.finish_ctrl.GetValue()
        cfg['deterministic'] = self.det_ctrl.GetValue()
        cfg['use_backend'] = self.run_backend_rb.GetValue()
        cfg['backend_url'] = self.backend_url_ctrl.GetValue().strip()
        cfg['backend_token'] = self.backend_tok_ctrl.GetValue()
        self.custom_servers = ui_model.sync_server_token(self.custom_servers,
                                                         cfg['backend_url'], cfg['backend_token'])
        cfg['backend_servers'] = [dict(e) for e in self.custom_servers]
        cfg['finish_signal_cap'] = self.adv_finish_signal.GetValue()
        cfg['finish_pour_cap'] = self.adv_finish_pour.GetValue()
        cfg['long_finish_cap'] = self.adv_long_finish.GetValue()
        cfg['reroute_window_cap'] = self.adv_reroute_window.GetValue()
        cfg['ilp_repair_cap'] = self.adv_ilp_repair.GetValue()
        cfg['engine_portfolio'] = self.adv_portfolio.GetValue().strip()
        cfg['layer_law_policy'] = self.adv_law_policy.GetStringSelection()
        cfg['pass_advice'] = self.adv_pass_advice.GetStringSelection()
        cfg['max_tool_calls'] = self.adv_max_tool_calls.GetValue()
        cfg['work'] = self.adv_work.GetValue()
        cfg['request_timeout_s'] = self.adv_timeout.GetValue()
        order = [l['canonical'] for l in self.facts['layers']] if self.facts else []
        if not self.facts:
            cfg['no_tracks_on'] = self.fallback_layers_ctrl.GetValue().strip()
            cfg['layer_cost'] = self.fallback_cost_ctrl.GetValue().strip()
        elif self.law_auto_rb.GetValue():
            cfg['no_tracks_on'] = ui_model.format_ban_spec(sorted(ui_model.recommended_ban(self.classif)), order)
            cfg['layer_cost'] = ''
        else:
            ban = [c for c, r in self.layer_rows.items() if r['ban'].GetValue()]
            cost = {}
            for c in self.layer_rows:
                f = self._row_factor(c)
                if f is not None and c not in ban:
                    cost[c] = f
            cost.update(self.extra_costs)
            cfg['no_tracks_on'] = ui_model.format_ban_spec(ban + list(self.extra_bans), order)
            cfg['layer_cost'] = ui_model.format_cost_spec(cost, order)
        cfgmod.save(cfg)
        return cfg

    # ---------------------------------------------------------- logging
    def log(self, msg):
        # Worker-thread logging: raising Cancelled here IS the
        # cooperative cancellation mechanism (pipeline/orchestrator
        # call this from the worker thread at step boundaries).
        if self.cancel_event.is_set():
            raise Cancelled()
        wx.CallAfter(self.log_ctrl.AppendText, str(msg) + '\n')

    def _log_ui(self, msg):
        # v1.8.2 (#18): UI-thread / exception-handler logging that
        # never raises. Before, on_cancel set the event and then
        # called log() -- which raised Cancelled inside the wx
        # handler -- and the worker's except-Cancelled handler called
        # log() again and died on the second raise.
        wx.CallAfter(self.log_ctrl.AppendText, str(msg) + '\n')

    # ---------------------------------------------------------- run flow
    def _set_running(self, on):
        self._running = on
        for c in self._config_controls:
            try: c.Enable(not on)
            except Exception: pass
        self.run_btn.Enable(not on)
        self.cancel_btn.Enable(on)
        if not on:
            self._apply_row_enable()
            self._apply_finish_state()
            self._apply_det_state()
            self._refresh_remove_btn()
        if on:
            self.open_btn.Disable()

    def _plain_error(self, e, backend=False):
        # Plain-language failure lines for the lamp detail / test
        # result (the full exception still goes to the log).
        msg = str(e)
        if '401' in msg:
            return 'Token rejected by the backend — it must match the backend’s auth_token (its config.json).'
        if backend and any(s in msg for s in ('Connection refused', 'URLError', 'timed out', 'Name or service', 'Temporary failure')):
            return 'Backend unreachable — check the URL and that the backend is running.'
        return msg

    def on_run(self, _evt):
        board = self.board_ctrl.GetValue().strip()
        if not board or not os.path.exists(board):
            wx.MessageBox('Board file not found: %s' % board, 'AI Router', wx.OK | wx.ICON_ERROR)
            return
        if self.run_backend_rb.GetValue() and not ui_model.valid_http_url(self.backend_url_ctrl.GetValue().strip()):
            wx.MessageBox('Backend URL must look like http://host:port — got: %s' % self.backend_url_ctrl.GetValue().strip(),
                          'AI Router', wx.OK | wx.ICON_ERROR)
            return
        cfg = self.collect_cfg()
        self.cancel_event.clear()
        self.result = None
        self._set_running(True)
        if cfg.get('use_backend'):
            self.set_status('RUNNING', 'Backend run — submitting…')
        else:
            self.set_status('RUNNING', 'Local run · %s mode' % cfg.get('mode'))
        def work():
            try:
                if cfg.get('use_backend'):
                    self.result = self._run_backend(cfg, board)
                elif cfg['mode'] == 'llm':
                    pipe = Pipeline(cfg, board, self.log)
                    self.result = orchestrator.run_llm(cfg, pipe, self.log)
                    dest = self._deliver_local(self.result, board)
                    if dest:
                        self.result['final_board'] = dest
                else:
                    pipe = Pipeline(cfg, board, self.log)
                    self.result = orchestrator.run_auto(cfg, pipe, self.log)
                    dest = self._deliver_local(self.result, board)
                    if dest:
                        self.result['final_board'] = dest
                g = (self.result or {}).get('gate') or {}
                final = (self.result or {}).get('final_board')
                self.log('=== DONE === final board: %s' % final)
                self.log('Final gate: unconnected=%s pour=%s split_nets=%s violations=%s' % (
                    g.get('unconnected_total'), g.get('pour_items'), g.get('split_nets'), g.get('violations_total')))
                # DONE means the run completed, never that the board
                # is clean -- the gate numbers ride in the same line.
                self._set_status('DONE', '%s unconnected · %s pour · %s violations · result: %s' % (
                    g.get('unconnected_total'), g.get('pour_items'), g.get('violations_total'),
                    os.path.basename(final) if final else '(none)'))
                wx.CallAfter(self._open_done, final)
            except Cancelled:
                self._log_ui('Cancelled by user (takes effect at step boundaries).')
                self._set_status('CANCELLED', 'Stopped at a step boundary — no result board')
            except Exception as e:
                self._log_ui('ERROR: %s' % e)
                self._set_status('FAILED', self._plain_error(e, backend=bool(cfg.get('use_backend'))))
            finally:
                wx.CallAfter(self._set_running, False)
        self.worker_thread = threading.Thread(target=work, daemon=True)
        self.worker_thread.start()

    def _open_done(self, final):
        self.open_btn.Enable()
        if final:
            self.open_btn.SetLabel('Open result board (%s)' % os.path.basename(final))
            self._relayout()

    def on_cancel(self, _evt):
        self.cancel_event.set()
        self._log_ui('Cancel requested...')

    def on_open(self, _evt):
        final = (self.result or {}).get('final_board')
        if final and os.path.exists(final):
            subprocess.Popen(['pcbnew', final])

    def on_page(self, _evt):
        import webbrowser
        webbrowser.open(self.backend_url_ctrl.GetValue().strip() or 'http://127.0.0.1:8731')

    def _deliver_local(self, result, board):
        # Local runs: copy the workdir's final board beside the
        # source under the #167 name (the workdir stays as the
        # run's audit trail) and fetch the project sidecars under
        # the new stem, like the backend download path does.
        final = (result or {}).get('final_board')
        if not final or not os.path.exists(final):
            return None
        try:
            dest = naming.unique_result_path(board)
            shutil.copyfile(final, dest)
            dst_stem = os.path.splitext(dest)[0]
            _src_pro, _src_dru = naming.sidecar_paths(board)  # (#182): project-stem resolution -- a renamed board delivers its real sidecars
            for _s, ext in ((_src_pro, '.kicad_pro'), (_src_dru, '.kicad_dru')):
                if _s:
                    shutil.copyfile(_s, dst_stem + ext)
            self._log_ui('Result board: %s' % dest)
            return dest
        except Exception as e:
            self._log_ui('Could not copy the result beside the source board (%s); it is at %s' % (e, final))
            return None

    def _run_backend(self, cfg, board):
        url = (cfg.get('backend_url') or 'http://127.0.0.1:8731').rstrip('/')
        tok = cfg.get('backend_token', '')
        h = backend_client.health(url)
        self.log('Backend %s - KiCad %s - TraceMaker selected: %s (installed: %s)' % (
            h.get('backend_version'), h.get('kicad_cli'), h.get('tracemaker_selected') or 'none',
            ', '.join(h.get('tracemaker_versions') or [])))
        if h.get('tracemaker_provenance'):
            self.log('Engine provenance: %s' % ' | '.join(l for l in h['tracemaker_provenance'].splitlines() if l.strip()))
        elif h.get('tracemaker_selected_sha256'):
            self.log('Engine archive sha256: %s' % h['tracemaker_selected_sha256'])
        overrides = ui_model.build_overrides(cfg)  # the five keys the dialog has always sent + max_tool_calls (#168, decision 12)
        jid = backend_client.submit(url, tok, board, overrides)
        self.log('Submitted job %s - routing runs on the backend; this window just watches.' % jid)
        self._set_status('RUNNING', 'Backend job %s · queued · elapsed 0:00' % jid)
        t0 = time.time()
        last_status, last_tail = '', ''
        while True:
            if self.cancel_event.is_set():
                backend_client.cancel(url, tok, jid)
                raise Cancelled()
            job = backend_client.get_job(url, tok, jid)
            st = job.get('status')
            if st != last_status:
                self.log('Job status: %s' % st)
                last_status = st
            if st in ('queued', 'running'):
                el = int(time.time() - t0)
                self._set_status('RUNNING', 'Backend job %s · %s · elapsed %d:%02d' % (jid, st, el // 60, el % 60))
            tail = job.get('log_tail') or ''
            if tail != last_tail:
                for line in tail.splitlines()[-3:]:
                    if line.strip():
                        self.log('  ' + line)
                last_tail = tail
            if st == 'done':
                # #167: the named, never-overwriting download point.
                dest = naming.unique_result_path(board)
                backend_client.download_result(url, tok, jid, dest)
                # v1.8.2 (#17): fetch the project sidecars next to the
                # result board, so opening it keeps the design rules.
                for kind, ext in (('result.pro', '.kicad_pro'), ('result.dru', '.kicad_dru')):
                    try:
                        if backend_client.download_sidecar(url, tok, jid, kind, os.path.splitext(dest)[0] + ext):
                            self.log('Result sidecar downloaded: %s' % (os.path.splitext(dest)[0] + ext))
                    except Exception:
                        pass
                self.log('Result downloaded to %s' % dest)
                return {'final_board': dest, 'gate': job.get('gate') or {}}
            if st == 'cancelled':
                raise Cancelled()
            if st in ('error', 'interrupted'):
                raise RuntimeError('backend job %s: %s' % (st, job.get('error') or ''))
            threading.Event().wait(5)
