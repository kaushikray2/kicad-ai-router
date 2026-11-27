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

"""Markings editor + engineered-nets report dialogs (wx).

All state lives in markings_model.MarkingsEditorModel; these
dialogs only bind controls to the model's form dicts and entry
conversions, so what the acceptance runs drive headless is exactly
what the GUI drives.  Saves go through the model ->
markings.save_markings() (validate-first): the editor cannot write
a sidecar the pipeline would reject, and validation errors surface
in the form that caused them.
"""
import os
import wx

try:
    from . import markings
    from . import markings_model as mm
    from . import board_index as board_index_mod
    from . import ui_model
except ImportError:
    import markings
    import markings_model as mm
    import board_index as board_index_mod
    import ui_model


def _label(parent, text, bold=False, wrap=0):
    st = wx.StaticText(parent, label=text)
    if bold:
        f = st.GetFont(); f.SetWeight(wx.FONTWEIGHT_BOLD); st.SetFont(f)
    if wrap:
        st.Wrap(wrap)
    return st


def _text(parent, value='', size=(90, -1)):
    tc = wx.TextCtrl(parent, value='' if value is None else str(value), size=size)
    return tc


def _choice(parent, choices, sel=''):
    ch = wx.Choice(parent, choices=list(choices))
    if sel in choices:
        ch.SetSelection(choices.index(sel))
    elif choices:
        ch.SetSelection(0)
    return ch


def _net_choice(parent, nets, sel=''):
    # A net picker that still shows an unmatched saved value (the
    # Rebind flow depends on seeing it).
    items = list(nets)
    if sel and sel not in items:
        items = [sel + '  (not on board)'] + items
    ch = wx.Choice(parent, choices=items)
    if sel:
        ch.SetSelection(0 if items and items[0].startswith(sel) else (items.index(sel) if sel in items else 0))
    return ch


def _net_value(choice_ctrl, nets):
    v = choice_ctrl.GetStringSelection()
    if v.endswith('  (not on board)'):
        v = v[:-len('  (not on board)')]
    return v.strip()


def _set_net_choice(ctrl, nets, value):
    if not value:
        return
    if value not in nets:
        # An unmatched saved net must survive the load (the rebind
        # flow depends on it): prepend it as '(not on board)', the
        # _net_choice convention, instead of letting the scan
        # settle on some other net (#206).
        ctrl.SetItems([value + '  (not on board)'] + list(nets))
        ctrl.SetSelection(0)
        return
    for i in range(ctrl.GetCount()):
        ctrl.SetSelection(i)
        if _net_value(ctrl, nets) == value:
            return


def solved_preview_diff(board_path, form, banned_layers):
    # The differential Solved-preview strip (Rev 3 wireframe):
    # tlcalc.solve_diff_geometry over the board stackup's top
    # surface layer (edge-coupled microstrip), computed from the
    # form's unsaved values.  Same degrade-honestly discipline as
    # the impedance strip.
    try:
        from . import worker as workermod
        from . import tlcalc
    except ImportError:
        import worker as workermod
        import tlcalc
    try:
        su = workermod.cmd_stackup(board_path, [])
    except Exception as e:
        return 'Solved preview unavailable (%s)' % e
    if not su.get('defined'):
        return ('No stackup defined on this board — the pair cannot be solved; '
                'the run will record the marking without sizing.')
    top = next((c for c in su.get('copper') or [] if c.get('class') == 'surface_top'), None)
    if top is None:
        return 'Solved preview unavailable (no surface layer in the stackup)'
    diel = top.get('diel_below') or {}
    h = diel.get('thickness_mm')
    er = form.get('dk') or diel.get('er')
    if not h or not er:
        return 'Solved preview unavailable (dielectric thickness/er missing)'
    t = top.get('thickness_mm') or 0.035
    try:
        zdiff = float(form.get('zdiff'))
    except (TypeError, ValueError):
        return 'Enter a target Zdiff to solve the pair geometry.'
    try:
        gap = float(form['gap_mm']) if form.get('gap_mm') not in (None, '') else None
        width = float(form['width_mm']) if form.get('width_mm') not in (None, '') else None
    except (TypeError, ValueError):
        return 'Solved preview unavailable (gap/width not numeric)'
    diff_of = lambda w, s: tlcalc.diff_microstrip(w, s, h, t, float(er))
    single_of = lambda w: tlcalc.microstrip(w, h, t, float(er), f_hz=0.0)
    try:
        r = tlcalc.solve_diff_geometry(diff_of, single_of, zdiff, gap_mm=gap, width_mm=width)
    except Exception as e:
        return 'Solved preview unavailable (%s)' % e
    if 'error' in r:
        return 'Solved preview unavailable (%s)' % r['error']
    return ('Solved: width %s mm · gap %s mm for Zdiff %s Ω on the top layer '
            '(preview — the run re-solves per layer).'
            % (r['width_mm'], r['gap_mm'], r['zdiff']))


def _num(tc):
    v = tc.GetValue().strip()
    return v if v else None


def solved_preview(board_path, form, banned_layers):
    # The impedance Solved-preview strip: the pipeline's own solve
    # (tlcalc over the board file's stackup, read by the worker's
    # cmd_stackup) for the top surface layer, computed from the
    # form's unsaved values.  Every failure mode degrades to an
    # honest message rather than a made-up number.
    try:
        from . import worker as workermod
        from . import tlcalc
    except ImportError:
        import worker as workermod
        import tlcalc
    try:
        su = workermod.cmd_stackup(board_path, [])
    except Exception as e:
        return 'Solved preview unavailable (%s)' % e
    if not su.get('defined'):
        return ('No stackup defined on this board — widths cannot be solved; '
                'the run will record the marking without sizing.')
    top = next((c for c in su.get('copper') or [] if c.get('class') == 'surface_top'), None)
    if top is None:
        return 'Solved preview unavailable (no surface layer in the stackup)'
    diel = top.get('diel_below') or {}
    h = diel.get('thickness_mm')
    er = diel.get('er')
    if not h or not er:
        return 'Solved preview unavailable (dielectric thickness/er missing)'
    t = top.get('thickness_mm') or 0.035
    try:
        z0 = float(form.get('z0'))
    except (TypeError, ValueError):
        return 'Enter a target Z0 to solve the width.'
    f = 0.0
    try:
        if form.get('freq_mhz'):
            f = float(form['freq_mhz']) * 1e6
    except (TypeError, ValueError):
        pass
    model = form.get('model') or 'microstrip'
    try:
        if model == 'coplanar':
            gap = form.get('gap_mm')
            if gap in (None, ''):
                return 'Coplanar needs the gap to the coplanar ground to solve.'
            fn = lambda w: tlcalc.coplanar(w, float(gap), h, t, er, f_hz=f, grounded=True)
            desc = 'coplanar, gap %s mm, h=%s mm' % (gap, h)
        else:
            fn = lambda w: tlcalc.microstrip(w, h, t, er, f_hz=f)
            desc = 'microstrip, h=%s mm' % h
        w = tlcalc.solve_width(lambda ww: fn(ww).z0, z0)
    except Exception as e:
        return 'Solved preview unavailable (%s)' % e
    if w is None:
        return 'Target Z0 unreachable in the width range on %s.' % top['name']
    line = 'Solved on %s (%s): width ≈ %.2f mm' % (top['name'], desc, w)
    if top['name'] in (banned_layers or set()):
        line += (' · ⚠ %s is banned by the current layer law — the engine '
                 'will use the next legal layer.' % top['name'])
    return line


