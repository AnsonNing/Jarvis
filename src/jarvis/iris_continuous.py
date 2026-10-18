"""Standing owner-authorized research, rolling finite programs, shared Iris quota."""

import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.campaign import code_provenance, source_identity
from jarvis.contracts import AdmissionError, Budget, Cost, IntegrityError, JarvisError, Record
from jarvis.identity import digest
from jarvis.iris import IrisTransport, LocalIrisTransport, verify_environment
from jarvis.iris_contracts import IrisAuthorization, IrisQueueSpec
from jarvis.iris_quota import IrisQuotaPolicy, IrisQuotaSnapshot, QuotaLedger, verify_quota
from jarvis.providers import CodexProvider, ResearchProvider
from jarvis.research_program import ResearchProgram, ResearchProgramSpec, program_report
from jarvis.research_queue import visible_results
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


class IrisResearchTrack(Record):
    label: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    smoke: IrisQueueSpec
    development: IrisQueueSpec

    @model_validator(mode="after")
    def same_model(self) -> "IrisResearchTrack":
        a, b = self.smoke.environment, self.development.environment
        if (
            a.model_lineage != b.model_lineage
            or a.side != b.side
            or a.template_sha256 != b.template_sha256
        ):
            raise ValueError("smoke and development must use the same frozen strategy/model")
        if a.period.exposure != "development_exposed" or b.period.exposure != "development_exposed":
            raise ValueError("continuous study admits declared development periods only")
        return self


class IrisContinuousSpec(Record):
    kind: Literal["IrisContinuousSpec"] = "IrisContinuousSpec"
    tracks: tuple[IrisResearchTrack, ...] = Field(min_length=1, max_length=2)
    quota_policy: IrisQuotaPolicy
    owner_instruction: str = Field(min_length=1, max_length=6000)
    directions: tuple[str, ...] = Field(min_length=1, max_length=12)
    agent: AgentConfig = AgentConfig(reserve_tokens=65536, max_calls=3, timeout_seconds=180)
    rounds_per_program: int = Field(default=3, ge=1, le=8, strict=True)
    max_experiments_per_batch: int = Field(default=2, ge=1, le=3, strict=True)
    primary_metric: Literal["entry_only_pl"] = "entry_only_pl"
    risk_policy: Literal["record_all_no_hidden_penalty"] = "record_all_no_hidden_penalty"

    @model_validator(mode="after")
    def labels(self) -> "IrisContinuousSpec":
        if len({t.label for t in self.tracks}) != len(self.tracks):
            raise ValueError("duplicate research tracks")
        if self.agent.max_calls < self.rounds_per_program:
            raise ValueError("agent call envelope cannot cover rolling program rounds")
        return self


