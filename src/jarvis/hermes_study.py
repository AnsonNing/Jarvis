"""Bounded real-model development rounds with paid, causal prior-round review."""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from jarvis.campaign import CampaignEngine, code_provenance, source_identity
from jarvis.contracts import IntegrityError, JarvisError, Record, ResearchTask
from jarvis.hermes_new import NewBaselineAdapter
from jarvis.history import verify
from jarvis.identity import digest, file_digest
from jarvis.providers import ResearchProvider
from jarvis.replay import replay
from jarvis.reporting import report
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


class HermesStudySpec(Record):
    kind: Literal["HermesStudySpec"] = "HermesStudySpec"
    root: str
    side: Literal["long", "short"]
    artifacts: str
    ledger_root: str
    objective: str | None = Field(default=None, min_length=1, max_length=2000)
    rounds: Annotated[int, Field(ge=2, le=3, strict=True)] = 2
    agent: AgentConfig = AgentConfig(max_calls=8)
    tokens_per_round: Annotated[int, Field(ge=32768, le=400000, strict=True)] = 250000


def prior_findings(store: Store, campaigns: list[str], metadata_hash: str) -> dict[str, Any]:
    summaries = []
    refs = []
    for cid in campaigns:
        verify(store, cid)
        value = report(store, cid)
        refs.append(digest(value))
        store.artifacts.get(refs[-1])
        turns = [a["turn"] for a in value["agent_attempts"] if a["turn"]]
        summaries.append(
            {
                "campaign_id": cid,
                "scope": value["scope"],
                "status": value["status"],
                "comparisons": [
                    {
                        "tool": e["action"],
                        "parameters": store.artifacts.get(
                            next(
                                n["payload"]["request_ref"]
                                for n in store.events(cid)
                                if n["event_type"] == "NODE_COMPLETED"
                                and n["payload"]["node_id"] == e["node_id"]
                            )
                        )["action"]["arguments"],
                        "metrics": e["metrics"],
                        "receipt_ref": e["receipt_ref"],
                    }
                    for e in value["experiments"]
                    if e["action"]
                    in {"threshold_experiment", "cancel_experiment", "joint_experiment"}
                ],
                "advisory_analysis": turns[-1]["analysis_summary"][:3500] if turns else None,
            }
        )
    return {
        "kind": "HermesPriorRound",
        "baseline_metadata_sha256": metadata_hash,
        "source_report_refs": refs,
        "prior_development_results": summaries,
        "reuse_scope": "same exposed calibration only; not independent validation",
        "interpretation_origin": "prior agent analysis, advisory; numeric values are verified tool receipts",
    }


def runtime_lineage(store: Store, study_id: str) -> str:
    """Audit explicit runtime upgrades only at fully settled round boundaries."""
    manifest = store.manifest(study_id)
    active = str(manifest["source_identity"])
    registered: list[str] = []
    for event in store.events(study_id):
        payload = event["payload"]
        if event["event_type"] == "HERMES_ROUND_REGISTERED":
            cid = payload["campaign_id"]
            if payload["index"] != len(registered) or cid in registered:
                raise IntegrityError("duplicate or unordered Hermes round")
            if store.manifest(cid)["world"]["engine_source"] != active:
                raise IntegrityError("round runtime differs from the admitted study version")
            registered.append(cid)
        if event["event_type"] == "HERMES_STUDY_RUNTIME_UPGRADED":
            if (
                payload["from_source"] != active
                or payload["code_provenance"]["source_identity"] != payload["to_source"]
            ):
                raise IntegrityError("study runtime upgrade chain differs")
            expected = []
            for cid in registered:
                closed = [
                    e
                    for e in store.events(cid)
                    if e["event_type"] == "STATUS" and e["payload"]["status"] == "CLOSED"
                ]
                if (
                    not closed
                    or closed[-1]["recorded_at_utc"] > event["recorded_at_utc"]
                    or any(not j["finalized"] or not j["actual"] for j in store.jobs(cid))
                ):
                    raise IntegrityError("runtime upgrade crossed an unsettled round")
                value = report(store, cid)
                ref = digest(value)
                store.artifacts.get(ref)
                expected.append({"campaign_id": cid, "report_ref": ref})
            if payload["closed_rounds"] != expected or not expected:
                raise IntegrityError("runtime upgrade lacks sealed completed-round evidence")
            active = payload["to_source"]
    return active