# ------------------------------------------------------------ row editors
class _RowDialog(wx.Dialog):
    FIELDS = []  # (key, label, kind) kind in ('text', 'choice:...')

    def __init__(self, parent, title, row):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.ctrls = {}
        grid = wx.FlexGridSizer(0, 2, 6, 8)
        for key, label, kind in self.FIELDS:
            grid.Add(_label(self, label), 0, wx.ALIGN_CENTER_VERTICAL)
            if kind.startswith('choice:'):
                c = _choice(self, kind[len('choice:'):].split('|'), row.get(key) or '')
            else:
                c = _text(self, row.get(key), size=(160, -1))
            self.ctrls[key] = c
            grid.Add(c, 0, wx.EXPAND)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(grid, 0, wx.ALL, 10)
        btns = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        if btns:
            box.Add(btns, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(box)

    def row(self):
        return {k: (c.GetStringSelection() if isinstance(c, wx.Choice) else c.GetValue().strip())
                for k, c in self.ctrls.items()}


class LoadRowDialog(_RowDialog):
    FIELDS = [('ref', 'Ref', 'text'), ('kind', 'Kind', 'choice:|constant_current|resistive|constant_power'),
              ('peak_a', 'Peak (A)', 'text'), ('sustained_a', 'Sustained (A)', 'text'),
              ('duty', 'Duty (0\u20131)', 'text'), ('modes', 'Modes (csv)', 'text'),
              ('mpn', 'Datasheet MPN', 'text'),
              ('datasheet_rev', 'Datasheet rev', 'text')]

    def __init__(self, parent, row):
        super().__init__(parent, 'Load', row)


class RegulatorRowDialog(_RowDialog):
    FIELDS = [('ref', 'Ref', 'text'), ('type', 'Type', 'choice:|buck|ldo|switch'),
              ('eta_min', 'Min efficiency (buck)', 'text'), ('iq_a', 'Quiescent (A, LDO)', 'text')]

    def __init__(self, parent, row):
        super().__init__(parent, 'Regulator', row)


class ConduitRowDialog(_RowDialog):
    FIELDS = [('ref', 'Conduit ref (component)', 'text'), ('from', 'From ref', 'text'), ('to', 'To ref', 'text'), ('kind', 'Kind', 'text'),
              ('length_mm', 'Length (mm)', 'text'), ('width_mm', 'Width (mm)', 'text'),
              ('r_mohm', 'Resistance (m\u03a9)', 'text'), ('rating_a', 'Rating (A)', 'text')]

    def __init__(self, parent, row):
        super().__init__(parent, 'Conduit', row)


class ProtectiveRowDialog(_RowDialog):
    FIELDS = [('ref', 'Ref', 'text'), ('kind', 'Kind', 'text'),
              ('rating_a', 'Rating (A)', 'text'), ('note', 'Note', 'text')]

    def __init__(self, parent, row):
        super().__init__(parent, 'Protective device', row)


# ------------------------------------------------------------ entry forms
class _FormBase(wx.Panel):
    SECTION = ''

    def __init__(self, parent, editor):
        super().__init__(parent)
        self.editor = editor
        self.model = editor.model
        self.grid = wx.FlexGridSizer(0, 2, 6, 8)
        self.grid.AddGrowableCol(1)
        self.SetSizer(self.grid)

    def row(self, label, ctrl):
        self.grid.Add(_label(self, label), 0, wx.ALIGN_CENTER_VERTICAL)
        self.grid.Add(ctrl, 0, wx.EXPAND)
        return ctrl

    def nets(self):
        return self.model.board_nets()

    def get_entry(self):
        return mm.ENTRY_FOR[self.SECTION](self.form_dict())


class ImpedanceForm(_FormBase):
    SECTION = 'impedance'

    def __init__(self, parent, editor):
        super().__init__(parent, editor)
        self.net = self.row('Net', _net_choice(self, self.nets()))
        self.z0 = self.row('Target Z0 (\u03a9)', _text(self))
        self.role = self.row('Role', _choice(self, ['', 'tx', 'rx', 'gnss_lna', 'lna_input', 'generic']))
        self.model_c = self.row('Topology', _choice(self, ['', 'microstrip', 'coplanar']))
        self.gap = self.row('Coplanar gap (mm)', _text(self))
        self.dk_basis = self.row('Dk basis', _choice(self, ['', 'design', 'process', '1mhz', 'stackup']))
        self.mask = self.row('Solder mask', _choice(self, ['', 'covered', 'open']))
        self.dk = self.row('Dk override', _text(self))
        self.loss = self.row('Loss budget (dB)', _text(self))
        layers = self.model.board_index.get('layers') or []
        self.layers = wx.CheckListBox(self, choices=layers, size=(-1, 74))
        self.row('Layers', self.layers)
        self.freq = self.row('Frequency (MHz)', _text(self))
        self.fence = self.row('Via fence', wx.CheckBox(self, label='Stitch a via fence along the run'))
        self.smooth = self.row('Smooth', wx.CheckBox(self, label='Smooth the routed run'))
        self.preview = _label(self, '', wrap=430)
        self.grid.Add(wx.StaticText(self, label=''), 0)
        self.grid.Add(self.preview, 0, wx.EXPAND)
        pane = wx.CollapsiblePane(self, label='Optional / EMC \u2014 \u03c0-match (0 set)')
        pwin = pane.GetPane()
        pg = wx.FlexGridSizer(0, 2, 4, 8)
        pg.Add(_label(pwin, 'Radio-side refs (csv)'), 0)
        self.pi_radio = _text(pwin, size=(200, -1)); pg.Add(self.pi_radio, 0, wx.EXPAND)
        pg.Add(_label(pwin, 'Antenna-side refs (csv)'), 0)
        self.pi_antenna = _text(pwin, size=(200, -1)); pg.Add(self.pi_antenna, 0, wx.EXPAND)
        pg.Add(_label(pwin, 'Radio ref (single)'), 0)
        self.pi_radio_ref = _text(pwin, size=(200, -1)); pg.Add(self.pi_radio_ref, 0, wx.EXPAND)
        pg.Add(_label(pwin, 'Tolerance (%)'), 0)
        self.tol = _text(pwin, size=(200, -1)); pg.Add(self.tol, 0, wx.EXPAND)
        pwin.SetSizer(pg)
        self.grid.Add(wx.StaticText(self, label=''), 0)
        self.grid.Add(pane, 0, wx.EXPAND)
        self.pi_pane = pane
        self.model_c.Bind(wx.EVT_CHOICE, self._on_topo)
        self.z0.Bind(wx.EVT_TEXT, lambda e: self.refresh_preview())
        self._on_topo()

    def _on_topo(self, _evt=None):
        coplanar = self.model_c.GetStringSelection() == 'coplanar'
        self.gap.Enable(coplanar)
        self.fence.Enable(coplanar)
        self.refresh_preview()

    def form_dict(self):
        return {'net': _net_value(self.net, self.nets()), 'z0': _num(self.z0),
                'role': self.role.GetStringSelection(),
                'model': self.model_c.GetStringSelection(),
                'gap_mm': _num(self.gap), 'dk_basis': self.dk_basis.GetStringSelection(),
                'mask': self.mask.GetStringSelection(), 'freq_mhz': _num(self.freq),
                'dk': _num(self.dk), 'loss_budget_db': _num(self.loss),
                'layers': [self.layers.GetString(i) for i in self.layers.GetCheckedItems()],
                'fence': self.fence.GetValue(), 'smooth': self.smooth.GetValue(),
                'pi_radio': self.pi_radio.GetValue(), 'pi_antenna': self.pi_antenna.GetValue(),
                'pi_radio_ref': self.pi_radio_ref.GetValue(),
                'tol_pct': _num(self.tol)}

    def refresh_preview(self):
        try:
            self.preview.SetLabel(solved_preview(self.editor.board_path, self.form_dict(),
                                                 self.editor.banned_layers))
            self.preview.Wrap(430)
        except Exception:
            pass

    def set_entry(self, entry):
        f = mm.impedance_form(entry)
        _set_net_choice(self.net, self.nets(), f['net'])
        self.z0.SetValue('' if f['z0'] is None else str(f['z0']))
        self.role.SetStringSelection(f['role'])
        self.model_c.SetStringSelection(f['model'])
        self.gap.SetValue('' if f['gap_mm'] is None else str(f['gap_mm']))
        self.dk_basis.SetStringSelection(f['dk_basis'])
        self.mask.SetStringSelection(f['mask'])
        self.dk.SetValue('' if f['dk'] is None else str(f['dk']))
        self.loss.SetValue('' if f['loss_budget_db'] is None else str(f['loss_budget_db']))
        for i in range(self.layers.GetCount()):
            self.layers.Check(i, self.layers.GetString(i) in f['layers'])
        self.freq.SetValue('' if f['freq_mhz'] is None else str(f['freq_mhz']))
        self.fence.SetValue(f['fence'])
        self.smooth.SetValue(f['smooth'])
        self.pi_radio.SetValue(', '.join(f['pi_radio']))
        self.pi_antenna.SetValue(', '.join(f['pi_antenna']))
        self.pi_radio_ref.SetValue(f['pi_radio_ref'])
        self.tol.SetValue('' if f.get('tol_pct') is None else str(f['tol_pct']))
        n = sum(1 for v in (f['pi_radio'], f['pi_antenna'], f['pi_radio_ref']) if v)
        self.pi_pane.SetLabel('Optional / EMC \u2014 \u03c0-match (%d set)' % n)
        self._on_topo()


def _pair_candidates(nets, a):
    # Auto-pair hint: same stem, complementary suffix.
    suffixes = ('_DP', '_DN', '_P', '_N', 'DP', 'DN', '+', '-', 'P', 'N', 'M')
    for suf in suffixes:
        if a.endswith(suf):
            base = a[:-len(suf)]
            return [n for n in nets if n != a and any(
                n == base + s2 for s2 in suffixes if s2 != suf)]
    return []


class DifferentialForm(_FormBase):
    SECTION = 'differential'

    def __init__(self, parent, editor):
        super().__init__(parent, editor)
        self.name = self.row('Pair name', _text(self, size=(200, -1)))
        self.net_a = self.row('Net A', _net_choice(self, self.nets()))
        self.net_b = self.row('Net B', _net_choice(self, self.nets()))
        self.hint = _label(self, '', wrap=430)
        self.grid.Add(wx.StaticText(self, label=''), 0)
        self.grid.Add(self.hint, 0, wx.EXPAND)
        self.zdiff = self.row('Target Zdiff (\u03a9)', _text(self))
        self.freq = self.row('Frequency (MHz)', _text(self))
        self.gap = self.row('Pair gap (mm)', _text(self))
        self.width = self.row('Conductor width (mm)', _text(self))
        self.dk = self.row('Dk override', _text(self))
        layers = self.model.board_index.get('layers') or []
        self.layers = wx.CheckListBox(self, choices=layers, size=(-1, 74))
        self.row('Layers', self.layers)
        self.preview = _label(self, '', wrap=430)
        self.grid.Add(wx.StaticText(self, label=''), 0)
        self.grid.Add(self.preview, 0, wx.EXPAND)
        self.net_a.Bind(wx.EVT_CHOICE, self._on_a)
        for c in (self.zdiff, self.gap, self.width, self.dk):
            c.Bind(wx.EVT_TEXT, lambda e: self.refresh_preview())
        self._on_a()

    def _on_a(self, _evt=None):
        a = _net_value(self.net_a, self.nets())
        cands = _pair_candidates(self.nets(), a) if a else []
        self.hint.SetLabel('Auto-pair hint: %s' % cands[0] if cands else '')
        self.hint.Wrap(430)
        if cands and not _net_value(self.net_b, self.nets()):
            # Rev 3 wireframe: the candidate FILLS Net B (still
            # editable) -- it never replaces a chosen net.
            _set_net_choice(self.net_b, self.nets(), cands[0])
        self.refresh_preview()

    def refresh_preview(self):
        try:
            self.preview.SetLabel(solved_preview_diff(self.editor.board_path, self.form_dict(),
                                                      self.editor.banned_layers))
            self.preview.Wrap(430)
        except Exception:
            pass

    def form_dict(self):
        return {'name': self.name.GetValue().strip(),
                'net_a': _net_value(self.net_a, self.nets()),
                'net_b': _net_value(self.net_b, self.nets()),
                'zdiff': _num(self.zdiff), 'freq_mhz': _num(self.freq),
                'gap_mm': _num(self.gap), 'width_mm': _num(self.width),
                'dk': _num(self.dk),
                'layers': [self.layers.GetString(i) for i in self.layers.GetCheckedItems()]}

    def set_entry(self, entry):
        f = mm.differential_form(entry)
        self.name.SetValue(f['name'])
        for ctrl, val in ((self.net_a, f['net_a']), (self.net_b, f['net_b'])):
            _set_net_choice(ctrl, self.nets(), val)
        self.zdiff.SetValue('' if f['zdiff'] is None else str(f['zdiff']))
        self.freq.SetValue('' if f['freq_mhz'] is None else str(f['freq_mhz']))
        self.gap.SetValue('' if f['gap_mm'] is None else str(f['gap_mm']))
        self.width.SetValue('' if f['width_mm'] is None else str(f['width_mm']))
        self.dk.SetValue('' if f['dk'] is None else str(f['dk']))
        for i in range(self.layers.GetCount()):
            self.layers.Check(i, self.layers.GetString(i) in f['layers'])
        self._on_a()


class _SubTable(wx.Panel):
    # One power sub-table (loads / regulators / conduits /
    # protective): a list plus Add/Edit/Remove backed by a row
    # dialog.  Rows are plain dicts in the form's row shape.
    def __init__(self, parent, columns, dialog_cls, form):
        super().__init__(parent)
        self.dialog_cls = dialog_cls
        self.form = form
        self.rows = []
        self.list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(-1, 96))
        for i, (title, w) in enumerate(columns):
            self.list.InsertColumn(i, title, width=w)
        self.columns = [t for t, _w in columns]
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.add_b = wx.Button(self, label='Add\u2026', size=(74, -1))
        self.edit_b = wx.Button(self, label='Edit\u2026', size=(74, -1))
        self.del_b = wx.Button(self, label='Remove', size=(74, -1))
        for b in (self.add_b, self.edit_b, self.del_b):
            btns.Add(b, 0, wx.RIGHT, 6)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.list, 0, wx.EXPAND)
        box.Add(btns, 0, wx.TOP, 4)
        self.SetSizer(box)
        self.add_b.Bind(wx.EVT_BUTTON, lambda e: self._edit(None))
        self.edit_b.Bind(wx.EVT_BUTTON, lambda e: self._edit(self.list.GetFirstSelected()))
        self.del_b.Bind(wx.EVT_BUTTON, self._remove)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._edit(self.list.GetFirstSelected()))

    def _edit(self, idx):
        row = dict(self.rows[idx]) if idx is not None and idx >= 0 else {}
        dlg = self.dialog_cls(self, row)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            new = dlg.row()
        finally:
            dlg.Destroy()
        if idx is None or idx < 0:
            self.rows.append(new)
        else:
            self.rows[idx] = new
        self.refresh()

    def _remove(self, _evt):
        idx = self.list.GetFirstSelected()
        if idx >= 0:
            del self.rows[idx]
            self.refresh()

    def set_rows(self, rows):
        self.rows = [dict(r) for r in rows]
        self.refresh()

    def refresh(self):
        self.list.DeleteAllItems()
        for r in self.rows:
            vals = [self.form.cell(r, c) for c in self.columns]
            i = self.list.InsertItem(self.list.GetItemCount(), vals[0])
            for j, v in enumerate(vals[1:], 1):
                self.list.SetItem(i, j, v)


