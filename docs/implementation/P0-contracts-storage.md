# P0 contracts and storage verification

Status: VERIFIED_WITH_LIMITATIONS. Date: 2026-10-18.

Implemented Python 3.12 packaging, strict versioned Pydantic records, generated
JSON Schemas, finite canonical JSON and content identities. The example task
validates and roundtrips. Unknown fields/versions, negative budgets, nonfinite
amounts, boolean action counts and aliased split identities are rejected.

The local evidence store uses SQLite WAL, transaction-safe append, hash-linked
events, immutable content-addressed JSON and immutable campaign manifests.
Resource reservations enforce every supported dimension and concurrent-job cap
inside BEGIN IMMEDIATE. Job receipts use atomic filesystem links before journal
finalization. Filesystem permissions are private; hashes detect changes, not a
malicious owner of the host. SQLite is the authoritative journal, not JSONL mirrors.

Executed commands from the repository root:

```sh
python -m pip install -e '.[dev,hermes]'
python -m pytest tests/test_contracts.py tests/test_storage.py -q
jarvis task validate examples/research_task.example.yaml
jarvis schema schemas
```

The automated suite includes real spawned-process append/reservation races,
rollback before commit, tampering, overwrite refusal and path traversal checks.
The full dependency snapshot is requirements-lock.txt. The milestone commit and
its parent provide source provenance; generated schemas accompany that commit.

Remaining: full campaign recovery, independent executor process security,
arbitrary generated-code isolation and remote job reconciliation. No remote
training, paid backtest, external provider or strategy efficacy is asserted here.
