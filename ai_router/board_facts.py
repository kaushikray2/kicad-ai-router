"""Board facts for the dialog (v1.12, #168): the open board's
stackup and zone map, read from the board FILE with pcbnew -- the
same data the worker's board_summary computes at run start
(copper_layers / layer_aliases / pour_layers), surfaced at dialog
open time instead.

Import-safe without pcbnew, and load_facts returns None on ANY
failure: detection is an aid, never a gate (proposal 4.4 fallback --
the dialog then keeps the saved config exactly as before). Never
raises.
"""
import os


def _stack(b, pcbnew):
    # Canonical copper names + pcbnew layer IDs, top to bottom --
    # the worker.copper_stack construction (KiCad numbers copper
    # out of stack order, so positions come from this table).
    n = b.GetCopperLayerCount()
    if n <= 1:
        return ['F.Cu'], [pcbnew.F_Cu]
    if n == 2:
        return ['F.Cu', 'B.Cu'], [pcbnew.F_Cu, pcbnew.B_Cu]
    canon = ['F.Cu'] + ['In%d.Cu' % k for k in range(1, n - 1)] + ['B.Cu']
    ids = [pcbnew.F_Cu] + [getattr(pcbnew, 'In%d_Cu' % k) for k in range(1, n - 1)] + [pcbnew.B_Cu]
    return canon, ids


def load_facts(board_path):
    # -> {'layers': [{'canonical':..., 'name':...}, ...],
    #     'zones_by_layer': {canonical: [net, ...]},
    #     'copper_count': int, 'zone_count': int}  or None.
    try:
        if not board_path or not os.path.exists(board_path):
            return None  # pcbnew.LoadBoard segfaults on a bad path
        import pcbnew
        b = pcbnew.LoadBoard(board_path)
        if b is None:
            return None
        canon, ids = _stack(b, pcbnew)
        layers, id2canon = [], {}
        for lid, c in zip(ids, canon):
            layers.append({'canonical': c, 'name': b.GetLayerName(lid)})
            id2canon[lid] = c
        zones_by_layer = {c: [] for c in canon}
        nzones = 0
        for z in b.Zones():
            nzones += 1
            net = z.GetNetname()
            if not net:
                continue
            try:
                lset = list(z.GetLayerSet().CuStack())
            except Exception:
                lset = [z.GetLayer()]
            for lid in lset:
                c = id2canon.get(lid)
                if c:
                    zones_by_layer[c].append(net)
        return {'layers': layers, 'zones_by_layer': zones_by_layer,
                'copper_count': len(canon), 'zone_count': nzones}
    except Exception:
        return None
