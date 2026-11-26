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

"""Issue #192: the #173 bootstrap picked the STORED config URL
over the deployment's TRACEMAKER_URL, so upgrading an existing
volume re-installed the old engine on every restart (reproduced
end-to-end: pinned env + old stored URL -> bootstrap 'installed
0.12.1' from the old tarball, recorded sha unchanged). Edges:
tm_update never selected a pinned install over an older
selected_version, and tm_pinned_install_missing trusted the
recorded sha map instead of the installed versions.
Run: python3 tests/test_tm_pinned_bootstrap.py"""
import hashlib, importlib.machinery, importlib.util, io, os, shutil, tarfile, tempfile

ROOT = os.path.join(os.path.dirname(__file__), '..')

def _fake_tarball(path, marker, provenance=None):
    exe = '#!/bin/sh\necho "tracemaker 0.12.1 (%s)"\n' % marker
    with tarfile.open(path, 'w:gz') as t:
        def add(name, data, mode=0o644):
            b = data.encode(); ti = tarfile.TarInfo(name); ti.size = len(b); ti.mode = mode
            t.addfile(ti, io.BytesIO(b))
        add('tracemaker-0.12.1-linux-x86_64/bin/tracemaker', exe, 0o755)
        if provenance is not None:
            add('tracemaker-0.12.1-linux-x86_64/PROVENANCE.txt', provenance)
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()

def _load_server(data_dir):
    os.environ['AIR_DATA_DIR'] = data_dir
    ld = importlib.machinery.SourceFileLoader('air_server_uut192', os.path.join(ROOT, 'docker', 'server.py'))
    sp = importlib.util.spec_from_loader('air_server_uut192', ld)
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    return m

def _install_tree(data, version, marker):
    b = os.path.join(data, 'tracemaker', version, 'bin'); os.makedirs(b)
    exe = os.path.join(b, 'tracemaker')
    with open(exe, 'w') as f: f.write('#!/bin/sh\necho "tracemaker %s (%s)"\n' % (version, marker))
    os.chmod(exe, 0o755)

def _scenario():
    d = tempfile.mkdtemp(); data = os.path.join(d, 'data')
    _install_tree(data, '0.12.1', 'fake-old')
    old_tb = os.path.join(d, 'old.tar.gz'); old_sha = _fake_tarball(old_tb, 'fake-old')
    pin_tb = os.path.join(d, 'pinned.tar.gz'); pin_sha = _fake_tarball(pin_tb, 'fake-pinned', 'tag: phase-2-end\n')
    os.environ['TRACEMAKER_PINNED_SHA256'] = pin_sha
    os.environ['TRACEMAKER_URL'] = 'file://' + pin_tb
    srv = _load_server(data)
    cfg = srv.load_config()
    cfg['tracemaker']['sha256'] = {'0.12.1': old_sha}
    cfg['tracemaker']['selected_version'] = '0.12.1'
    cfg['tracemaker']['url'] = 'file://' + old_tb
    srv.save_config(cfg)
    return srv, data, old_tb, pin_tb, pin_sha

def test_bootstrap_url_prefers_env_when_pin_missing():
    srv, data, old_tb, pin_tb, pin_sha = _scenario()
    cfg = srv.load_config()
    assert srv.tm_pinned_install_missing(cfg) is True
    url = srv.tm_bootstrap_url(cfg)
    assert url == 'file://' + pin_tb, url
    # and driving the bootstrap with it actually installs the pin
    srv.tm_update(url)
    cfg = srv.load_config()
    assert cfg['tracemaker']['sha256']['0.12.1'] == pin_sha
    assert 'phase-2-end' in srv.tm_provenance(cfg)

def test_pinned_install_selects_its_version():
    srv, data, old_tb, pin_tb, pin_sha = _scenario()
    _install_tree(data, '0.11.0', 'fake-older')
    cfg = srv.load_config()
    cfg['tracemaker']['selected_version'] = '0.11.0'
    srv.save_config(cfg)
    srv.tm_update('file://' + pin_tb)
    cfg = srv.load_config()
    assert cfg['tracemaker']['selected_version'] == '0.12.1', cfg['tracemaker']

def test_missing_check_uses_installed_versions():
    srv, data, old_tb, pin_tb, pin_sha = _scenario()
    _install_tree(data, '0.11.0', 'fake-older')
    cfg = srv.load_config()
    cfg['tracemaker']['sha256'] = {'0.12.1': pin_sha, '0.11.0': 'e' * 64}
    srv.save_config(cfg)
    # the record claims the pin is installed for 0.12.1; delete the
    # tree while 0.11.0 remains -> the pin IS missing
    shutil.rmtree(os.path.join(data, 'tracemaker', '0.12.1'))
    assert srv.tm_versions() == ['0.11.0']
    assert srv.tm_pinned_install_missing(srv.load_config()) is True

if __name__ == '__main__':
    test_bootstrap_url_prefers_env_when_pin_missing()
    test_pinned_install_selects_its_version()
    test_missing_check_uses_installed_versions()
    print('tm pinned bootstrap OK')