class PowerForm(_FormBase):
    SECTION = 'power'

    def cell(self, row, col):
        if col == 'State':
            try:
                _ref, spec = mm._load_entry(row)
                return 'UNKNOWN \u2014 resistances only' if mm.load_is_unknown(spec) else ''
            except Exception:
                return ''
        if col == 'KB ref':
            return row.get('mpn') or ''
        key = {'Ref': 'ref', 'Kind': 'kind', 'Type': 'type', 'Peak (A)': 'peak_a',
               'Sustained (A)': 'sustained_a', 'Duty': 'duty', 'From': 'from', 'To': 'to',
               'Rating (A)': 'rating_a', 'Eta min': 'eta_min', 'Iq (A)': 'iq_a',
               'R (m\u03a9)': 'r_mohm', 'Modes': 'modes'}.get(col, '')
        v = row.get(key)
        return '' if v is None else str(v)

    def __init__(self, parent, editor):
        super().__init__(parent, editor)
        cond = editor.conditions
        self.banner = _label(self, 'Power sizes only when BOTH conditions hold: schematic netlist %s \u00b7 defined stackup %s \u2014 otherwise the rail is recorded and reported, never sized.' % (
            '\u2713' if (cond.get('netlist') or cond.get('schematic')) else '\u2717',
            '\u2713' if cond.get('stackup') else '\u2717'), wrap=430)
        self.grid.Add(self.banner, 0, wx.EXPAND)
        self.grid.Add(wx.StaticText(self, label=''), 0)
        self.net = self.row('Net', _net_choice(self, self.nets()))
        self.has_v = self.row('Voltage', wx.CheckBox(self, label='Rail voltage known'))
        self.nominal = self.row('Nominal (V)', _text(self))
        self.vmin = self.row('Min (V)', _text(self))
        self.vmax = self.row('Max (V)', _text(self))
        self.root_ref = self.row('Source ref', _choice(self, ['']))
        self.root_pin = self.row('Pin', _text(self))
        self.grid.Add(_label(self, ''), 0)
        self.grid.Add(_label(self, 'Pin is carried in the sidecar; it is not read by the power planner \u2014 the root is the ref.', wrap=430), 0, wx.EXPAND)
        self.drop = self.row('Drop budget (V, this rail)', _text(self))
        self.grid.Add(_label(self, ''), 0)
        self.grid.Add(_label(self, 'Blank = the board-level power-corners budget applies.', wrap=430), 0, wx.EXPAND)
        self.grid.Add(_label(self, 'Loads', bold=True), 0)
        self.loads = _SubTable(self, [('Ref', 60), ('Kind', 100), ('Peak (A)', 64), ('Sustained (A)', 84), ('Duty', 44), ('Modes', 80), ('KB ref', 90), ('State', 190)], LoadRowDialog, self)
        self.grid.Add(self.loads, 0, wx.EXPAND)
        self.import_b = wx.Button(self, label='Import connected parts\u2026')
        self.grid.Add(wx.StaticText(self, label=''), 0)
        self.grid.Add(self.import_b, 0)
        self.grid.Add(_label(self, 'Regulators', bold=True), 0)
        self.regulators = _SubTable(self, [('Ref', 60), ('Type', 70), ('Eta min', 60), ('Iq (A)', 60)], RegulatorRowDialog, self)
        self.grid.Add(self.regulators, 0, wx.EXPAND)
        self.grid.Add(_label(self, 'Conduits', bold=True), 0)
        self.conduits = _SubTable(self, [('Ref', 50), ('From', 55), ('To', 55), ('Kind', 80), ('R (m\u03a9)', 60), ('Rating (A)', 64)], ConduitRowDialog, self)
        self.grid.Add(self.conduits, 0, wx.EXPAND)
        self.grid.Add(_label(self, 'Protective', bold=True), 0)
        self.protective = _SubTable(self, [('Ref', 60), ('Kind', 90), ('Rating (A)', 70)], ProtectiveRowDialog, self)
        self.grid.Add(self.protective, 0, wx.EXPAND)
        self.net.Bind(wx.EVT_CHOICE, self._on_net)
        self.import_b.Bind(wx.EVT_BUTTON, self._on_import)
        self._on_net()

    def _on_net(self, _evt=None):
        net = _net_value(self.net, self.nets())
        refs = [''] + [p['ref'] for p in self.model.connected_parts(net)] if net else ['']
        cur = self.root_ref.GetStringSelection()
        self.root_ref.SetItems(refs)
        if cur in refs:
            self.root_ref.SetStringSelection(cur)
        self.import_b.SetLabel('Import connected parts on %s\u2026' % (net or '<net>'))

    def _on_import(self, _evt):
        net = _net_value(self.net, self.nets())
        have = {r.get('ref') for r in self.loads.rows}
        added = 0
        for p in self.model.connected_parts(net):
            if p['ref'] not in have:
                self.loads.rows.append({'ref': p['ref'], 'kind': '', 'peak_a': None,
                                        'sustained_a': None, 'duty': None, 'mpn': '', 'datasheet_rev': ''})
                added += 1
        self.loads.refresh()
        self.editor.set_form_note('%d parts imported on %s \u2014 each arrives UNKNOWN until a current or a KB ref is given.' % (added, net))

    def form_dict(self):
        return {'net': _net_value(self.net, self.nets()), 'has_voltage': self.has_v.GetValue(),
                'nominal_v': _num(self.nominal), 'min_v': _num(self.vmin), 'max_v': _num(self.vmax),
                'root_ref': self.root_ref.GetStringSelection(), 'root_pin': self.root_pin.GetValue().strip(),
                'loads': self.loads.rows, 'regulators': self.regulators.rows,
                'conduits': self.conduits.rows, 'protective': self.protective.rows,
                'drop_budget_v': _num(self.drop)}

    def get_entry(self):
        return mm.power_entry(self.form_dict())

    def set_entry(self, entry):
        f = mm.power_form(entry)
        self.net.SetStringSelection(f['net'])
        self.has_v.SetValue(f['has_voltage'])
        self.nominal.SetValue('' if f['nominal_v'] is None else str(f['nominal_v']))
        self.vmin.SetValue('' if f['min_v'] is None else str(f['min_v']))
        self.vmax.SetValue('' if f['max_v'] is None else str(f['max_v']))
        self._on_net()
        self.root_ref.SetStringSelection(f['root_ref'])
        self.root_pin.SetValue(f['root_pin'])
        self.drop.SetValue('' if f['drop_budget_v'] is None else str(f['drop_budget_v']))
        self.loads.set_rows(f['loads'])
        self.regulators.set_rows(f['regulators'])
        self.conduits.set_rows(f['conduits'])
        self.protective.set_rows(f['protective'])


