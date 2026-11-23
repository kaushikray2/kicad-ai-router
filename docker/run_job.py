#!/usr/bin/env python3
"""One routing job, run as a subprocess by server.py. Drives the existing
ai_router Pipeline + orchestrator on the job's input board; writes the
terminal status to <job-dir>/status.json and the result board beside it."""
import argparse, json, os, subprocess, sys, time

def find_plugin():
    cands = [os.path.dirname(os.path.abspath(__file__)),
             os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
    if os.environ.get('AIR_PLUGIN_PATH'): cands.insert(0, os.environ['AIR_PLUGIN_PATH'])
    for c in cands:
        if os.path.isfile(os.path.join(c, 'ai_router', 'pipeline.py')):
            return c
    raise RuntimeError('ai_router package not found in %s' % cands)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--job-dir', required=True)
    a = ap.parse_args()
    jd = a.job_dir
    status_p = os.path.join(jd, 'status.json')
    rec = json.load(open(status_p)) if os.path.exists(status_p) else {}
    def write_status(**kw):
        rec.update(kw)
        tmp = status_p + '.tmp'
        with open(tmp, 'w') as f: json.dump(rec, f, indent=1)
        os.replace(tmp, status_p)
    try:
        cfgj = json.load(open(os.path.join(a.data, 'config', 'config.json')))
        llm = cfgj.get('llm', {}); rt = dict(cfgj.get('routing', {}))
        rt.update(rec.get('overrides') or {})
        tm_ver = (cfgj.get('tracemaker') or {}).get('selected_version', '')
        if not tm_ver:
            tm_dir = os.path.join(a.data, 'tracemaker')
            installed = [d for d in sorted(os.listdir(tm_dir))
                         if os.path.isfile(os.path.join(tm_dir, d, 'bin', 'tracemaker'))] if os.path.isdir(tm_dir) else []
            if len(installed) == 1: tm_ver = installed[0]
        tm_exe = os.path.join(a.data, 'tracemaker', tm_ver, 'bin', 'tracemaker')
        if not os.path.isfile(tm_exe):
            raise RuntimeError('selected TraceMaker %r not installed' % tm_ver)
        kc = subprocess.run(['kicad-cli', '--version'], capture_output=True, text=True, timeout=30).stdout.strip()
        # pinning: the resolved absolute path + versions are fixed for this run
        write_status(tracemaker_version=tm_ver, tracemaker_path=tm_exe, kicad_cli=kc)
        cfg = {'endpoint': llm.get('endpoint', ''), 'model': llm.get('model', ''),
               'api_key': llm.get('api_key', ''), 'tracemaker_path': tm_exe,
               'mode': rt.get('mode', 'auto'), 'max_passes': int(rt.get('max_passes', 4)),
               'work': int(rt.get('work', 20000000)), 'no_tracks_on': rt.get('no_tracks_on', 'In1.Cu'),
               'strip_existing': bool(rt.get('strip_existing', True)), 'kicad_cli': 'kicad-cli',
               'finish_signal_cap': int(rt.get('finish_signal_cap', 8)),
               'finish_pour_cap': int(rt.get('finish_pour_cap', 4)),
               'long_finish_cap': int(rt.get('long_finish_cap', 6)),
               'ilp_repair_cap': int(rt.get('ilp_repair_cap', 0)), 'attempt_select': bool(rt.get('attempt_select', False)),
               'layer_law_policy': rt.get('layer_law_policy', 'auto'),
               'first_nets_policy': rt.get('first_nets_policy', 'auto')}
        sys.path.insert(0, find_plugin())
        sys.path.insert(0, os.path.join(find_plugin(), 'ai_router'))
        from pipeline import Pipeline
        import orchestrator
        def log(m): print('[job] ' + str(m), flush=True)
        pipe = Pipeline(cfg, os.path.join(jd, 'input.kicad_pcb'), log)
        res = orchestrator.run_auto(cfg, pipe, log) if cfg['mode'] == 'auto' else orchestrator.run_llm(cfg, pipe, log)
        final = res.get('final_board')
        if final and os.path.exists(final):
            import shutil; shutil.copy(final, os.path.join(jd, 'result.kicad_pcb'))
            # v1.8.1 (#8): serve the WINNING pipeline's ledger -- after a
            # layer-law escalation two workdirs exist and the later one
            # is not necessarily the variant that was kept.
            _led = os.path.join(os.path.dirname(final), 'ledger.json')
            if os.path.isfile(_led): shutil.copy(_led, os.path.join(jd, 'ledger.json'))
        gate = res.get('gate') or {}
        write_status(status='done', gate={k: gate.get(k) for k in ('unconnected_total', 'pour_items', 'split_nets', 'split_signal_nets', 'split_pour_nets', 'pour_nets', 'violations_total', 'copper_violations', 'vias', 'wirelength_mm', 'banned_layer_tracks', 'tracks_by_layer')})
    except Exception as e:
        import traceback; traceback.print_exc()
        write_status(status='error', error=str(e)[:500])

if __name__ == '__main__':
    main()