class IrisContinuousStudy:
    def __init__(
        self,
        store: Store,
        *,
        provider_factory: Callable[[AgentConfig], ResearchProvider] = CodexProvider,
        transport_factory: Callable[..., IrisTransport] = LocalIrisTransport,
    ):
        self.store, self.provider_factory, self.transport_factory = (
            store,
            provider_factory,
            transport_factory,
        )
        self.program = ResearchProgram(
            store, provider_factory=provider_factory, transport_factory=transport_factory
        )

    def create(self, spec: IrisContinuousSpec) -> str:
        for track in spec.tracks:
            for q in [track.smoke, track.development]:
                verify_environment(q.environment)
        descriptor = self.provider_factory(spec.agent).identity()
        return self.store.create(
            {
                "kind": "IrisContinuousStudy",
                "spec": spec.model_dump(mode="json"),
                "provider": descriptor,
                "source_identity": source_identity(),
                "code_provenance": code_provenance(),
                "world_id": digest({"spec": spec.model_dump(mode="json"), "provider": descriptor}),
                "task": {
                    "budget": Budget(
                        max_actions=1000000,
                        max_tool_calls=1000000,
                        max_wall_seconds=100000000,
                        max_parallel_jobs=1,
                    ).model_dump()
                },
                "scope": "standing owner-authorized development research; no fixed lifetime experiment count",
            }
        )

    def _spec(self, owner: str) -> IrisContinuousSpec:
        m = self.store.manifest(owner)
        if m["kind"] != "IrisContinuousStudy" or m["source_identity"] != source_identity():
            raise IntegrityError(
                "continuous research runtime changed; do not resume active jobs across revisions"
            )
        spec = IrisContinuousSpec.model_validate(m["spec"])
        if self.provider_factory(spec.agent).identity() != m["provider"]:
            raise IntegrityError("continuous researcher provider changed")
        return spec

    def refresh_quota(
        self, owner: str, known_request_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """Read-only backend observation with a durable receipt and local cost."""
        spec = self._spec(owner)
        if known_request_ids is None:
            known_request_ids = self.known_requests(owner, spec)
        ledger = QuotaLedger(self.store, owner, spec.quota_policy)
        with self.store.campaign_lock(owner):
            pending = [j for j in self.store.jobs(owner) if not j["finalized"]]
            if pending:
                job = pending[0]
            else:
                request = {
                    "kind": "IrisQuotaRead",
                    "world_id": self.store.manifest(owner)["world_id"],
                    "request_ids": list(dict.fromkeys(known_request_ids))[-2000:],
                    "observation_nonce": secrets.token_hex(8),
                }
                jid = self.store.reserve(
                    owner, request, Cost(actions=1, tool_calls=1, wall_seconds=180)
                )
                job = next(j for j in self.store.jobs(owner) if j["id"] == jid)
            saved = self.store.read_receipt(job["id"])
            if saved is None:
                if job["status"] != "QUEUED":
                    self.store.set_status(owner, "NEEDS_RECONCILIATION")
                    return {
                        "status": "NEEDS_RECONCILIATION",
                        "reason": "quota-read usage unknown; reserved attempt retained",
                    }
                if job["status"] == "QUEUED":
                    self.store.start_job(job["id"])
                request = self.store.artifacts.get(job["request_ref"])
                started = time.monotonic()
                try:
                    result = self.transport_factory(spec.tracks[0].smoke.environment).quota(
                        request["request_ids"], self.store.root / "quota-observations" / job["id"]
                    )
                except Exception as exc:
                    result = {"status": "QUOTA_UNAVAILABLE", "error_type": type(exc).__name__}
                saved = {
                    "job_id": job["id"],
                    "request_ref": job["request_ref"],
                    "world_id": request["world_id"],
                    "result_ref": self.store.artifacts.put(result),
                    "cost": Cost(
                        actions=1, tool_calls=1, wall_seconds=time.monotonic() - started
                    ).model_dump(),
                }
                self.store.write_receipt(job["id"], saved)
            result = self.store.artifacts.get(saved["result_ref"])
            self.store.finish_auxiliary_job(
                job["id"], "IRIS_QUOTA_READ_COMPLETED", {"result_ref": saved["result_ref"]}
            )
            if result.get("status") == "QUOTA_OBSERVED":
                snapshot = IrisQuotaSnapshot.model_validate(result["snapshot"])
                expected = spec.tracks[0].smoke.environment.result_origin
                if (snapshot.source == "SIMULATED_FIXTURE") != (expected == "SIMULATED_FIXTURE"):
                    raise IntegrityError("quota observation origin differs from backend")
                ledger.observe(snapshot)
            else:
                return {"status": "QUOTA_UNAVAILABLE", "last_known": ledger.status()}
        return ledger.status()

    def known_requests(self, owner: str, spec: IrisContinuousSpec) -> list[str]:
        import json

        values: list[str] = []
        for track in spec.tracks:
            for q in [track.smoke, track.development]:
                if q.baseline:
                    verify_environment(q.environment)
                    values.extend(
                        c["request_id"]
                        for c in json.loads(Path(q.baseline.manifest).read_text())["cases"]
                        if c.get("request_id")
                    )
        for track in spec.tracks:
            for link in self._links(owner, track.label):
                for qid in self._queues(link["program_id"]):
                    values.extend(
                        e["payload"]["request_id"]
                        for e in self.store.events(qid)
                        if e["event_type"] == "IRIS_DISPATCH_RESERVED"
                    )
        return list(dict.fromkeys(values))[-2000:]

    def _links(self, owner: str, label: str) -> list[dict[str, Any]]:
        return [
            e["payload"]
            for e in self.store.events(owner)
            if e["event_type"] == "IRIS_PROGRAM_REGISTERED" and e["payload"]["track"] == label
        ]

    def _queues(self, program: str) -> list[str]:
        return [
            e["payload"]["queue_id"]
            for e in self.store.events(program)
            if e["event_type"] == "RESEARCH_QUEUE_ATTACHED"
        ]

    def _new_program(
        self,
        owner: str,
        spec: IrisContinuousSpec,
        track: IrisResearchTrack,
        phase: str,
        links: list[dict[str, Any]],
    ) -> str:
        base = track.smoke if phase == "smoke" else track.development
        prior = [q for link in links for q in self._queues(link["program_id"])]
        # Keep the founding scope plus recent evidence. All older receipts and
        # attempted candidate identities remain in the study and shared ledger.
        prior = list(dict.fromkeys([*base.prior_queues, *prior]))
        if len(prior) > 16:
            comparable = [
                (r["result"]["entry_only_pl"], q)
                for q in prior
                if self.store.manifest(q)["spec"]["environment"]
                == base.environment.model_dump(mode="json")
                for r in visible_results(self.store, q)
                if r["result"]["status"] == "SUCCEEDED"
            ]
            best_queue = max(comparable, key=lambda item: item[0])[1] if comparable else prior[0]
            prior = list(dict.fromkeys([prior[0], best_queue, *prior[-14:]]))
        grant = IrisAuthorization(
            environment_id=digest(base.environment),
            max_submissions=32,
            expires_at=None,
            owner_note="Standing human instruction in continuous study " + owner,
        )
        q = base.model_copy(
            update={
                "authorization": grant,
                "operating_owner": owner,
                "prior_queues": tuple(prior),
                "max_tasks": 4,
            }
        )
        rounds = 1 if phase == "smoke" else spec.rounds_per_program
        cfg = spec.agent.model_copy(update={"max_calls": rounds})
        program_spec = ResearchProgramSpec(
            queue=q,
            directions=spec.directions,
            agent=cfg,
            max_direction_batches=rounds,
            experiments_per_batch=1
            if phase == "smoke"
            else min(
                spec.max_experiments_per_batch,
                max(
                    1,
                    QuotaLedger(self.store, owner, spec.quota_policy).status()[
                        "remaining_conservative"
                    ],
                ),
            ),
        )
        pid = self.program.create(program_spec)
        self.store.append(
            owner,
            "IRIS_PROGRAM_REGISTERED",
            {
                "track": track.label,
                "phase": phase,
                "program_id": pid,
                "program_spec_ref": self.store.artifacts.put(program_spec),
                "generation": len(links) + 1,
            },
        )
        return pid

    def advance(self, owner: str) -> dict[str, Any]:
        with self.store.campaign_lock(owner, scope="continuous-run"):
            return self._advance_unlocked(owner)

    def _advance_unlocked(self, owner: str) -> dict[str, Any]:
        if self.store.status(owner) in {"CLOSED", "NEEDS_RECONCILIATION"}:
            return {"status": self.store.status(owner), "study_id": owner}
        spec = self._spec(owner)
        if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
            return {"status": self.store.get_control(owner), "study_id": owner}
        ledger = QuotaLedger(self.store, owner, spec.quota_policy)
        events = self.store.events(owner)
        stopped = {e["payload"]["track"] for e in events if e["event_type"] == "IRIS_TRACK_STOPPED"}
        tracks = [t for t in spec.tracks if t.label not in stopped]
        if not tracks:
            self.store.set_status(owner, "CLOSED")
            return {"status": "CLOSED", "study_id": owner}
        steps = [e for e in events if e["event_type"] == "IRIS_CONTINUOUS_TURN"]
        track = tracks[len(steps) % len(tracks)]
        self.store.append(owner, "IRIS_CONTINUOUS_TURN", {"track": track.label})
        with self.store.campaign_lock(owner):
            links = self._links(owner, track.label)
            if not links:
                pid = self._new_program(owner, spec, track, "smoke", links)
                phase = "smoke"
            else:
                pid = links[-1]["program_id"]
                phase = links[-1]["phase"]
                if self.store.status(pid) == "CLOSED":
                    report = program_report(self.store, pid)
                    known = {
                        e["payload"]["program_id"]
                        for e in self.store.events(owner)
                        if e["event_type"] == "IRIS_PROGRAM_REVIEWED"
                    }
                    if pid not in known:
                        self.store.append(
                            owner,
                            "IRIS_PROGRAM_REVIEWED",
                            {
                                "program_id": pid,
                                "track": track.label,
                                "phase": phase,
                                "report_ref": self.store.artifacts.put(report),
                            },
                        )
                    candidates = [
                        r for r in report["experiments"] if r["experiment"]["family"] != "baseline"
                    ]
                    valid = [r for r in candidates if r["result"]["status"] == "SUCCEEDED"]
                    stops = [
                        b["batch"]["stop_reason"]
                        for b in report["direction_batches"]
                        if b["batch"]["stop_reason"]
                    ]
                    failures = [r for r in candidates if r["result"]["status"] != "SUCCEEDED"]
                    if failures:
                        self.store.append(
                            owner,
                            "IRIS_TRACK_STOPPED",
                            {
                                "track": track.label,
                                "reason": "backend/identity failure requires analysis; no automatic retry",
                                "program_id": pid,
                            },
                        )
                        return {
                            "status": "TRACK_NEEDS_ATTENTION",
                            "study_id": owner,
                            "program_id": pid,
                        }
                    if (stops and phase == "development") or not valid:
                        self.store.append(
                            owner,
                            "IRIS_TRACK_STOPPED",
                            {
                                "track": track.label,
                                "reason": stops
                                or "no novel executable hypothesis; capability review required",
                                "program_id": pid,
                            },
                        )
                        return {"status": "TRACK_STOPPED", "study_id": owner, "program_id": pid}
                    quota = ledger.status()
                    if quota["status"] not in {"AVAILABLE", "WAITING_IRIS_SLOT"}:
                        return {**quota, "study_id": owner}
                    phase = "development"
                    pid = self._new_program(owner, spec, track, phase, links)
        try:
            progress = self.program.advance(pid)
        except JarvisError:
            self.store.set_status(owner, "NEEDS_RECONCILIATION")
            raise
        self.store.append(
            owner,
            "IRIS_CONTINUOUS_STEP",
            {"track": track.label, "phase": phase, "program_id": pid, "progress": progress},
        )
        if progress["status"] in {
            "NEEDS_RECONCILIATION",
            "PAUSED",
            "INVALID_RESULT",
            "FAILED",
            "REJECTED",
        }:
            self.store.set_status(owner, "NEEDS_RECONCILIATION")
        return {
            **progress,
            "status": "PROGRAM_COMPLETED" if progress["status"] == "CLOSED" else progress["status"],
            "study_id": owner,
            "track": track.label,
            "phase": phase,
        }

    def run(
        self, owner: str, *, wall_seconds: int = 1800, poll_seconds: int = 45, max_steps: int = 200
    ) -> dict[str, Any]:
        if (
            not 1 <= wall_seconds <= 14400
            or not 1 <= poll_seconds <= 60
            or not 1 <= max_steps <= 2000
        ):
            raise AdmissionError(
                "finite coordinator wall/step bounds and 1..60 second polling required"
            )
        started = time.monotonic()
        result: dict[str, Any] = {"status": "RUNNER_LIMIT"}
        # One coordinator lease for all tracks. Each nested program retains its
        # existing job locks and durable no-resubmit behavior.
        with self.store.campaign_lock(owner, scope="continuous-run"):
            for _ in range(max_steps):
                if time.monotonic() - started >= wall_seconds:
                    break
                result = self._advance_unlocked(owner)
                if result["status"] in {
                    "CLOSED",
                    "PAUSED",
                    "CANCELLED",
                    "NEEDS_RECONCILIATION",
                    "TRACK_NEEDS_ATTENTION",
                    "WAITING_FOR_QUOTA",
                    "QUOTA_REFRESH_REQUIRED",
                }:
                    return result
                if result["status"] in {"WAITING_FOR_IRIS", "WAITING_IRIS_SLOT"}:
                    time.sleep(
                        min(poll_seconds, max(0, wall_seconds - (time.monotonic() - started)))
                    )
        return {
            "study_id": owner,
            "status": "RUNNER_LIMIT",
            "last_progress": result,
            "resumable": True,
        }


def continuous_report(store: Store, owner: str) -> dict[str, Any]:
    manifest = store.manifest(owner)
    spec = IrisContinuousSpec.model_validate(manifest["spec"])
    if manifest["world_id"] != digest({"spec": manifest["spec"], "provider": manifest["provider"]}):
        raise IntegrityError("standing study declaration changed")
    programs = []
    results: dict[str, dict[str, Any]] = {}
    for event in store.events(owner):
        if event["event_type"] != "IRIS_PROGRAM_REGISTERED":
            continue
        p = event["payload"]
        report = program_report(store, p["program_id"])
        declared = ResearchProgramSpec.model_validate(store.artifacts.get(p["program_spec_ref"]))
        if (
            ResearchProgramSpec.model_validate(store.manifest(p["program_id"])["spec"]) != declared
            or declared.queue.operating_owner != owner
        ):
            raise IntegrityError("continuous program escaped its registered declaration")
        programs.append({**p, "report": report})
        for result in report["experiments"]:
            results[result["result_ref"]] = {**result, "track": p["track"], "phase": p["phase"]}
    best = {}
    for track in spec.tracks:
        for phase in ["smoke", "development"]:
            candidates = [
                r
                for r in results.values()
                if r["track"] == track.label
                and r["phase"] == phase
                and r["result"]["status"] == "SUCCEEDED"
            ]
            best[track.label + "/" + phase] = (
                max(candidates, key=lambda r: r["result"]["entry_only_pl"]) if candidates else None
            )
    return {
        "kind": "IrisContinuousReport",
        "study_id": owner,
        "status": store.status(owner),
        "primary_metric": "entry_only_pl",
        "risk_policy": spec.risk_policy,
        "programs": programs,
        "experiments": list(results.values()),
        "best_by_comparable_period": best,
        "quota": QuotaLedger(store, owner, spec.quota_policy).status(),
        "quota_verification": verify_quota(store, owner, spec.quota_policy),
        "operating_usage": store.resource_totals(owner),
        "formal_strategy_validation": "NOT_RUN",
        "policy_promotion": "NOT_RUN",
        "deployment": "NOT_RUN",
        "verification": {"status": "VERIFIED", "external_calls": 0},
    }


def prepare_continuous(
    upstream: Path, python: Path, owner_instruction: str, *, design_root: Path | None = None
) -> IrisContinuousSpec:
    from jarvis.iris_admission import attach_design, prepare_baseline, prepare_environment

    tracks = []
    sides: tuple[Literal["long", "short"], ...] = ("long", "short")
    windows: tuple[Literal["smoke", "full"], ...] = ("smoke", "full")
    for side in sides:
        queues = []
        for window in windows:
            environment = prepare_environment(upstream, side, "08", python, window=window)
            if design_root:
                environment = attach_design(environment, design_root / (side + ".json"))
            queues.append(
                IrisQueueSpec(
                    goal="最大化 entry-only PL，完整記錄風險、機制、反證、負面結果與改善",
                    environment=environment,
                    baseline=prepare_baseline(upstream, side, "08", window=window),
                )
            )
        tracks.append(IrisResearchTrack(label=side, smoke=queues[0], development=queues[1]))
    return IrisContinuousSpec(
        tracks=tuple(tracks),
        owner_instruction=owner_instruction,
        quota_policy=IrisQuotaPolicy(
            scope=digest({"iris_runtime": str(upstream.resolve().parents[1])})
        ),
        directions=(
            "以停利、停損、模型/heuristic 砍單、入場 cutoff 與時間設定擴展研究；每次只提出一個可反駁實驗，必要時才做明確聯合對照。",
            "主目標 entry-only PL，保留回撤、最差日、集中度與全部負面結果；不要把風險藏入評分或把曝光月份稱為獨立驗證。",
            "先驗證兩日接入，再根據完整月 baseline 與先前結果逐輪改善；明確分析設定的機制，缺能力或無可執行的新假設時停止並記錄。",
            "若已宣告策略設計能力，可探索砍單/額外入場 all/any 條件、交易時段與掛單/持倉限制。只用當時可得信號，不改模型輸入或期限處理。",
        ),
    )
