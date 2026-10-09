"""Checkpoint relocation tests; not a model training or robot acceptance."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).parents[1]/'scripts/donghan/relocate_training_run.py'


def sample_checkpoint(root):
    shards = root/'shards'
    shards.mkdir()
    data = shards/'train-S0001-7-teacher.jsonl.gz'
    # Only byte-integrity/path handling is under test, not label content.
    data.write_bytes(b'test-byte-payload')
    status = dict(task='train-S0001-7-teacher', collection_digest='test-contract',
                  shard='/old/machine/run/shards/'+data.name,
                  shard_sha256=hashlib.sha256(data.read_bytes()).hexdigest())
    record = shards/'train-S0001-7-teacher.status.json'
    record.write_text(json.dumps(status))
    (root/'collection.json').write_text(json.dumps({'digest':'test-contract'}))
    metadata = root/'packed/train/metadata.json'
    metadata.parent.mkdir(parents=True)
    metadata.write_text('{"test":"immutable pack provenance"}')
    return data, record, metadata


def test_relocated_checkpoint_preserves_contract_bytes_and_pack_fingerprint(tmp_path):
    data, record, metadata = sample_checkpoint(tmp_path)
    before_data, before_metadata = data.read_bytes(), metadata.read_bytes()
    dry = subprocess.run([sys.executable,str(SCRIPT),'--run-dir',str(tmp_path)],
                         check=True,capture_output=True,text=True)
    assert json.loads(dry.stdout)['applied'] is False
    assert json.loads(record.read_text())['shard'].startswith('/old/')
    subprocess.run([sys.executable,str(SCRIPT),'--run-dir',str(tmp_path),'--apply'],
                   check=True,capture_output=True,text=True)
    assert json.loads(record.read_text())['shard'] == str(data)
    assert data.read_bytes() == before_data and metadata.read_bytes() == before_metadata
    assert json.loads((tmp_path/'collection.json').read_text()) == {'digest':'test-contract'}


def test_relocation_does_not_relabel_a_tampered_shard_as_completed(tmp_path):
    data, record, _ = sample_checkpoint(tmp_path)
    before = record.read_bytes()
    data.write_bytes(b'truncated-or-mutated')
    done = subprocess.run([sys.executable,str(SCRIPT),'--run-dir',str(tmp_path),'--apply'],
                          capture_output=True,text=True)
    assert done.returncode != 0
    assert record.read_bytes() == before
