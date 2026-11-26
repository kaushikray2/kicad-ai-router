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

"""Issue #173: a pinned TraceMaker build that shares its version
string with an older build could not be installed in-band -- the
bootstrap only fires on an empty volume (`if not tm_versions()`),
so an existing "0.12.1" install silently kept the old binary, and
tm_update TOFU-refused the pinned tarball for the same version.
The Dockerfile pin (archive sha256) is trusted instead: bootstrap
fires when the pinned build is not the installed one, and tm_update
accepts the pinned tarball for its version while non-pinned
mismatches stay refused.
Run: python3 tests/test_tm_pinned_upgrade.py"""
import hashlib, importlib.machinery, importlib.util, io, os, tarfile, tempfile

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
    ld = importlib.machinery.SourceFileLoader('air_server_uut', os.path.join(ROOT, 'docker', 'server.py'))
    sp = importlib.util.spec_from_loader('air_server_uut', ld)
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    return m


def _scenario():
    d = tempfile.mkdtemp()
    data = os.path.join(d, 'data')
    old_bin = os.path.join(data, 'tracemaker', '0.12.1', 'bin')
    os.makedirs(old_bin)
    with open(os.path.join(old_bin, 'tracemaker'), 'w') as f:
        f.write('#!/bin/sh\necho "tracemaker 0.12.1 (fake-old)"\n')
    os.chmod(os.path.join(old_bin, 'tracemaker'), 0o755)
    pin_tb = os.path.join(d, 'pinned.tar.gz')
    pin_sha = _fake_tarball(pin_tb, 'fake-pinned', 'repository: test\ntag: phase-2-end\ncommit: deadbeef\n')
    rogue_tb = os.path.join(d, 'rogue.tar.gz')
    _fake_tarball(rogue_tb, 'fake-rogue')
    os.environ['TRACEMAKER_PINNED_SHA256'] = pin_sha
    srv = _load_server(data)
    cfg = srv.load_config()
    cfg['tracemaker']['sha256'] = {'0.12.1': 'f' * 64}
    cfg['tracemaker']['selected_version'] = '0.12.1'
    cfg['tracemaker']['url'] = pin_tb
    srv.save_config(cfg)
    return srv, pin_tb, pin_sha, rogue_tb


def test_pinned_upgrade_path():
    srv, pin_tb, pin_sha, rogue_tb = _scenario()
    cfg = srv.load_config()
    assert srv.tm_pinned_install_missing(cfg) is True
    res = srv.tm_update(pin_tb)
    assert res['version'] == '0.12.1' and res['sha256'] == pin_sha, res
    assert 'fake-pinned' in open(srv.tm_exe('0.12.1')).read()
    cfg = srv.load_config()
    assert cfg['tracemaker']['sha256']['0.12.1'] == pin_sha
    assert srv.tm_pinned_install_missing(cfg) is False
    assert 'phase-2-end' in srv.tm_provenance(cfg)
    try:
        srv.tm_update(rogue_tb)
    except RuntimeError as e:
        assert 'TOFU' in str(e), e
    else:
        raise AssertionError('rogue tarball was installed')
    assert 'fake-pinned' in open(srv.tm_exe('0.12.1')).read()


def test_no_pin_keeps_strict_tofu():
    srv, pin_tb, pin_sha, rogue_tb = _scenario()
    os.environ['TRACEMAKER_PINNED_SHA256'] = ''
    try:
        srv.tm_update(pin_tb)
    except RuntimeError as e:
        assert 'TOFU' in str(e), e
    else:
        raise AssertionError('unpinned mismatched tarball was installed')


if __name__ == '__main__':
    test_pinned_upgrade_path()
    test_no_pin_keeps_strict_tofu()
    print('tm pinned upgrade OK')
