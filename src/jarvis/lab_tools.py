"""Receipt-backed read-only sources and a closed numerical capability interpreter."""

import json
import math
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

from jarvis.contracts import AdmissionError, Cost, IntegrityError, JarvisError
from jarvis.identity import digest
from jarvis.lab_contracts import CapabilityDefinition, SourceDocument
from jarvis.storage import Store


class SourceSearch(Protocol):
    def identity(self) -> dict[str, Any]: ...
    def search(self, public_question: str) -> list[SourceDocument]: ...


def query_documents(adapter: SourceSearch, question: str) -> list[dict[str, Any]]:
    return [document.model_dump(mode="json") for document in adapter.search(question)]


class CrossrefSearch:
    """Public metadata/abstract search only. No crawling, credentials or code execution."""

    def identity(self) -> dict[str, Any]:
        return {
            "adapter": "crossref-read-only",
            "version": 1,
            "max_results": 3,
            "read_scope": "metadata_or_abstract",
        }

    def search(self, public_question: str) -> list[SourceDocument]:
        query = urllib.parse.urlencode({"query": public_question, "rows": 3})
        request = urllib.request.Request(
            "https://api.crossref.org/works?" + query,
            headers={
                "User-Agent": "JarvisResearch/0.11 (public-metadata-only)",
                "Accept": "application/json",
            },
        )

        # Crossref responses must not redirect to another host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(
                self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
            ) -> None:
                raise AdmissionError("source redirect denied")

        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise AdmissionError("source response exceeds read limit")
        items = json.loads(data)["message"]["items"]
        documents = []
        for item in items[:3]:
            title = " ".join(item.get("title", []))[:1000]
            if not title or not item.get("DOI"):
                continue
            abstract = re.sub(r"<[^>]+>", " ", item.get("abstract", "")).strip()
            documents.append(
                SourceDocument(
                    uri="https://doi.org/" + item["DOI"],
                    title=title,
                    text=(abstract or title)[:16000],
                    version=str(item.get("indexed", {}).get("timestamp", "unknown")),
                    origin="external",
                    read_scope="abstract" if abstract else "metadata",
                    license_note="Metadata/abstract only; publisher rights retained; no source code executed",
                )
            )
        return documents


