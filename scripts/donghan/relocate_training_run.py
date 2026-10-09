#!/usr/bin/env python3
"""Rebase completed shard paths after unpacking a training checkpoint.

Only stored absolute locations change. Shard bytes, packed metadata, training
contracts and model weights stay untouched. Stop any worker on this directory
before using --apply. Dry-run is the default.
"""
import argparse
import hashlib
import json
from pathlib import Path


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = args.run_dir.resolve()
    collection = json.loads((root/'collection.json').read_text())
    changes, records = [], {}
    # Verify all completed files first. A truncated/mutated gzip shard must
    # never be legitimized by rewriting its status record.
    for path in sorted((root/'shards').glob('*.status.json')):
        record = json.loads(path.read_text())
        shard = path.with_name(path.name.removesuffix('.status.json')+'.jsonl.gz')
        if record['collection_digest'] != collection['digest'] or record['shard_sha256'] != sha256(shard):
            parser.error('Changed or incompatible completed shard: '+str(path))
        records[record['task']] = {**record, 'shard': str(shard)}
        if record['shard'] != str(shard):
            changes.append((path, records[record['task']]))
    summary_path = root/'collection_summary.json'
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text())
        old = summary['episodes']
        if any(r['task'] not in records or r['shard_sha256'] != records[r['task']]['shard_sha256'] for r in old):
            parser.error('Collection summary and completed shards differ')
        updated = [records[r['task']] for r in old]
        if updated != old:
            changes.append((summary_path, {**summary, 'episodes': updated}))
    if args.apply:
        for path, value in changes:
            temporary = path.with_name(path.name+'.relocate-tmp')
            temporary.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
            temporary.replace(path)
    print(json.dumps(dict(scope='CHECKPOINT_PATH_RELOCATION', completed_shards=len(records),
                         json_files_to_change=len(changes), applied=args.apply,
                         immutable_data_changed=False)))


if __name__ == '__main__':
    main()
