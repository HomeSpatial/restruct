# SPDX-License-Identifier: Apache-2.0
"""Bounded review candidates from complete ray arrays, including missing hits.

These are scan/model discrepancies, not semantic errors or acceptance decisions.
No scene-specific sampling, tolerance, or quality threshold is used here.
"""
from collections import defaultdict

import numpy as np


def summarize_anomalies(arrays, domain, limit=6):
    """Group by hit identity pair and report spatial bounds plus representative rays.

    ``limit`` bounds presentation only; counts and bounds always use all samples.
    Distances are Euclidean metres even when the parent report uses an XYZ component.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError('Diagnostic presentation limit must be a positive integer')
    scan = arrays['scan_valid']
    before = arrays[f'before_{domain}_valid']
    after = arrays[f'after_{domain}_valid']
    old_ids = arrays[f'before_{domain}_object_id']
    new_ids = arrays[f'after_{domain}_object_id']
    count = len(scan)
    errors = {}
    for phase, valid in [('before', before), ('after', after)]:
        values = np.full(count, np.nan)
        mask = scan & valid
        values[mask] = np.linalg.norm(
            arrays[f'{phase}_{domain}_points_work_m'][mask] - arrays['scan_points_work_m'][mask], axis=1)
        errors[phase] = values

    def point(key, valid, i):
        return arrays[key][i].tolist() if valid[i] else None

    def sample(i):
        row = {'ray_index': int(i), 'origin_work_m': arrays['origins_work_m'][i].tolist(),
               'direction_work': arrays['directions_work'][i].tolist(),
               'scan_point_work_m': point('scan_points_work_m', scan, i)}
        for phase, valid in [('before', before), ('after', after)]:
            row[phase + '_object_id'] = str(arrays[f'{phase}_{domain}_object_id'][i]) if valid[i] else None
            row[phase + '_point_work_m'] = point(f'{phase}_{domain}_points_work_m', valid, i)
            row[phase + '_error_distance_m'] = float(errors[phase][i]) if scan[i] and valid[i] else None
        return row

    # Prefer observed scan positions; otherwise use the new hit, old hit or origin.
    anchors = arrays['origins_work_m'].copy()
    for key, valid in [(f'before_{domain}_points_work_m', before),
                       (f'after_{domain}_points_work_m', after), ('scan_points_work_m', scan)]:
        anchors[valid] = arrays[key][valid]

    def select(indices, scores=None, cap=limit):
        if scores is not None:
            order = np.argsort(-scores[indices], kind='stable')
            return indices[order[:cap]]
        # Spread examples across space, rather than only the first raster row.
        xyz = anchors[indices]
        order = np.lexsort((indices, xyz[:, 2], xyz[:, 1], xyz[:, 0]))
        slots = np.linspace(0, len(order)-1, min(cap, len(order)), dtype=int)
        return indices[order[slots]]

    def candidates(mask, scores=None):
        indices = np.flatnonzero(mask)
        groups = defaultdict(list)
        for i in indices:
            groups[(str(old_ids[i]) if before[i] else '', str(new_ids[i]) if after[i] else '')].append(i)
        ranked = sorted(groups.items(), key=lambda item: (
            -max(scores[item[1]]) if scores is not None else -len(item[1]), item[0]))
        summaries = []
        for (old, new), members in ranked[:limit]:
            members = np.array(members)
            xyz = anchors[members]
            summaries.append({'before_object_id': old or None, 'after_object_id': new or None,
                              'count': len(members), 'anchor_bounds_work_m': [xyz.min(0).tolist(), xyz.max(0).tolist()],
                              'examples': [sample(i) for i in select(members, scores, min(2, limit))]})
        return {'count': len(indices), 'examples': [sample(i) for i in select(indices, scores)] if len(indices) else [],
                'hit_pair_group_count': len(groups), 'omitted_hit_pair_groups': max(0, len(groups)-limit),
                'hit_pair_groups': summaries}

    def coverage(old_mask, new_mask):
        result = candidates(new_mask)
        result.update(before_count=int(old_mask.sum()), new_count=int((new_mask & ~old_mask).sum()),
                      persistent_count=int((new_mask & old_mask).sum()), resolved_count=int((old_mask & ~new_mask).sum()))
        return result

    common = scan & before & after
    delta = errors['after'] - errors['before']
    surface = candidates(scan & after, errors['after'])
    surface['largest_increases'] = candidates(common & (delta > 0), delta)
    return {
        'surface_deviation': surface,
        'scan_hit_model_miss': coverage(scan & ~before, scan & ~after),
        'scan_miss_model_hit': coverage(~scan & before, ~scan & after),
        'hit_identity_change': candidates(before & after & (old_ids != new_ids)),
    }