class GitHubImplementationSearch:
    """Public implementation metadata; never clone, install or run a repository."""

    def identity(self) -> dict[str, Any]:
        return {
            "adapter": "github-public-repository-search",
            "version": 1,
            "max_results": 3,
            "read_scope": "metadata",
        }

    def search(self, public_question: str) -> list[SourceDocument]:
        query = urllib.parse.urlencode({"q": public_question, "per_page": 3})
        request = urllib.request.Request(
            "https://api.github.com/search/repositories?" + query,
            headers={"User-Agent": "JarvisResearch/0.11", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            if urllib.parse.urlparse(response.geturl()).netloc != "api.github.com":
                raise AdmissionError("implementation source redirect denied")
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise AdmissionError("implementation metadata too large")
        return [
            SourceDocument(
                uri=item["html_url"],
                title=item["full_name"],
                text=(item.get("description") or item["full_name"])[:16000],
                version=item.get("updated_at", "unknown"),
                origin="external",
                read_scope="metadata",
                license_note="Repository metadata only; license="
                + str((item.get("license") or {}).get("spdx_id", "UNKNOWN"))
                + "; code not reviewed or executed",
            )
            for item in json.loads(data)["items"][:3]
        ]


def run_tool(
    store: Store, owner: str, key: str, request: dict[str, Any], function: Callable[[], Any]
) -> Any:
    envelope = {
        "kind": "LabTool",
        "key": key,
        "request": request,
        "world_id": store.manifest(owner)["world_id"],
    }
    with store.campaign_lock(owner):
        matches = [j for j in store.jobs(owner) if j["request_ref"] == digest(envelope)]
        if matches:
            job = matches[0]
        else:
            jid = store.reserve(
                owner, envelope, Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=20)
            )
            job = next(j for j in store.jobs(owner) if j["id"] == jid)
        receipt = store.read_receipt(job["id"])
        if receipt is None:
            if job["status"] != "QUEUED":
                store.set_status(owner, "NEEDS_RECONCILIATION")
                raise JarvisError("unknown lab tool completion; no resubmission")
            store.start_job(job["id"])
            started = time.monotonic()
            try:
                value, error = function(), None
            except Exception as exc:
                value, error = None, type(exc).__name__ + ":" + str(exc)[:500]
            receipt = {
                "job_id": job["id"],
                "request_ref": job["request_ref"],
                "world_id": envelope["world_id"],
                "value": value,
                "error": error,
                "cost": Cost(
                    actions=1,
                    tool_calls=1,
                    compute_units=1,
                    wall_seconds=time.monotonic() - started,
                ).model_dump(),
            }
            store.write_receipt(job["id"], receipt)
        store.finish_auxiliary_job(
            job["id"],
            "LAB_TOOL_COMPLETED",
            {
                "key": key,
                "value_ref": store.artifacts.put(receipt["value"]),
                "error": receipt["error"],
            },
        )
    if receipt["error"]:
        raise AdmissionError("charged source/diagnostic failure: " + receipt["error"])
    return receipt["value"]


def execute_capability(
    definition: CapabilityDefinition, groups: dict[str, tuple[float, ...]]
) -> dict[str, Any]:
    """Interpret a typed, non-executable diagnostic recipe. Inputs are development-only."""
    if definition.operation != "group_mean_argmax" or not 1 <= len(groups) <= 16:
        raise AdmissionError("unsupported capability/input")
    if any(not v or len(v) > 1000 or any(not math.isfinite(x) for x in v) for v in groups.values()):
        raise AdmissionError("invalid diagnostic observations")
    means = {k: math.fsum(v) / len(v) for k, v in sorted(groups.items())}
    return {
        "means": means,
        "best_group": min(means, key=lambda k: (-means[k], k)),
        "scope": "declared development diagnostic; not a backtest or future outcome",
    }


def fidelity_check(definition: CapabilityDefinition) -> dict[str, Any]:
    cases: list[tuple[dict[str, tuple[float, ...]], str]] = [
        ({"a": (1.0, 3.0), "b": (5.0, 7.0)}, "b"),
        ({"a": (-3.0, -1.0), "b": (-5.0,)}, "a"),
        ({"a": (1.0,), "b": (1.0,)}, "a"),
    ]
    outputs = [execute_capability(definition, groups) for groups, _ in cases]
    if [o["best_group"] for o in outputs] != [wanted for _, wanted in cases]:
        raise IntegrityError("fixed capability fidelity failed")
    return {
        "status": "FIDELITY_TESTED",
        "cases": outputs,
        "interpreter": "group-mean-argmax:v1",
        "utility": "UNVERIFIED",
    }


def verify_tools(store: Store, owner: str) -> None:
    jobs = {j["id"]: j for j in store.jobs(owner)}
    seen = set()
    for event in store.events(owner):
        if event["event_type"] != "LAB_TOOL_COMPLETED":
            continue
        p = event["payload"]
        job = jobs[p["job_id"]]
        receipt = store.artifacts.get(p["receipt_ref"])
        request = store.artifacts.get(job["request_ref"])
        if (
            p["job_id"] in seen
            or not job["finalized"]
            or receipt["request_ref"] != job["request_ref"]
            or receipt["world_id"] != store.manifest(owner)["world_id"]
            or digest(receipt["value"]) != p["value_ref"]
            or store.artifacts.get(p["value_ref"]) != receipt["value"]
            or p["key"] != request["key"]
            or Cost.model_validate(receipt["cost"]) != Cost.model_validate_json(job["actual"])
        ):
            raise IntegrityError("lab tool receipt mismatch")
        seen.add(p["job_id"])
