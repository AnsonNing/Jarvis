"""Prevent accidental publication of local inputs and machine-specific context."""

import re
import subprocess
from pathlib import Path


def public_files():
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], text=True
    )
    return [Path(p) for p in output.split("\0") if p]


def test_public_tree_has_no_private_artifacts_or_host_paths():
    prohibited = {".parquet", ".pkl", ".sqlite", ".sqlite3", ".hpp", ".ipynb"}
    host_path = re.compile(r"/Users/" + r"[^/\s]+/|/home/" + r"[^/\s]+/")
    token = re.compile(r"(?:ghp_|github_pat_|sk-proj-)[A-Za-z0-9_]{20,}")
    for path in public_files():
        assert path.suffix not in prohibited, str(path)
        data = path.read_text()
        assert not host_path.search(data), str(path)
        assert not token.search(data), str(path)


def test_local_artifacts_are_ignored():
    paths = [".jarvis/evidence.json", "local/integration.yaml", "model.pkl", "data.parquet", ".env"]
    output = subprocess.check_output(["git", "check-ignore", *paths], text=True)
    assert output.splitlines() == paths
