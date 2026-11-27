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

"""Prepared-input cache (#62).

Prepared (stripped) inputs are cached keyed by a hash of the source
board files (.kicad_pcb + .kicad_pro + .kicad_dru bytes). Every later
run routes the identical prepared input, so A/B conclusions cannot
drift with input generation -- the failure WeaveEngine's sweep caught
in KiCad's own DSN export. Re-preparing unchanged sources must be
byte-stable: the prepared file's sha256 is recorded at first prep and
verified on every cache hit; a mismatch is a hard error, never a
silent re-prep.

The strip itself is injected (strip_fn) so this module stays pure:
production passes the pipeline's own worker strip, tests pass a fake.
"""
import hashlib
import json
import os
import shutil


class PrepError(Exception):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _read(path):
    if path and os.path.exists(path):
        with open(path, 'rb') as f:
            return f.read()
    return None


def source_key(pcb_path, pro_path=None, dru_path=None):
    """Cache key: hash of the source board files' bytes."""
    h = hashlib.sha256()
    for part in (_read(pcb_path), _read(pro_path), _read(dru_path)):
        if part is None:
            h.update(b'\x00absent\x00')
        else:
            h.update(len(part).to_bytes(8, 'big'))
            h.update(part)
    return h.hexdigest()


def prepare(board, cache_dir, strip_fn):
    """Return the prep record for board, preparing on a cache miss.

    strip_fn(src_pcb, out_pcb) must write the stripped board to
    out_pcb. The record carries the prepared file's path and sha256;
    on a cache hit the stored sha256 is re-verified against the file
    on disk (the byte-stability check).
    """
    if not board.pcb or not os.path.exists(board.pcb):
        raise PrepError('board file missing for %s: %s'
                        % (board.slug, board.pcb))
    key = source_key(board.pcb, board.pro, board.dru)
    cdir = os.path.join(cache_dir, key)
    meta_path = os.path.join(cdir, 'prep.json')
    prepared = os.path.join(cdir, 'prepared.kicad_pcb')
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        actual = sha256_file(prepared)
        if actual != meta['prepared_sha256']:
            raise PrepError(
                'prepared input drift for %s: cache records sha256 %s '
                'but the file on disk hashes to %s -- refusing to route '
                'a drifted input' % (board.slug, meta['prepared_sha256'],
                                     actual))
        meta['cache_hit'] = True
        meta['prepared_path'] = prepared
        return meta
    os.makedirs(cdir, exist_ok=True)
    src = os.path.join(cdir, 'source.kicad_pcb')
    shutil.copy2(board.pcb, src)
    for ext, sib in (('.kicad_pro', board.pro), ('.kicad_dru', board.dru)):
        if sib:
            shutil.copy2(sib, os.path.join(cdir, 'source' + ext))
            shutil.copy2(sib, os.path.join(cdir, 'prepared' + ext))
    strip_fn(src, prepared)
    if not os.path.exists(prepared):
        raise PrepError('strip produced no prepared board for %s'
                        % board.slug)
    meta = {'slug': board.slug, 'split': board.split,
            'stratum': board.stratum, 'source_key': key,
            'source_pcb_sha256': sha256_file(board.pcb),
            'source_pro_sha256': sha256_file(board.pro) if board.pro else None,
            'source_dru_sha256': sha256_file(board.dru) if board.dru else None,
            'prepared_sha256': sha256_file(prepared),
            'cache_hit': False}
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=1)
    meta['prepared_path'] = prepared
    return meta
