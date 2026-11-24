"""wx dialog for the AI Router plugin: configuration + run + live log."""
import os, subprocess, threading
import wx
try:
    from . import config as cfgmod
    from .pipeline import Pipeline
    from . import orchestrator
    from . import backend_client
except ImportError:
    import config as cfgmod
    from pipeline import Pipeline
    import orchestrator
    import backend_client

class Cancelled(Exception):
    pass

class RouterDialog(wx.Dialog):
    def __init__(self, parent, board_path):
        super().__init__(parent, title='AI Router — LLM-orchestrated routing', size=(760, 640))
        self.cfg = cfgmod.load()
        self.cancel_event = threading.Event()
        self.worker_thread = None
        self.result = None
        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(0, 2, 6, 8)
        grid.AddGrowableCol(1, 1)
        def add(label, ctrl):
            grid.Add(wx.StaticText(panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)
        self.board_ctrl = wx.TextCtrl(panel, value=board_path or '')
        add('Board file', self.board_ctrl)
        self.endpoint_ctrl = wx.TextCtrl(panel, value=self.cfg['endpoint'])
        add('LLM endpoint (OpenAI-compatible)', self.endpoint_ctrl)
        self.model_ctrl = wx.TextCtrl(panel, value=self.cfg['model'])
        add('Model', self.model_ctrl)
        self.key_ctrl = wx.TextCtrl(panel, value=self.cfg.get('api_key', ''), style=wx.TE_PASSWORD)
        add('API key (if needed)', self.key_ctrl)
        self.tm_ctrl = wx.TextCtrl(panel, value=self.cfg['tracemaker_path'])
        add('TraceMaker binary', self.tm_ctrl)
        self.mode_ctrl = wx.Choice(panel, choices=['llm', 'auto'])
        self.mode_ctrl.SetStringSelection(self.cfg.get('mode', 'auto'))  # v1.9.6 (#40): fallback matches config DEFAULTS
        add('Mode (llm = model decides, auto = rule-based)', self.mode_ctrl)
        self.passes_ctrl = wx.SpinCtrl(panel, value=str(self.cfg.get('max_passes', 4)), min=1, max=8)
        add('Max route passes', self.passes_ctrl)
        self.layers_ctrl = wx.TextCtrl(panel, value=self.cfg.get('no_tracks_on', 'In1.Cu'))  # v1.9.6 (#40)
        add('No tracks on layers', self.layers_ctrl)
        self.strip_ctrl = wx.CheckBox(panel, label='Strip existing routing into the working copy first')
        self.strip_ctrl.SetValue(bool(self.cfg.get('strip_existing', True)))
        add('', self.strip_ctrl)
        self.backend_ctrl = wx.CheckBox(panel, label='Run on the Docker backend instead of locally')
        self.backend_ctrl.SetValue(bool(self.cfg.get('use_backend', False)))
        add('Backend', self.backend_ctrl)
        self.backend_url_ctrl = wx.TextCtrl(panel, value=self.cfg.get('backend_url', 'http://127.0.0.1:8731'))
        add('Backend URL', self.backend_url_ctrl)
        self.backend_tok_ctrl = wx.TextCtrl(panel, value=self.cfg.get('backend_token', ''), style=wx.TE_PASSWORD)
        add('Backend token', self.backend_tok_ctrl)
        vbox.Add(grid, 0, wx.EXPAND | wx.ALL, 10)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.run_btn = wx.Button(panel, label='Run routing pipeline')
        self.cancel_btn = wx.Button(panel, label='Cancel')
        self.cancel_btn.Disable()
        self.open_btn = wx.Button(panel, label='Open result board')
        self.open_btn.Disable()
        self.page_btn = wx.Button(panel, label='Backend config page')
        btns.Add(self.run_btn, 0, wx.RIGHT, 8)
        btns.Add(self.cancel_btn, 0, wx.RIGHT, 8)
        btns.Add(self.open_btn, 0)
        btns.Add(self.page_btn, 0, wx.LEFT, 8)
        vbox.Add(btns, 0, wx.LEFT, 10)
        self.log_ctrl = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        vbox.Add(self.log_ctrl, 1, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(vbox)
        self.run_btn.Bind(wx.EVT_BUTTON, self.on_run)
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.open_btn.Bind(wx.EVT_BUTTON, self.on_open)
        self.page_btn.Bind(wx.EVT_BUTTON, self.on_page)

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

    def collect_cfg(self):
        self.cfg['endpoint'] = self.endpoint_ctrl.GetValue().strip()
        self.cfg['model'] = self.model_ctrl.GetValue().strip()
        self.cfg['api_key'] = self.key_ctrl.GetValue()
        self.cfg['tracemaker_path'] = self.tm_ctrl.GetValue().strip()
        self.cfg['mode'] = self.mode_ctrl.GetStringSelection()
        self.cfg['max_passes'] = self.passes_ctrl.GetValue()
        self.cfg['no_tracks_on'] = self.layers_ctrl.GetValue().strip()
        self.cfg['strip_existing'] = self.strip_ctrl.GetValue()
        self.cfg['use_backend'] = self.backend_ctrl.GetValue()
        self.cfg['backend_url'] = self.backend_url_ctrl.GetValue().strip()
        self.cfg['backend_token'] = self.backend_tok_ctrl.GetValue()
        cfgmod.save(self.cfg)
        return self.cfg

    def on_run(self, _evt):
        board = self.board_ctrl.GetValue().strip()
        if not board or not os.path.exists(board):
            wx.MessageBox('Board file not found: %s' % board, 'AI Router', wx.OK | wx.ICON_ERROR)
            return
        cfg = self.collect_cfg()
        self.cancel_event.clear()
        self.run_btn.Disable(); self.cancel_btn.Enable(); self.open_btn.Disable()
        def work():
            try:
                if cfg.get('use_backend'):
                    self.result = self._run_backend(cfg, board)
                elif cfg['mode'] == 'llm':
                    pipe = Pipeline(cfg, board, self.log)
                    self.result = orchestrator.run_llm(cfg, pipe, self.log)
                else:
                    pipe = Pipeline(cfg, board, self.log)
                    self.result = orchestrator.run_auto(cfg, pipe, self.log)
                g = (self.result or {}).get('gate') or {}
                self.log('=== DONE === final board: %s' % (self.result or {}).get('final_board'))
                self.log('Final gate: unconnected=%s pour=%s split_nets=%s violations=%s' % (
                    g.get('unconnected_total'), g.get('pour_items'), g.get('split_nets'), g.get('violations_total')))
                wx.CallAfter(self.open_btn.Enable)
            except Cancelled:
                self._log_ui('Cancelled by user (takes effect at step boundaries).')
            except Exception as e:
                self._log_ui('ERROR: %s' % e)
            finally:
                wx.CallAfter(self.run_btn.Enable)
                wx.CallAfter(self.cancel_btn.Disable)
        self.worker_thread = threading.Thread(target=work, daemon=True)
        self.worker_thread.start()

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

    def _run_backend(self, cfg, board):
        url = (cfg.get('backend_url') or 'http://127.0.0.1:8731').rstrip('/')
        tok = cfg.get('backend_token', '')
        h = backend_client.health(url)
        self.log('Backend %s - KiCad %s - TraceMaker selected: %s (installed: %s)' % (
            h.get('backend_version'), h.get('kicad_cli'), h.get('tracemaker_selected') or 'none',
            ', '.join(h.get('tracemaker_versions') or [])))
        overrides = {'mode': cfg['mode'], 'max_passes': cfg['max_passes'],
                     'no_tracks_on': cfg['no_tracks_on'], 'strip_existing': cfg['strip_existing']}
        jid = backend_client.submit(url, tok, board, overrides)
        self.log('Submitted job %s - routing runs on the backend; this window just watches.' % jid)
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
            tail = job.get('log_tail') or ''
            if tail != last_tail:
                for line in tail.splitlines()[-3:]:
                    if line.strip():
                        self.log('  ' + line)
                last_tail = tail
            if st == 'done':
                dest = os.path.join(os.path.dirname(os.path.abspath(board)),
                                    os.path.splitext(os.path.basename(board))[0] + '-backend.kicad_pcb')
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
            if st in ('error', 'cancelled', 'interrupted'):
                raise RuntimeError('backend job %s: %s' % (st, job.get('error') or ''))
            threading.Event().wait(5)