class HermesStudy:
    def __init__(
        self,
        store: Store,
        provider_factory: Callable[[AgentConfig], ResearchProvider] | None = None,
    ):
        self.store = store
        self.engine = CampaignEngine(store, provider_factory=provider_factory)

    def create(self, spec: HermesStudySpec) -> str:
        adapter = NewBaselineAdapter(
            Path(spec.root), spec.side, Path(spec.artifacts), Path(spec.ledger_root)
        )
        adapter.validate(adapter.task())
        if spec.tokens_per_round < spec.agent.reserve_tokens:
            raise JarvisError("round budget does not cover one provider reservation")
        return self.store.create(
            {
                "kind": "RealHermesStudy",
                "spec": spec.model_dump(mode="json"),
                "baseline_hashes": adapter.hashes(),
                "metadata_sha256": file_digest(Path(spec.artifacts) / "model_metadata.json"),
                "source_identity": source_identity(),
                "code_provenance": code_provenance(),
                "limits": {
                    "max_campaigns": spec.rounds,
                    "max_provider_calls": spec.rounds * spec.agent.max_calls,
                    "max_llm_tokens": spec.rounds * spec.tokens_per_round,
                },
                "scope": "same_exposed_calibration_development_only",
            }
        )

    def admit_runtime_upgrade(self, study_id: str) -> dict[str, Any]:
        """Explicit local migration; never alters a child world or resends a job."""
        with self.store.campaign_lock(study_id):
            manifest = self.store.manifest(study_id)
            if manifest["kind"] != "RealHermesStudy":
                raise IntegrityError("not a Hermes study")
            before = runtime_lineage(self.store, study_id)
            current = source_identity()
            if before == current:
                raise JarvisError("study already admits the current runtime")
            spec = HermesStudySpec.model_validate(manifest["spec"])
            clean = NewBaselineAdapter(
                Path(spec.root), spec.side, Path(spec.artifacts), Path(spec.ledger_root)
            )
            clean.validate(clean.task())
            if clean.hashes() != manifest["baseline_hashes"]:
                raise IntegrityError("cannot upgrade a study with changed baseline inputs")
            closed = []
            for event in self.store.events(study_id):
                if event["event_type"] != "HERMES_ROUND_REGISTERED":
                    continue
                cid = event["payload"]["campaign_id"]
                if self.store.status(cid) != "CLOSED" or any(
                    not j["finalized"] or not j["actual"] for j in self.store.jobs(cid)
                ):
                    raise JarvisError("runtime upgrade requires fully settled CLOSED children")
                value = report(self.store, cid)
                closed.append({"campaign_id": cid, "report_ref": self.store.artifacts.put(value)})
            if not closed:
                raise JarvisError("no completed round to preserve; create a new study")
            value = {
                "from_source": before,
                "to_source": current,
                "code_provenance": code_provenance(),
                "closed_rounds": closed,
                "semantics": "Completed worlds retained; only subsequent child campaigns use the admitted runtime. No provider/tool resubmission.",
            }
            self.store.append(study_id, "HERMES_STUDY_RUNTIME_UPGRADED", value)
            runtime_lineage(self.store, study_id)
            return value

    def run(self, study_id: str, *, fault: str | None = None) -> str:
        with self.store.campaign_lock(study_id):
            manifest = self.store.manifest(study_id)
            previous = [
                e
                for e in self.store.events(study_id)
                if e["event_type"] == "HERMES_STUDY_COMPLETED"
            ]
            if previous:
                ref = str(previous[0]["payload"]["report_ref"])
                verify_hermes_study(self.store, study_id)
                return ref
            if runtime_lineage(self.store, study_id) != source_identity():
                raise IntegrityError(
                    "Hermes study runtime changed; explicitly admit a settled-round upgrade or create a new study"
                )
            spec = HermesStudySpec.model_validate(manifest["spec"])
            clean = NewBaselineAdapter(
                Path(spec.root), spec.side, Path(spec.artifacts), Path(spec.ledger_root)
            )
            clean.validate(clean.task())
            if clean.hashes() != manifest["baseline_hashes"]:
                raise IntegrityError("frozen real study inputs changed")
            campaigns: list[str] = []
            rows = []
            for index in range(spec.rounds):
                registered = [
                    e["payload"]
                    for e in self.store.events(study_id)
                    if e["event_type"] == "HERMES_ROUND_REGISTERED"
                    and e["payload"]["index"] == index
                ]
                if registered:
                    cid = registered[0]["campaign_id"]
                else:
                    prior_ref = (
                        self.store.artifacts.put(
                            prior_findings(self.store, campaigns, manifest["metadata_sha256"])
                        )
                        if campaigns
                        else None
                    )
                    adapter = NewBaselineAdapter(
                        Path(spec.root),
                        spec.side,
                        Path(spec.artifacts),
                        Path(spec.ledger_root),
                        self.store.artifacts.path(prior_ref) if prior_ref else None,
                    )
                    goal = f"研究新 Hermes {spec.side.upper()} 凍結基準，這是第 {index + 1} 輪、共 {spec.rounds} 輪的校準期開發研究。先檢查來源與基準。若有 prior_round_review，必須讀取並依前輪實測結果改變研究方向，勿重做相同設定。提出可反駁假設，最多做兩個不同的受控參數實驗；每次先分析上一個實測結果再決定下一個。優先辨別 Cancel 犧牲獲利、風險門檻尺度與等待時間等競爭解釋，必要時用可用診斷。只有在單因素證據支持時才設計明確的聯合比較。最多一次額外診斷後停止並整理負面結果、取捨與缺項。不能重訓、修改凍結設定、讀九月結果或提交外部回測；停利停損缺少價格路徑時應指出所需能力。所有結果只是同一已曝光校準期的研究，不是獨立策略驗證或部署授權。"
                    if spec.objective:
                        goal += " 人類指定補充目標：" + spec.objective
                    task = adapter.task()
                    task = ResearchTask.model_validate(
                        {
                            **task.model_dump(mode="json"),
                            "goal": goal,
                            "hypotheses_allowed": [*task.hypotheses_allowed, "parameter_change"],
                            "budget": {
                                **task.budget.model_dump(),
                                "max_llm_tokens": spec.tokens_per_round,
                                "max_wall_seconds": 1800,
                            },
                        }
                    )
                    cid = self.engine.create(task, adapter, researcher=spec.agent)
                    self.store.append(
                        study_id,
                        "HERMES_ROUND_REGISTERED",
                        {"index": index, "campaign_id": cid, "prior_context_ref": prior_ref},
                    )
                status = self.store.status(cid)
                if status != "CLOSED":
                    status = self.engine.run(cid)
                if status == "NEEDS_RECONCILIATION":
                    self.store.set_status(study_id, status)
                    raise JarvisError(
                        "real provider/tool job requires reconciliation; no resubmission"
                    )
                value = report(self.store, cid)
                report_ref = self.store.artifacts.put(value)
                verify(self.store, cid)
                replayed = replay(self.store, cid)
                row = {
                    "round": index + 1,
                    "campaign_id": cid,
                    "status": status,
                    "report_ref": report_ref,
                    "replay_ref": self.store.artifacts.put(replayed),
                    "usage": value["resource_totals"],
                    "experiment_count": sum(
                        e["action"]
                        in {"threshold_experiment", "cancel_experiment", "joint_experiment"}
                        for e in value["experiments"]
                    ),
                }
                if not any(
                    e["event_type"] == "HERMES_ROUND_RECORDED"
                    and e["payload"]["round"] == index + 1
                    for e in self.store.events(study_id)
                ):
                    self.store.append(study_id, "HERMES_ROUND_RECORDED", row)
                rows.append(row)
                campaigns.append(cid)
                if fault == "after_round":
                    raise JarvisError("injected study interruption after completed round")
            if clean.hashes() != manifest["baseline_hashes"]:
                raise IntegrityError("protected baseline changed during real research")
            cost = {
                key: sum(r["usage"]["actual"][key] for r in rows)
                for key in rows[0]["usage"]["actual"]
            }
            pending = {
                key: sum(r["usage"]["pending"][key] for r in rows)
                for key in rows[0]["usage"]["pending"]
            }
            result = {
                "kind": "RealHermesStudyReport",
                "study_id": study_id,
                "side": spec.side,
                "status": "CLOSED" if all(r["status"] == "CLOSED" for r in rows) else "PARTIAL",
                "scope": manifest["scope"],
                "rounds": rows,
                "actual": cost,
                "pending": pending,
                "baseline_unchanged": True,
                "training_runs": 0,
                "external_backtest_submissions": 0,
                "formal_strategy_validation": "NOT_RUN",
                "policy_promotion": "NOT_RUN",
                "capability_audit": clean.backend_requirements(),
                "conclusion": "Round-to-round evidence reuse and adaptive real LLM experiments on exposed calibration; no independent trading or exploration-policy gain is established",
            }
            ref = self.store.artifacts.put(result)
            self.store.append(study_id, "HERMES_STUDY_COMPLETED", {"report_ref": ref})
            self.store.set_status(study_id, result["status"])
            verify_hermes_study(self.store, study_id)
            return ref