class LengthForm(_FormBase):
    SECTION = 'length_groups'

    def __init__(self, parent, editor):
        super().__init__(parent, editor)
        self.name = self.row('Group name', _text(self, size=(200, -1)))
        radios = wx.BoxSizer(wx.HORIZONTAL)
        self.rb_nets = wx.RadioButton(self, label='Net checklist', style=wx.RB_GROUP)
        self.rb_pair = wx.RadioButton(self, label='Differential pair')
        radios.Add(self.rb_nets, 0, wx.RIGHT, 10)
        radios.Add(self.rb_pair, 0)
        self.grid.Add(_label(self, 'Define members by'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.grid.Add(radios, 0)
        self.nets_cl = wx.CheckListBox(self, choices=self.nets(), size=(-1, 110))
        self.row('Member nets', self.nets_cl)
        self.pair = self.row('Pair', _choice(self, [''] + self.model.diff_pair_names()))
        self.resolved = _label(self, '', wrap=430)
        self.grid.Add(wx.StaticText(self, label=''), 0)
        self.grid.Add(self.resolved, 0, wx.EXPAND)
        self.mode = self.row('Target mode', _choice(self, ['longest', 'length', 'delay']))
        self.target = self.row('Target value', _text(self))
        tolrow = wx.BoxSizer(wx.HORIZONTAL)
        self.tol = _text(self)
        self.tol_unit = _choice(self, ['mm', 'ps'])
        tolrow.Add(self.tol, 0, wx.RIGHT, 6)
        tolrow.Add(self.tol_unit, 0)
        self.grid.Add(_label(self, 'Tolerance'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.grid.Add(tolrow, 0)
        self.meander = wx.CheckListBox(self, choices=self.nets(), size=(-1, 74))
        self.row('Meander allowed', self.meander)
        self.pi = wx.CheckListBox(self, choices=self.nets(), size=(-1, 74))
        self.row('\u03c0-match members', self.pi)
        self.rb_nets.Bind(wx.EVT_RADIOBUTTON, self._on_mode)
        self.rb_pair.Bind(wx.EVT_RADIOBUTTON, self._on_mode)
        self.mode.Bind(wx.EVT_CHOICE, self._on_target_mode)
        self.pair.Bind(wx.EVT_CHOICE, self._on_pair)
        self._on_mode(); self._on_target_mode()

    def _on_mode(self, _evt=None):
        by_pair = self.rb_pair.GetValue()
        self.nets_cl.Enable(not by_pair)
        self.pair.Enable(by_pair)
        self._on_pair()

    def _on_pair(self, _evt=None):
        if self.rb_pair.GetValue():
            nets = self.model.diff_pair_nets(self.pair.GetStringSelection())
            self.resolved.SetLabel('Resolves to nets: %s' % (', '.join(nets) if nets else '\u2014'))
            self.resolved.Wrap(430)
        else:
            self.resolved.SetLabel('')

    def _on_target_mode(self, _evt=None):
        m = self.mode.GetStringSelection()
        self.target.Enable(m in ('length', 'delay'))
        if m == 'length':
            self.tol_unit.SetStringSelection('mm'); self.tol_unit.Disable()
        elif m == 'delay':
            self.tol_unit.SetStringSelection('ps'); self.tol_unit.Disable()
        else:
            self.tol_unit.Enable()

    @staticmethod
    def _checked(cl):
        return [cl.GetString(i) for i in cl.GetCheckedItems()]

    def form_dict(self):
        return {'name': self.name.GetValue().strip(),
                'members_mode': 'pair' if self.rb_pair.GetValue() else 'nets',
                'nets': self._checked(self.nets_cl), 'pair': self.pair.GetStringSelection(),
                'target_mode': self.mode.GetStringSelection(),
                'target_value': _num(self.target), 'tolerance': _num(self.tol),
                'tolerance_unit': self.tol_unit.GetStringSelection(),
                'meander_nets': self._checked(self.meander), 'pi_nets': self._checked(self.pi)}

    def set_entry(self, entry):
        f = mm.length_form(entry)
        self.name.SetValue(f['name'])
        (self.rb_pair if f['members_mode'] == 'pair' else self.rb_nets).SetValue(True)
        for i in range(self.nets_cl.GetCount()):
            self.nets_cl.Check(i, self.nets_cl.GetString(i) in f['nets'])
        self.pair.SetStringSelection(f['pair'])
        self.mode.SetStringSelection(f['target_mode'])
        self.target.SetValue('' if f['target_value'] is None else str(f['target_value']))
        self.tol.SetValue('' if f['tolerance'] is None else str(f['tolerance']))
        self.tol_unit.SetStringSelection(f['tolerance_unit'])
        for i in range(self.meander.GetCount()):
            self.meander.Check(i, self.meander.GetString(i) in f['meander_nets'])
            self.pi.Check(i, self.pi.GetString(i) in f['pi_nets'])
        self._on_mode(); self._on_target_mode()


class HVForm(_FormBase):
    SECTION = 'hv_domains'

    def __init__(self, parent, editor):
        super().__init__(parent, editor)
        self.name = self.row('Domain name', _text(self, size=(200, -1)))
        self.peak = self.row('Peak voltage (V, signed)', _text(self))
        self.ac = self.row('Waveform', wx.CheckBox(self, label='AC (peak is the waveform peak)'))
        self.coating = self.row('Coating', _choice(self, ['', 'uncoated', 'polymer', 'conformal']))
        self.pollution = self.row('Pollution degree', _choice(self, ['', '1', '2', '3']))
        self.altitude = self.row('Altitude (m)', _text(self))
        self.insulation = self.row('Insulation (IEC)', _choice(self, ['', 'functional', 'basic', 'supplementary', 'reinforced']))
        self.matgroup = self.row('Material group (CTI)', _choice(self, ['', 'I', 'II', 'IIIa', 'IIIb']))
        self.rms = self.row('RMS voltage (V)', _text(self))
        self.minclr = self.row('Min clearance (mm)', _text(self))
        self.mincrp = self.row('Min creepage (mm)', _text(self))
        self.materialise = self.row('Materialise', wx.CheckBox(self, label='Stamp keepouts for this domain'))
        self.members = wx.CheckListBox(self, choices=self.nets(), size=(-1, 110))
        self.row('Member nets', self.members)
        warn = _label(self, mm.SAME_DOMAIN_WARNING, wrap=430)
        warn.SetForegroundColour(wx.Colour(176, 96, 0))
        self.grid.Add(wx.StaticText(self, label=''), 0)
        self.grid.Add(warn, 0, wx.EXPAND)
        self.grid.Add(_label(self, 'Pairwise spacing preview', bold=True), 0, wx.TOP, 6)
        self.pairs = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(-1, 110))
        self.pairs.InsertColumn(0, 'Domain A', width=170)
        self.pairs.InsertColumn(1, 'Domain B', width=170)
        self.pairs.InsertColumn(2, '\u0394V', width=70)
        self.pairs.InsertColumn(3, 'Required (mm)', width=100)
        self.grid.Add(self.pairs, 0, wx.EXPAND)
        self.name.Bind(wx.EVT_TEXT, lambda e: self.refresh_pairs())
        self.peak.Bind(wx.EVT_TEXT, lambda e: self.refresh_pairs())
        self.coating.Bind(wx.EVT_CHOICE, lambda e: self.refresh_pairs())
        self.altitude.Bind(wx.EVT_TEXT, lambda e: self.refresh_pairs())
        self.refresh_pairs()

    def refresh_pairs(self):
        # Reflects the form's live values (#206): the domain being
        # edited is substituted into the model's rows before
        # pairing (mm.hv_pairwise_rows' `extra`).
        extra = None
        try:
            fd = self.form_dict()
            if fd.get('name'):
                extra = {'name': fd['name'],
                         'peak_v': float(fd['peak_v']) if fd.get('peak_v') not in (None, '') else 0.0,
                         'coating': fd.get('coating') or 'uncoated',
                         'altitude_m': float(fd['altitude_m']) if fd.get('altitude_m') not in (None, '') else 0,
                         'nets': fd.get('nets') or []}
        except Exception:
            extra = None
        self.pairs.DeleteAllItems()
        for r in mm.hv_pairwise_rows(self.model, extra=extra):
            i = self.pairs.InsertItem(self.pairs.GetItemCount(), r['a'])
            self.pairs.SetItem(i, 1, r['b'])
            self.pairs.SetItem(i, 2, '%s V' % r['delta_v'])
            self.pairs.SetItem(i, 3, '%.2f' % r['required_mm'])

    def form_dict(self):
        return {'name': self.name.GetValue().strip(),
                'nets': [self.members.GetString(i) for i in self.members.GetCheckedItems()],
                'peak_v': _num(self.peak), 'ac': self.ac.GetValue(),
                'coating': self.coating.GetStringSelection(),
                'pollution_degree': self.pollution.GetStringSelection(),
                'altitude_m': _num(self.altitude), 'materialise': self.materialise.GetValue(),
                'insulation': self.insulation.GetStringSelection(),
                'material_group': self.matgroup.GetStringSelection(),
                'rms_v': _num(self.rms), 'min_clearance_mm': _num(self.minclr),
                'min_creepage_mm': _num(self.mincrp)}

    def set_entry(self, entry):
        f = mm.hv_form(entry)
        self.name.SetValue(f['name'])
        self.peak.SetValue('' if f['peak_v'] is None else str(f['peak_v']))
        self.ac.SetValue(f['ac'])
        self.coating.SetStringSelection(f['coating'])
        self.pollution.SetStringSelection('' if f['pollution_degree'] is None else str(f['pollution_degree']))
        self.altitude.SetValue('' if f['altitude_m'] is None else str(f['altitude_m']))
        self.materialise.SetValue(f['materialise'])
        self.insulation.SetStringSelection(f.get('insulation') or '')
        self.matgroup.SetStringSelection(f.get('material_group') or '')
        self.rms.SetValue('' if f.get('rms_v') is None else str(f['rms_v']))
        self.minclr.SetValue('' if f.get('min_clearance_mm') is None else str(f['min_clearance_mm']))
        self.mincrp.SetValue('' if f.get('min_creepage_mm') is None else str(f['min_creepage_mm']))
        for i in range(self.members.GetCount()):
            self.members.Check(i, self.members.GetString(i) in f['nets'])
        self.refresh_pairs()


class QuietForm(_FormBase):
    SECTION = 'quiet'

    def __init__(self, parent, editor):
        super().__init__(parent, editor)
        radios = wx.BoxSizer(wx.HORIZONTAL)
        self.rb_net = wx.RadioButton(self, label='Net', style=wx.RB_GROUP)
        self.rb_ref = wx.RadioButton(self, label='Component ref')
        radios.Add(self.rb_net, 0, wx.RIGHT, 10)
        radios.Add(self.rb_ref, 0)
        self.grid.Add(_label(self, 'Mark by'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.grid.Add(radios, 0)
        self.net = self.row('Net', _net_choice(self, self.nets()))
        self.ref = self.row('Component ref', _choice(self, [''] + self.model.footprint_refs()))
        self.resolved = _label(self, '', wrap=430)
        self.grid.Add(wx.StaticText(self, label=''), 0)
        self.grid.Add(self.resolved, 0, wx.EXPAND)
        self.profile = self.row('Profile', _choice(self, ['crystal', 'analog', 'reference']))
        self.iso = self.row('Isolation (mm)', _text(self))
        self.guard = self.row('Guard net', _text(self))
        self.allow = self.row('Allowed nets (csv)', _text(self, size=(240, -1)))
        self.ground = self.row('Ground nets (csv)', _text(self, size=(240, -1)))
        self.materialise = self.row('Materialise', wx.CheckBox(self, label='Stamp the keepout'))
        note = _label(self, 'Off: the run still checks and reports intrusions; the keepout is not stamped.', wrap=430)
        self.grid.Add(wx.StaticText(self, label=''), 0)
        self.grid.Add(note, 0, wx.EXPAND)
        self.rb_net.Bind(wx.EVT_RADIOBUTTON, self._on_mode)
        self.rb_ref.Bind(wx.EVT_RADIOBUTTON, self._on_mode)
        self.ref.Bind(wx.EVT_CHOICE, self._on_mode)
        self._on_mode()

    def _on_mode(self, _evt=None):
        by_ref = self.rb_ref.GetValue()
        self.net.Enable(not by_ref)
        self.ref.Enable(by_ref)
        if by_ref:
            nets = self.model.nets_for_ref(self.ref.GetStringSelection())
            self.resolved.SetLabel('Resolves to nets: %s' % (', '.join(nets) if nets else '\u2014'))
        else:
            self.resolved.SetLabel('')
        self.resolved.Wrap(430)

    def form_dict(self):
        return {'mode': 'ref' if self.rb_ref.GetValue() else 'net',
                'net': _net_value(self.net, self.nets()),
                'ref': self.ref.GetStringSelection(),
                'profile': self.profile.GetStringSelection(),
                'isolation_mm': _num(self.iso), 'guard_net': self.guard.GetValue().strip(),
                'allow_nets': self.allow.GetValue(), 'ground_nets': self.ground.GetValue(),
                'materialise': self.materialise.GetValue()}

    def set_entry(self, entry):
        f = mm.quiet_form(entry)
        (self.rb_ref if f['mode'] == 'ref' else self.rb_net).SetValue(True)
        _set_net_choice(self.net, self.nets(), f['net'])
        self.ref.SetStringSelection(f['ref'])
        self.profile.SetStringSelection(f['profile'])
        self.iso.SetValue('' if f['isolation_mm'] is None else str(f['isolation_mm']))
        self.guard.SetValue(f['guard_net'])
        self.allow.SetValue(', '.join(f['allow_nets']))
        self.ground.SetValue(', '.join(f['ground_nets']))
        self.materialise.SetValue(f['materialise'])
        self._on_mode()


class CornersForm(wx.Panel):
    # Board-level power corners (By Group & Board view).
    FIELDS = [('ambient_c', 'Ambient (\u00b0C)'), ('delta_t_c', 'Allowed rise \u0394T (\u00b0C)'),
              ('drop_budget_v', 'Drop budget (V)'), ('width_increment_mm', 'Width increment (mm)'),
              ('etch_tol_mm', 'Etch tolerance (mm)'), ('foil_tol_frac', 'Foil tolerance (fraction)'),
              ('plating_um', 'Plating (\u00b5m)')]

    def __init__(self, parent, editor):
        super().__init__(parent)
        self.editor = editor
        self.model = editor.model
        self.ctrls = {}
        grid = wx.FlexGridSizer(0, 2, 6, 8)
        for key, label in self.FIELDS:
            grid.Add(_label(self, label), 0, wx.ALIGN_CENTER_VERTICAL)
            self.ctrls[key] = _text(self)
            grid.Add(self.ctrls[key], 0)
        self.SetSizer(grid)
        self.refresh()

    def form_dict(self):
        # Raw control strings -- apply() parses these same values;
        # the dialog's pending-edit tracking compares them (#205).
        return {key: self.ctrls[key].GetValue() for key, _l in self.FIELDS}

    def refresh(self):
        corners = self.model.power_corners()
        for key, _l in self.FIELDS:
            self.ctrls[key].SetValue('' if corners.get(key) is None else str(corners[key]))

    def apply(self):
        corners = {}
        for key, _l in self.FIELDS:
            v = self.ctrls[key].GetValue().strip()
            if v:
                try:
                    corners[key] = float(v)
                except ValueError:
                    raise ValueError('%s must be a number' % key)
        self.model.set_power_corners(corners)


class RulesPanel(wx.Panel):
    # #42 read-only display (parked ruling: no apply/report switch).
    def __init__(self, parent, editor):
        super().__init__(parent)
        self.model = editor.model
        box = wx.BoxSizer(wx.VERTICAL)
        fps = self.model.rule_footprints()
        if fps:
            lines = ['Rule-carrying footprints on this board:']
            for f in fps:
                props = ' \u00b7 '.join('%s = %s' % kv for kv in sorted(f['properties'].items()))
                lines.append('  %s \u2014 %s' % (f['ref'], props))
        else:
            lines = ['No footprints on this board carry AIRule* properties.']
        lines.append('')
        lines.append('Defaults in use: library defaults (decap 2.0 mm, edge clearance 5.0 mm, class by FPID) \u2014 shown read-only.')
        lines.append('The placement audit runs with the route; its verdict appears in the post-run report. Whether shipped rules apply by default is a parked ruling (#42) \u2014 this panel never changes them.')
        tc = wx.TextCtrl(self, value='\n'.join(lines), style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 220))
        box.Add(tc, 1, wx.EXPAND)
        self.SetSizer(box)


FORM_CLASSES = {'impedance': ImpedanceForm, 'differential': DifferentialForm,
                'power': PowerForm, 'length_groups': LengthForm,
                'hv_domains': HVForm, 'quiet': QuietForm}
_BADGES = {'impedance': 'Z0', 'differential': 'DIFF', 'power': 'PWR',
           'length_groups': 'LEN', 'hv_domains': 'HV', 'quiet': 'QUIET'}


class MarkingsEditorDialog(wx.Dialog):
    def __init__(self, parent, board_path, data, index, banned_layers=(), conditions=None):
        super().__init__(parent, title='Markings \u2014 %s' % os.path.basename(board_path),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.board_path = board_path
        self.model = mm.MarkingsEditorModel(data, index)
        self.conditions = conditions or mm.evaluate_conditions(board_path, data)
        self.banned_layers = set(banned_layers or ())
        self.saved = False
        self.view = 'net'
        self.filter = 'marked'
        self.selection = None      # {'kind': 'entry'|'new'|'corners'|'rules', ...}
        self.form = None
        self._pending = mm.PendingEdits()   # #205: un-applied form edits
        self._list_sel = -1                 # list row whose form is showing
        self._sel_guard = False
        self._rows = []            # row descriptors parallel to list items
        top = wx.BoxSizer(wx.HORIZONTAL)
        self.view_net = wx.RadioButton(self, label='By Net', style=wx.RB_GROUP)
        self.view_group = wx.RadioButton(self, label='By Group & Board')
        top.Add(self.view_net, 0, wx.RIGHT, 8)
        top.Add(self.view_group, 0, wx.RIGHT, 16)
        self.pill_marked = wx.RadioButton(self, label='Marked nets', style=wx.RB_GROUP)
        self.pill_all = wx.RadioButton(self, label='All nets')
        self.pill_issues = wx.RadioButton(self, label='Issues')
        for p in (self.pill_marked, self.pill_all, self.pill_issues):
            top.Add(p, 0, wx.RIGHT, 8)
        self.search = wx.SearchCtrl(self, size=(170, -1))
        self.search.SetDescriptiveText('Search nets\u2026')
        top.Add(self.search, 0, wx.LEFT, 8)
        mid = wx.BoxSizer(wx.HORIZONTAL)
        self.list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(330, -1))
        self.list.InsertColumn(0, 'Net', width=130)
        self.list.InsertColumn(1, 'Markings', width=120)
        self.list.InsertColumn(2, 'Status', width=76)
        mid.Add(self.list, 0, wx.EXPAND | wx.RIGHT, 8)
        right = wx.BoxSizer(wx.VERTICAL)
        self.entry_pick = wx.Choice(self, choices=[])
        self.entry_pick.Hide()
        right.Add(self.entry_pick, 0, wx.EXPAND | wx.BOTTOM, 6)
        self.form_host = wx.Panel(self)
        self.form_sizer = wx.BoxSizer(wx.VERTICAL)
        self.form_host.SetSizer(self.form_sizer)
        right.Add(self.form_host, 1, wx.EXPAND)
        self.form_note = _label(self, '', wrap=430)
        right.Add(self.form_note, 0, wx.EXPAND | wx.TOP, 6)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.apply_b = wx.Button(self, label='Apply')
        self.add_b = wx.Button(self, label='Add marking \u25be')
        self.del_b = wx.Button(self, label='Delete marking')
        self.rebind_b = wx.Button(self, label='Rebind net\u2026')
        self.rebind_b.Hide()
        for b in (self.apply_b, self.add_b, self.del_b, self.rebind_b):
            btns.Add(b, 0, wx.RIGHT, 6)
        right.Add(btns, 0, wx.TOP, 8)
        mid.Add(right, 1, wx.EXPAND)
        bottom = wx.BoxSizer(wx.HORIZONTAL)
        self.status_label = _label(self, '')
        bottom.Add(self.status_label, 1, wx.ALIGN_CENTER_VERTICAL)
        self.save_b = wx.Button(self, label='Save')
        self.close_b = wx.Button(self, label='Close')
        bottom.Add(self.save_b, 0, wx.RIGHT, 6)
        bottom.Add(self.close_b, 0)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(top, 0, wx.EXPAND | wx.ALL, 8)
        box.Add(mid, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        box.Add(bottom, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(box)
        ca = wx.GetClientDisplayRect()
        w, h, min_w, min_h = ui_model.capped_open_size(1040, 680, ca.width, ca.height)
        self.SetMinSize((min_w, min_h))
        self.SetSize(wx.Size(w, h))
        self.view_net.Bind(wx.EVT_RADIOBUTTON, lambda e: self._set_view('net'))
        self.view_group.Bind(wx.EVT_RADIOBUTTON, lambda e: self._set_view('group'))
        self.pill_marked.Bind(wx.EVT_RADIOBUTTON, lambda e: self._set_filter('marked'))
        self.pill_all.Bind(wx.EVT_RADIOBUTTON, lambda e: self._set_filter('all'))
        self.pill_issues.Bind(wx.EVT_RADIOBUTTON, lambda e: self._set_filter('issues'))
        self.search.Bind(wx.EVT_TEXT, self._on_search)
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_select)
        self.entry_pick.Bind(wx.EVT_CHOICE, self._on_entry_pick)
        self.apply_b.Bind(wx.EVT_BUTTON, self._on_apply)
        self.add_b.Bind(wx.EVT_BUTTON, self._on_add_menu)
        self.del_b.Bind(wx.EVT_BUTTON, self._on_delete)
        self.rebind_b.Bind(wx.EVT_BUTTON, self._on_rebind)
        self.save_b.Bind(wx.EVT_BUTTON, self._on_save)
        self.close_b.Bind(wx.EVT_BUTTON, lambda e: self._guard_close())
        self.Bind(wx.EVT_CLOSE, lambda e: self._guard_close())
        if not index:
            self.set_form_note('Board index unavailable \u2014 net pickers are empty.')
        self.refresh_list()

    # ---------------------------------------------------------- list mgmt
    def set_form_note(self, text):
        self.form_note.SetLabel(text)
        self.form_note.Wrap(430)

    def _set_view(self, view):
        if view == self.view:
            return
        if not self._confirm_pending():
            # #215: refresh_list resets _list_sel, which would
            # strand the active form (a later Cancel could no
            # longer restore the list selection to its row).
            # Put the radios back and stay.
            self.view_net.SetValue(self.view == 'net')
            self.view_group.SetValue(self.view == 'group')
            return
        self.view = view
        self.refresh_list()

    def _set_filter(self, f):
        if f == self.filter:
            return
        if not self._confirm_pending():
            self.pill_marked.SetValue(self.filter == 'marked')
            self.pill_all.SetValue(self.filter == 'all')
            self.pill_issues.SetValue(self.filter == 'issues')
            return
        self.filter = f
        self.refresh_list()

    def _on_search(self, _evt):
        if self.search.GetValue() and self.filter == 'marked':
            self.filter = 'all'
            self.pill_all.SetValue(True)
        self.refresh_list()

    def _badges(self, net):
        out = []
        for s, i in self.model.marked_nets().get(net, []):
            e = self.model.entries(s)[i]
            b = _BADGES[s]
            if s == 'impedance' and e.get('z0_ohm') is not None:
                b = 'Z0 %s\u03a9' % e['z0_ohm']
            out.append(b)
        return ' \u00b7 '.join(out)

    def refresh_list(self):
        self._list_sel = -1
        marked = self.model.marked_nets()
        issues = self.model.issues()
        self.pill_marked.SetLabel('Marked nets (%d)' % len(marked))
        self.pill_all.SetLabel('All nets (%d)' % len(self.model.board_nets()))
        self.pill_issues.SetLabel('Issues (%d)' % len(issues))
        self.list.DeleteAllItems()
        self._rows = []
        q = self.search.GetValue().strip().lower()
        if self.view == 'group':
            self.list.SetColumn(0, wx.ListItem())  # keep columns; titles differ per view
            for s in ('length_groups', 'hv_domains', 'differential'):
                for i, e in enumerate(self.model.entries(s)):
                    label = '%s \u2014 %s' % (mm.SECTION_TITLES[s], self.model.entry_label(s, e))
                    self._add_row(label, '%d nets' % len(self.model.entry_nets(s, e)), '',
                                  {'kind': 'entry', 'section': s, 'index': i})
            self._add_row('Power corners (board level)', '%d set' % len(self.model.power_corners()), '',
                          {'kind': 'corners'})
            self._add_row('Library rules (#42)', '%d rule footprints' % len(self.model.rule_footprints()),
                          'read-only', {'kind': 'rules'})
            self._restore_selection()
            return
        if self.filter == 'issues':
            for iss in issues:
                for missing in iss['missing']:
                    self._add_row(missing, mm.SECTION_TITLES[iss['section']], 'Unmatched',
                                  {'kind': 'entry', 'section': iss['section'], 'index': iss['index'],
                                   'missing': missing})
            self._restore_selection()
            return
        if self.filter == 'marked':
            nets = sorted(marked)
        else:
            nets = sorted(set(self.model.board_nets()) | set(marked))
        have = set(self.model.board_nets())
        for n in nets:
            if q and q not in n.lower():
                continue
            status = '' if n in have else 'Not on board'
            self._add_row(n, self._badges(n), status,
                          {'kind': 'net', 'net': n})
        self._restore_selection()

    def _restore_selection(self):
        # #221: refresh_list rebuilds the rows on every search
        # keystroke (and on view/filter changes); without this,
        # _list_sel stayed -1 while the form kept showing, so the
        # list highlight desynced from the form and a later Cancel
        # in _confirm_pending restored the selection to nowhere.
        # Re-match the shown selection against the rebuilt rows
        # and restore its highlight.  Non-modal by design -- this
        # runs per keystroke.  A selection whose row was filtered
        # out is NOT resurrected: _list_sel stays -1.
        sel = self.selection
        if not sel:
            return
        marked = self.model.marked_nets()
        for idx, desc in enumerate(self._rows):
            if self._row_matches(desc, sel, marked):
                self._list_sel = idx
                self._sel_guard = True
                try:
                    self.list.Select(idx)
                    self.list.Focus(idx)
                finally:
                    self._sel_guard = False
                return

    @staticmethod
    def _row_matches(desc, sel, marked):
        if desc.get('kind') == sel.get('kind'):
            if sel.get('kind') == 'entry':
                return (desc.get('section') == sel.get('section')
                        and desc.get('index') == sel.get('index')
                        and desc.get('missing') == sel.get('missing'))
            return True      # corners / rules: the kind is the row
        # A net row stands for the markings picked on it: the
        # shown entry restores onto its own net's row.
        if desc.get('kind') == 'net' and sel.get('kind') == 'entry':
            if sel.get('net') is not None:
                # #228: the entry was shown from a specific net
                # row -- restore to THAT row.  A multi-net entry
                # (pair / group / domain) sits in every one of
                # its nets' marked lists, so matching on the
                # entry alone lands on the first of its rows.
                return (desc.get('net') == sel.get('net')
                        and (sel.get('section'), sel.get('index'))
                        in marked.get(desc.get('net'), []))
            return (sel.get('section'), sel.get('index')) in marked.get(desc.get('net'), [])
        return False

    def _add_row(self, c0, c1, c2, desc):
        i = self.list.InsertItem(self.list.GetItemCount(), c0)
        self.list.SetItem(i, 1, c1)
        self.list.SetItem(i, 2, c2)
        self._rows.append(desc)

    # ---------------------------------------------------------- selection / forms
    def _show_form(self, form):
        if self.form is not None:
            self.form_sizer.Detach(self.form)
            self.form.Destroy()
        self.form = form
        if form is not None:
            self.form_sizer.Add(form, 1, wx.EXPAND)
        self.form_sizer.Layout()
        self.form_host.Layout()
        # #205: snapshot the freshly shown form -- any later
        # difference is an un-applied (pending) edit.
        if form is not None and hasattr(form, 'form_dict'):
            try:
                self._pending.shown(form.form_dict())
            except Exception:
                self._pending.cleared()
        else:
            self._pending.cleared()

    def _show_entry(self, section, index, missing=None):
        self.selection = {'kind': 'entry', 'section': section, 'index': index, 'missing': missing}
        form = FORM_CLASSES[section](self.form_host, self)
        form.set_entry(self.model.entries(section)[index])
        self._show_form(form)
        self.rebind_b.Show(bool(missing))
        self.Layout()

    # ------------------------------------------------- pending edits (#205)
    def _form_pending(self):
        if self.form is None or not hasattr(self.form, 'form_dict'):
            return False
        try:
            return self._pending.pending(self.form.form_dict())
        except Exception:
            return False

    def _confirm_pending(self):
        # True when leaving the active form loses nothing: no
        # pending edits, or the user applied / discarded them.
        if not self._form_pending():
            return True
        dlg = wx.MessageDialog(self, 'The current form has edits that have not been applied.',
                               'Markings', wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION)
        dlg.SetYesNoLabels('Apply', 'Discard')
        try:
            r = dlg.ShowModal()
        finally:
            dlg.Destroy()
        if r == wx.ID_YES:
            return bool(self._on_apply(None))
        if r == wx.ID_NO:
            self._pending.cleared()
            return True
        return False

    def _on_select(self, evt):
        if self._sel_guard:
            return
        idx = evt.GetIndex()
        if idx >= 0 and not self._confirm_pending():
            # Stay on the form that is showing: restore the list
            # selection to its row.
            self._sel_guard = True
            try:
                if self._list_sel >= 0:
                    self.list.Select(self._list_sel)
                    self.list.Focus(self._list_sel)
            finally:
                self._sel_guard = False
            return
        if idx >= 0:
            self._list_sel = idx
        if idx < 0 or idx >= len(self._rows):
            return
        desc = self._rows[idx]
        self.entry_pick.Hide()
        if desc['kind'] == 'net':
            marks = self.model.marked_nets().get(desc['net'], [])
            if not marks:
                self.selection = None
                self._show_form(None)
                self.set_form_note('No markings on %s \u2014 use Add marking \u25be to create one.' % desc['net'])
                return
            if len(marks) > 1:
                self.entry_pick.SetItems(['%s \u2014 %s' % (mm.SECTION_TITLES[s],
                                            self.model.entry_label(s, self.model.entries(s)[i]))
                                          for s, i in marks])
                self.entry_pick.SetSelection(0)
                self.entry_pick.Show()
                self.Layout()
            s, i = marks[0]
            self._show_entry(s, i)
            if self.selection:
                # #228: remember which net row the entry was
                # shown from, so a list refresh restores there.
                self.selection['net'] = desc['net']
        elif desc['kind'] == 'entry':
            self._show_entry(desc['section'], desc['index'], desc.get('missing'))
        elif desc['kind'] == 'corners':
            self.selection = {'kind': 'corners'}
            self._show_form(CornersForm(self.form_host, self))
            self.rebind_b.Hide()
        elif desc['kind'] == 'rules':
            self.selection = {'kind': 'rules'}
            self._show_form(RulesPanel(self.form_host, self))
            self.rebind_b.Hide()

    def _on_entry_pick(self, _evt):
        # The selected net carries several markings: switch forms.
        if not self._confirm_pending():
            sel = self.list.GetFirstSelected()
            if 0 <= sel < len(self._rows) and self.selection:
                marks = self.model.marked_nets().get(self._rows[sel].get('net'), [])
                for k, (s, i) in enumerate(marks):
                    if s == self.selection.get('section') and i == self.selection.get('index'):
                        self.entry_pick.SetSelection(k)
                        break
            return
        sel = self.list.GetFirstSelected()
        if sel < 0:
            return
        desc = self._rows[sel]
        if desc['kind'] != 'net':
            return
        marks = self.model.marked_nets().get(desc['net'], [])
        k = self.entry_pick.GetSelection()
        if 0 <= k < len(marks):
            s, i = marks[k]
            self._show_entry(s, i)
            if self.selection:
                self.selection['net'] = desc['net']

    # ---------------------------------------------------------- actions
    def _on_apply(self, _evt):
        if self.form is None or self.selection is None:
            return False
        try:
            if isinstance(self.form, CornersForm):
                self.form.apply()
                self._pending.shown(self.form.form_dict())
                self.set_form_note('Applied \u2713 (unsaved until Save)')
                return True
            if isinstance(self.form, RulesPanel):
                return True
            entry = self.form.get_entry()
        except ValueError as e:
            self.set_form_note('Not applied: %s' % e)
            return False
        section = self.selection['section']
        if self.selection['kind'] == 'new':
            index = self.model.add_entry(section, entry)
        else:
            index = self.selection['index']
            self.model.update_entry(section, index, entry)
        err = self.model.validate_working()
        self.set_form_note('Applied \u2713 (unsaved until Save)' if not err
                           else 'Applied, but the sidecar does not validate yet: %s' % err)
        _net = self.selection.get('net')
        self.selection = {'kind': 'entry', 'section': section, 'index': index}
        if _net is not None:
            self.selection['net'] = _net
        self.refresh_list()
        self._show_entry(section, index)
        return True

    def _on_add_menu(self, _evt):
        menu = wx.Menu()
        items = {}
        for s in mm.SECTIONS:
            items[menu.Append(wx.ID_ANY, mm.SECTION_TITLES[s]).GetId()] = s
        choice = {}

        def pick(evt):
            choice['s'] = items.get(evt.GetId())
        menu.Bind(wx.EVT_MENU, pick)
        self.PopupMenu(menu)
        menu.Destroy()
        if choice.get('s'):
            self._new_entry(choice['s'])

    def _new_entry(self, section):
        if not self._confirm_pending():
            return
        nets = self.model.board_nets()
        blank = {'impedance': {'net': nets[0] if nets else '', 'z0_ohm': 50.0},
                 'differential': {'name': '', 'nets': []},
                 'power': {'net': nets[0] if nets else ''},
                 'length_groups': {'name': '', 'target_mode': 'longest', 'tolerance_mm': 1.0},
                 'hv_domains': {'name': '', 'nets': [], 'peak_v': 0.0},
                 'quiet': {'profile': 'crystal', 'isolation_mm': 1.0}}[section]
        self.selection = {'kind': 'new', 'section': section}
        self.entry_pick.Hide()
        form = FORM_CLASSES[section](self.form_host, self)
        form.set_entry(blank)
        self._show_form(form)
        self.rebind_b.Hide()
        self.set_form_note('New %s marking \u2014 fill the form and Apply.' % mm.SECTION_TITLES[section])

    def _on_delete(self, _evt):
        if not self.selection or self.selection.get('kind') != 'entry':
            return
        self.model.remove_entry(self.selection['section'], self.selection['index'])
        self.selection = None
        self._show_form(None)
        self.set_form_note('Marking removed (unsaved until Save; Close without saving to undo).')
        self.refresh_list()

    def _on_rebind(self, _evt):
        if not self.selection or not self.selection.get('missing'):
            return
        if not self._confirm_pending():
            return
        dlg = wx.SingleChoiceDialog(self, 'Rebind %s to which board net?' % self.selection['missing'],
                                    'Rebind net', self.model.board_nets())
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            new = dlg.GetStringSelection()
        finally:
            dlg.Destroy()
        self.model.rebind(self.selection['section'], self.selection['index'],
                          self.selection['missing'], new)
        self.set_form_note('Rebound %s \u2192 %s (unsaved until Save).' % (self.selection['missing'], new))
        self.refresh_list()
        self._show_entry(self.selection['section'], self.selection['index'])

    def _on_save(self, _evt=None):
        if self._form_pending() and not self._on_apply(None):
            return False
        err = self.model.validate_working()
        if err:
            wx.MessageBox('The markings do not validate, so nothing was saved:\n\n%s' % err,
                          'Markings', wx.OK | wx.ICON_ERROR)
            return False
        path = self.model.save(mm.sidecar_path(self.board_path))
        self.saved = True
        self.status_label.SetLabel('Saved \u2713 \u2014 %s' % path)
        return True

    def _guard_close(self):
        if self.model.dirty or self._form_pending():
            dlg = wx.MessageDialog(self, 'You have unsaved marking changes.', 'Markings',
                                   wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION)
            dlg.SetYesNoCancelLabels('Save', 'Discard', 'Cancel')
            try:
                r = dlg.ShowModal()
            finally:
                dlg.Destroy()
            if r == wx.ID_CANCEL:
                return
            if r == wx.ID_YES and not self._on_save():
                return
        self.EndModal(wx.ID_OK if self.saved else wx.ID_CANCEL)


# ------------------------------------------------------------ report dialog
def _pan_board_to(pcbnew, board, x_mm, y_mm):
    """Focus the editor view on (x_mm, y_mm) of the open board.
    KiCad 10's pcbnew has no FocusOnLocation (#218) -- focus the
    nearest board item instead (vias / short track endpoints,
    footprint pads),
    or a transient shape at the point when nothing is within
    2 mm.  Returns True only when the focus calls succeed; the
    caller must not claim a pan otherwise."""
    target = pcbnew.VECTOR2I(int(x_mm * 1e6), int(y_mm * 1e6))
    best = [None, (2.0e6) ** 2]  # 2 mm catchment, board units (nm)

    def consider(item, pt):
        d2 = (pt.x - target.x) ** 2 + (pt.y - target.y) ** 2
        if d2 < best[1]:
            best[0], best[1] = item, d2

    arc_t = getattr(pcbnew, 'PCB_ARC_T', None)
    for t in board.GetTracks():
        if arc_t is not None and hasattr(t, 'Type'):
            try:
                if t.Type() == arc_t:
                    # #229: an arc is not its chord -- a
                    # near-closed arc's endpoints can sit <4 mm
                    # apart while FocusOnItem centres on the
                    # arc, far from the marked point (the #220
                    # class, curved).  Arcs fall through to the
                    # pads / transient shape like long tracks do.
                    continue
            except Exception:
                pass
        st, en = t.GetStart(), t.GetEnd()
        if st.x == en.x and st.y == en.y:
            consider(t, st)      # via: the item IS the point
        elif (st.x - en.x) ** 2 + (st.y - en.y) ** 2 <= (4.0e6) ** 2:
            consider(t, st)
            consider(t, en)
        # A longer track is not a point (#220): FocusOnItem
        # centres on the item, so focusing it would pan to its
        # midpoint -- a marked point near one end falls through
        # to the transient shape centred on the point instead.
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            consider(pad, pad.GetPosition())
    item = best[0]
    if item is None:
        item = pcbnew.PCB_SHAPE(board)
        item.SetShape(pcbnew.SHAPE_T_CIRCLE)
        item.SetCenter(target)
        item.SetRadius(int(0.5e6))
    pcbnew.FocusOnItem(item)
    pcbnew.Refresh()
    return True


class ReportDialog(wx.Dialog):
    def __init__(self, parent, summary, board_path, result_board, ledger_path):
        super().__init__(parent, title='Engineered nets \u2014 run report',
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.summary = summary
        self.board_path = board_path
        self.result_board = result_board
        self.ledger_path = ledger_path
        box = wx.BoxSizer(wx.VERTICAL)
        bar = _label(self, mm.verdict_bar_text(summary['counts']), bold=True)
        box.Add(bar, 0, wx.ALL, 8)
        self.list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(640, 260))
        self.list.InsertColumn(0, 'Marking', width=190)
        self.list.InsertColumn(1, 'Verdict', width=90)
        self.list.InsertColumn(2, 'Detail', width=350)
        for r in summary['rows']:
            d = r['detail']
            if r.get('at'):
                d += ' (%.1f, %.1f)' % r['at']
            i = self.list.InsertItem(self.list.GetItemCount(), r['marking'])
            self.list.SetItem(i, 1, r['verdict'])
            self.list.SetItem(i, 2, d)
        box.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.note = _label(self, '', wrap=620)
        box.Add(self.note, 0, wx.EXPAND | wx.ALL, 8)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.locate_b = wx.Button(self, label='Locate on board')
        self.copy_b = wx.Button(self, label='Copy (x, y)')
        self.md_b = wx.Button(self, label='Export Markdown\u2026')
        self.html_b = wx.Button(self, label='Export HTML\u2026')
        self.ledger_b = wx.Button(self, label='Open ledger.json')
        self.ledger_b.Enable(bool(ledger_path and os.path.exists(ledger_path)))
        close_b = wx.Button(self, label='Close')
        for b in (self.locate_b, self.copy_b, self.md_b, self.html_b, self.ledger_b, close_b):
            btns.Add(b, 0, wx.RIGHT, 6)
        box.Add(btns, 0, wx.ALL, 8)
        self.SetSizer(box)
        self.SetSize(wx.Size(680, 440))
        self.locate_b.Bind(wx.EVT_BUTTON, self._on_locate)
        self.copy_b.Bind(wx.EVT_BUTTON, self._on_copy)
        self.md_b.Bind(wx.EVT_BUTTON, lambda e: self._on_export('md'))
        self.html_b.Bind(wx.EVT_BUTTON, lambda e: self._on_export('html'))
        self.ledger_b.Bind(wx.EVT_BUTTON, self._on_ledger)
        close_b.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_OK))

    def _selected(self):
        i = self.list.GetFirstSelected()
        if i < 0:
            return None
        return self.summary['rows'][i]

    def _on_copy(self, _evt):
        r = self._selected()
        if not r or not r.get('at'):
            self.note.SetLabel('Select a row with coordinates first.')
            return
        txt = '(%.1f, %.1f)' % r['at']
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(txt))
            wx.TheClipboard.Close()
        self.note.SetLabel('Copied %s' % txt)

    def _on_locate(self, _evt):
        r = self._selected()
        if not r or not r.get('at'):
            self.note.SetLabel('Select a row with coordinates first.')
            return
        x, y = r['at']
        open_name = ''
        panned = False
        try:
            import pcbnew
            b = pcbnew.GetBoard()
            if b is not None:
                open_name = b.GetFileName() or ''
                try:
                    # #218: FocusOnLocation does not exist in
                    # KiCad 10 -- pan via the nearest item, and
                    # claim the pan only when it actually happened.
                    panned = _pan_board_to(pcbnew, b, x, y)
                except Exception:
                    panned = False
        except Exception:
            open_name = ''
        is_result = bool(open_name and self.result_board
                         and os.path.abspath(open_name) == os.path.abspath(self.result_board))
        is_input = bool(open_name
                        and os.path.abspath(open_name) == os.path.abspath(self.board_path))
        if panned and is_result:
            self.note.SetLabel('Panned open board to (%.1f, %.1f)' % (x, y))
        elif panned and is_input:
            self.note.SetLabel('Panned open board to (%.1f, %.1f) \u2014 open the result board to inspect routed copper' % (x, y))
        elif is_result or is_input:
            self.note.SetLabel('Could not pan the open board to (%.1f, %.1f) \u2014 the coordinates are in the row above; Copy (x, y) puts them on the clipboard.' % (x, y))
        else:
            self.note.SetLabel('No matching board open in the editor \u2014 coordinates (%.1f, %.1f) are in the row above; Copy (x, y) puts them on the clipboard.' % (x, y))
        self.note.Wrap(620)

    def _on_export(self, fmt):
        dlg = wx.FileDialog(self, 'Export report', wildcard='Markdown (*.md)|*.md' if fmt == 'md' else 'HTML (*.html)|*.html',
                            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        finally:
            dlg.Destroy()
        text = (mm.report_markdown(self.summary['rows'], self.summary['counts'])
                if fmt == 'md' else mm.report_html(self.summary['rows'], self.summary['counts']))
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        self.note.SetLabel('Exported to %s' % path)

    def _on_ledger(self, _evt):
        if self.ledger_path:
            wx.LaunchDefaultApplication(self.ledger_path)
