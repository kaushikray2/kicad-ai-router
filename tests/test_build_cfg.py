"""N1/#43 + N14/#56: backend job config is built from the plugin
DEFAULTS (single source), with defensive parsing and the engine
whitelist. Pure python."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'docker'))
import run_job

def test_defaults_reach_backend_jobs():
    cfg = run_job.build_cfg({}, {}, '/tm/bin/tracemaker')
    assert cfg['engine_portfolio'] == 'tm_relax'      # shipped default since v1.9.5
    assert cfg['reroute_accept'] == 'connectivity'    # not the dead 'faults' fallback
    assert cfg['max_passes'] == 4

def test_server_and_job_overrides_win():
    cfg = run_job.build_cfg({'max_passes': 2, 'engine_portfolio': 'krt'}, {}, '/tm')
    assert cfg['max_passes'] == 2 and cfg['engine_portfolio'] == 'krt'

def test_engine_whitelist_dedupe_cap():
    cfg = run_job.build_cfg({'engine_portfolio': 'tm_relax,tm_relax,bogus,krt,freerouting,tm_global,tm_escape'}, {}, '/tm')
    assert cfg['engine_portfolio'] == 'tm_relax,krt,freerouting,tm_global'

def test_non_numeric_does_not_crash():
    cfg = run_job.build_cfg({'max_passes': 'lots', 'work': 'plenty'}, {}, '/tm')
    assert cfg['max_passes'] == 4 and cfg['work'] == 20000000
