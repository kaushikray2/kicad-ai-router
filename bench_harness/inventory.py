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

"""Board inventory for the bench harness (#62).

Two board sources:
- dataset-v3: slugs and the train/held-out split come from
  dataset-v3/split.json, the stratum per board from
  dataset-v3/dataset.json, and the board files live under a boards
  root as <stratum>/<slug>.kicad_pcb.
- fixtures: any directory of .kicad_pcb files (e.g. docker/selftest).

The held-out guard lives here: select_boards() refuses to hand out a
held-out board unless exam=True. Exam mode exists so the final exam
is an invocation of this harness, not new code; it is never the
default, and callers must label exam selections in their manifests.
"""
import glob
import json
import os

SPLIT_TRAIN = 'train'
SPLIT_HELD_OUT = 'held_out'
SPLIT_FIXTURE = 'fixture'
FIXTURE_STRATUM = 'fixtures'


class HeldOutError(Exception):
    """A non-exam selection named held-out (final exam) boards."""

    def __init__(self, slugs):
        self.slugs = sorted(slugs)
        super().__init__(
            'held-out board(s) refused in normal operation: %s. The 49 '
            'held-out dataset-v3 boards belong to the final exam; select '
            'them only through explicit exam mode.'
            % ', '.join(self.slugs))


class Board(object):
    def __init__(self, slug, split, stratum, pcb, pro=None, dru=None):
        self.slug = slug
        self.split = split
        self.stratum = stratum
        self.pcb = pcb
        self.pro = pro
        self.dru = dru

    def as_dict(self):
        return {'slug': self.slug, 'split': self.split,
                'stratum': self.stratum, 'pcb': self.pcb,
                'pro': self.pro, 'dru': self.dru}

    def __repr__(self):
        return 'Board(%s, %s, %s)' % (self.slug, self.split, self.stratum)


def _siblings(pcb):
    # #226: delegate to the single-sourced resolver (#182/#198) --
    # a board whose filename differs from the project stem still
    # owns the directory's sole .kicad_pro (and a board-stem
    # .kicad_dru wins over the project-stem one).  The stem-only
    # check here silently dropped those sidecars in prep, so the
    # staged board routed without its netclasses / DRC rules.
    from ai_router.naming import sidecar_paths
    return sidecar_paths(pcb)


def _resolve_board_file(boards_root, stratum, slug):
    name = slug + '.kicad_pcb'
    if stratum:
        p = os.path.join(boards_root, stratum, name)
        if os.path.exists(p):
            return p
    hits = sorted(glob.glob(os.path.join(boards_root, '*', name)))
    if hits:
        return hits[0]
    p = os.path.join(boards_root, name)
    return p if os.path.exists(p) else None


def load_dataset_inventory(dataset_dir, boards_root):
    """All dataset-v3 boards (train + held-out) with split + stratum."""
    with open(os.path.join(dataset_dir, 'split.json')) as f:
        split = json.load(f)
    strata = {}
    ds_path = os.path.join(dataset_dir, 'dataset.json')
    if os.path.exists(ds_path):
        with open(ds_path) as f:
            for rec in json.load(f):
                if rec.get('board'):
                    strata[rec['board']] = rec.get('stratum')
    boards = []
    for split_name in (SPLIT_TRAIN, SPLIT_HELD_OUT):
        for slug in split.get(split_name) or []:
            stratum = strata.get(slug)
            pcb = _resolve_board_file(boards_root, stratum, slug)
            pro = dru = None
            if pcb:
                pro, dru = _siblings(pcb)
            boards.append(Board(slug, split_name, stratum, pcb, pro, dru))
    return boards


def load_fixtures(directories):
    """Every .kicad_pcb in the given directories, as fixture boards."""
    boards = []
    for d in directories or []:
        for pcb in sorted(glob.glob(os.path.join(d, '*.kicad_pcb'))):
            slug = os.path.basename(pcb)[:-len('.kicad_pcb')]
            pro, dru = _siblings(pcb)
            boards.append(Board(slug, SPLIT_FIXTURE, FIXTURE_STRATUM,
                                pcb, pro, dru))
    return boards


def select_boards(boards, slugs=None, exam=False):
    """Pick boards by slug (or all), enforcing the held-out guard.

    Raises HeldOutError if the selection contains a held-out board and
    exam is False; KeyError for an unknown slug.
    """
    if slugs is not None:
        by_slug = {}
        for b in boards:
            by_slug.setdefault(b.slug, b)
        picked = []
        for s in slugs:
            if s not in by_slug:
                raise KeyError('unknown board slug: %s' % s)
            picked.append(by_slug[s])
    else:
        picked = list(boards)
    held = [b.slug for b in picked if b.split == SPLIT_HELD_OUT]
    if held and not exam:
        raise HeldOutError(held)
    return picked
