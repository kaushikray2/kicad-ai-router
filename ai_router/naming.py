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

"""Result-board naming (v1.12, #167): <project>-<YYYYMMDD>-<HHMMSS>.

Pure python (no wx, no pcbnew) so the scheme is unit-testable
headless. The backend never receives a project name (its submit
payload carries only the board basename), so naming is minted
client-side in both modes: at the download point for backend runs,
and as a post-run copy beside the source board for local runs.
Server-side file names are unchanged by design.
"""
import glob, os, re
from datetime import datetime

# A previous result's own suffix: -YYYYMMDD-HHMMSS plus the optional
# collision counter (-2, -3, ...) unique_result_path may have added.
_TS_SUFFIX = re.compile(r'-\d{8}-\d{6}(-\d+)?$')
_BACKEND_SUFFIX = '-backend'


def strip_generated_suffixes(stem):
    # Remove the suffixes this plugin itself generates, repeatedly,
    # so re-routing a previous result can never compound them (the
    # routed-6040g-backend-backend.kicad_pcb failure). A stem that
    # legitimately ends in '-backend' loses it too -- that is the
    # documented price of the de-compounding rule.
    prev = None
    while prev != stem:
        prev = stem
        stem = _TS_SUFFIX.sub('', stem)
        if stem.endswith(_BACKEND_SUFFIX):
            stem = stem[:-len(_BACKEND_SUFFIX)]
    return stem


def project_stem(board_path):
    # The project name is the sibling .kicad_pro file's stem (KiCad's
    # project layout makes the .kicad_pro filename the project name;
    # BOARD.GetProject().GetProjectName() is not exposed in the
    # KiCad 10.0.8 Python bindings). Prefer the .kicad_pro that
    # shares the board's stem; else, when the directory holds exactly
    # one .kicad_pro, that is the project; else the board stem.
    # Generated suffixes are stripped from whichever stem wins.
    board_path = os.path.abspath(board_path)
    d = os.path.dirname(board_path)
    stem = os.path.splitext(os.path.basename(board_path))[0]
    if os.path.exists(os.path.join(d, stem + '.kicad_pro')):
        return strip_generated_suffixes(stem)
    pros = glob.glob(os.path.join(d, '*.kicad_pro'))
    if len(pros) == 1:
        return strip_generated_suffixes(os.path.splitext(os.path.basename(pros[0]))[0])
    return strip_generated_suffixes(stem)


def sidecar_paths(board_path):
    # (#182): the project's .kicad_pro / .kicad_dru for a board,
    # resolved with the same stem logic as project_stem: prefer
    # the files sharing the board's stem; else, when the directory
    # holds exactly one .kicad_pro, that project's files. Either
    # entry may be None. Single-sourced because Pipeline.__init__,
    # backend_client.submit and the dialog's local delivery each
    # hardcoded splitext(board)+ext and silently lost the sidecars
    # of a renamed board -- it routed without its netclasses and
    # DRC rules and the result was delivered bare.
    board_path = os.path.abspath(board_path)
    d = os.path.dirname(board_path)
    stem = os.path.splitext(os.path.basename(board_path))[0]
    board_stem = stem
    pro = os.path.join(d, stem + '.kicad_pro')
    if not os.path.exists(pro):
        pros = glob.glob(os.path.join(d, '*.kicad_pro'))
        if len(pros) == 1:
            pro = pros[0]
            stem = os.path.splitext(os.path.basename(pro))[0]
        else:
            pro = None
    # (#198): the .kicad_dru is the BOARD's sidecar first -- when
    # the project stem won above, a board-stem .kicad_dru
    # (variant.kicad_dru beside myproj.kicad_pro) was never
    # checked and the renamed board silently lost its DRC rules.
    dru = os.path.join(d, board_stem + '.kicad_dru')
    if not os.path.exists(dru):
        dru = os.path.join(d, stem + '.kicad_dru')
    if not os.path.exists(dru):
        dru = None
    return pro, dru


def result_filename(board_path, when=None):
    when = when or datetime.now()
    return '%s-%s.kicad_pcb' % (project_stem(board_path), when.strftime('%Y%m%d-%H%M%S'))


def unique_result_path(board_path, when=None):
    # The named result path beside the source board. NEVER overwrites:
    # on collision append -2, -3, ... until free (replaces the old
    # backend-download behaviour, which silently overwrote the
    # previous result of the same name).
    d = os.path.dirname(os.path.abspath(board_path))
    name = result_filename(board_path, when)
    dest = os.path.join(d, name)
    if not os.path.exists(dest):
        return dest
    stem = name[:-len('.kicad_pcb')]
    n = 2
    while True:
        cand = os.path.join(d, '%s-%d.kicad_pcb' % (stem, n))
        if not os.path.exists(cand):
            return cand
        n += 1