def verify_hermes_study(store: Store, study_id: str) -> dict[str, Any]:
    manifest = store.manifest(study_id)
    spec = HermesStudySpec.model_validate(manifest["spec"])
    runtime_lineage(store, study_id)
    events = store.events(study_id)
    final = [e for e in events if e["event_type"] == "HERMES_STUDY_COMPLETED"]
    if len(final) != 1:
        raise IntegrityError("real study has no unique final report")
    value = store.artifacts.get(final[0]["payload"]["report_ref"])
    rows = [e["payload"] for e in events if e["event_type"] == "HERMES_ROUND_RECORDED"]
    if rows != value["rounds"] or len(rows) != spec.rounds:
        raise IntegrityError("real study round count/report mismatch")
    previous: list[str] = []
    for index, row in enumerate(rows):
        cid = row["campaign_id"]
        verify(store, cid)
        checked = report(store, cid)
        if (
            digest(checked) != row["report_ref"]
            or checked["resource_totals"] != row["usage"]
            or checked["status"] != row["status"]
        ):
            raise IntegrityError("real round evidence/cost changed")
        mapping = next(
            e
            for e in events
            if e["event_type"] == "HERMES_ROUND_REGISTERED" and e["payload"]["index"] == index
        )
        if mapping["payload"]["campaign_id"] != cid:
            raise IntegrityError("real campaign lineage changed")
        context = mapping["payload"]["prior_context_ref"]
        expected = (
            prior_findings(store, previous, manifest["metadata_sha256"]) if previous else None
        )
        if (digest(expected) if expected is not None else None) != context:
            raise IntegrityError("prior review has hindsight, unverified metrics or another model")
        if context and store.manifest(cid)["local_adapter"]["prior_context"] != str(
            store.artifacts.path(context)
        ):
            raise IntegrityError("prior context not bound to the campaign")
        if store.artifacts.get(row["replay_ref"]) != replay(store, cid):
            raise IntegrityError("recorded zero-call replay changed")
        previous.append(cid)
    actual = {k: sum(r["usage"]["actual"][k] for r in rows) for k in value["actual"]}
    pending = {k: sum(r["usage"]["pending"][k] for r in rows) for k in value["pending"]}
    if value["actual"] != actual or value["pending"] != pending:
        raise IntegrityError("real study aggregate cost mismatch")
    return {
        "status": "VERIFIED",
        "study_id": study_id,
        "rounds": len(rows),
        "report_ref": final[0]["payload"]["report_ref"],
        "actual": actual,
        "pending": pending,
    }
