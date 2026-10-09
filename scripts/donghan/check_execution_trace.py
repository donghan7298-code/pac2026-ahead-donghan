#!/usr/bin/env python3
"""Check the records produced on the actual ROS/Gazebo demo machine."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    own = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(own/'ros2_ws/src/pac_execution'))
    from pac_execution.contract import ExecutionFault
    from pac_execution.trace_check import check_trace
    try:
        rows = [json.loads(line) for line in (args.demo_dir/'execution_trace.jsonl').read_text().splitlines()]
        completion = json.loads((args.demo_dir/'demo_complete.json').read_text())
        fixture = json.loads((args.demo_dir/'fixture.json').read_text())
        report = check_trace(rows, completion, fixture)
    except (ExecutionFault, OSError, ValueError, KeyError) as error:
        report = dict(scope='OFFLINE_EXECUTION_LOG_CHECK', status='INCOMPLETE', reason=str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
    return 0 if report['status'] == 'RECORDED_ACCEPTANCE' else 2


if __name__ == '__main__':
    sys.exit(main())
