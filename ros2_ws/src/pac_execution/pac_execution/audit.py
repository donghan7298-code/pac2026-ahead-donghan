"""Recheck the measured layout with the team's authoritative hard mask."""

import math

from .contract import ExecutionFault


def check_placement(command, fixture, pallet, measured, previous, candidate_config):
    from pac_common import (BoxState, BoxStatus, InventoryState, PalletState, PlacedBox,
                            PlacementCandidate, PlanningContext, Pose3D, Size3D, SkuSpec, SystemState)
    from pac_candidates import CandidateBackend
    rows = {b['box_id']: b for b in fixture['boxes']}
    current = rows[command['box_id']]
    placed = []
    catalog, capacities = {}, {}
    for bid, row in rows.items():
        if 'max_top_load_n' not in row:
            raise ExecutionFault('Physical fixture needs explicit declared top-load limits')
        catalog[row['sku']] = SkuSpec(row['sku'], Size3D(*row['size_m']), row['weight_kg'],
                                      (0., math.pi/2), row['max_top_load_n'])
        capacities[bid] = row['max_top_load_n']
    for bid, corner in previous.items():
        row = rows[bid]
        pose = Pose3D('pallet', *corner[:3], yaw=corner[3])
        placed.append(PlacedBox(bid, row['sku'], Size3D(*row['size_m']), row['weight_kg'], pose))
    box = BoxState(current['box_id'], current['sku'], Size3D(*current['size_m']), current['weight_kg'],
                   Pose3D('conveyor', 0., 0., 0.), (0., math.pi/2), BoxStatus.MEASURED,
                   1., 0., 'GAZEBO_MEASURED')
    state = SystemState(command['state_version'], 0.,
                        PalletState('PHYSICAL_AUDIT', Size3D(*pallet.size), tuple(placed)),
                        InventoryState({box.box_id: box}, {}))
    context = PlanningContext(catalog=catalog, capacity_overrides_n=capacities,
                              pallet_max_weight_kg=fixture['pallet']['max_load_kg'])
    candidate = PlacementCandidate(command['candidate_id'], box.box_id,
                                   Pose3D('pallet', *measured[:3], yaw=measured[3]), state.state_version)
    verdict = CandidateBackend(context, candidate_config).validate_constraints(box, candidate, state)
    if not verdict.success:
        raise ExecutionFault('Measured hard-mask rejection: '+','.join(c.value for c in verdict.codes)
                             +' '+str(verdict.details.get('reasons', ())))
    return verdict
