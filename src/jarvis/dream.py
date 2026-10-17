"""Durable development-only policy search, one finalist, independent validation."""

import json
import time
from typing import Any, Literal

from pydantic import Field, ValidationError

from jarvis.branch_replay import aggregate, replay_tree
from jarvis.campaign import code_provenance, source_identity
from jarvis.contracts import Cost, IntegrityError, JarvisError, Record
from jarvis.discovery import Collector
from jarvis.exploration import ExplorationPolicy, fixture_proposals, incumbent
from jarvis.fresh import FreshRunner, verify_fresh
from jarvis.identity import digest
from jarvis.pool import HistoryPool, verify_pool
from jarvis.providers import CodexProvider, ResearchProvider
from jarvis.researcher import AgentConfig
from jarvis.storage import Store
from jarvis.suite import Fixture, task_for


class ProposalBatch(Record):
    kind: Literal["PolicyProposals"] = "PolicyProposals"
    development_ref: str
    proposals: tuple[ExplorationPolicy, ...] = Field(min_length=2, max_length=5)


def evaluate_cohort(
    store: Store, pool: HistoryPool, policy: ExplorationPolicy, cohort: str
) -> dict[str, Any]:
    results = []
    for member in pool.members:
        if member.cohort != cohort:
            continue
        results.append(
            {
                **replay_tree(store, member.tree_ref, policy, pool.protocol.target),
                "fixture_id": member.fixture.identity,
                "family": member.fixture.family,
            }
        )
    return aggregate(results)


def development_summary(
    store: Store, pool: HistoryPool, current: ExplorationPolicy
) -> dict[str, Any]:
    baseline = evaluate_cohort(store, pool, current, "development")
    observed = []
    for member in pool.members:
        if member.cohort != "development":
            continue
        tree = store.artifacts.get(member.tree_ref)
        samples = []
        for edge in tree["edges"]:
            receipt = store.artifacts.get(edge["receipt_ref"])
            samples.append(
                {
                    "action": receipt["observation"]["action_id"],
                    "status": receipt["observation"]["status"],
                    "metrics": receipt["observation"]["metrics"],
                    "findings": receipt["observation"]["findings"],
                    "cost": receipt["cost"],
                }
            )
        observed.append({"family": member.fixture.family, "samples": samples})
    return {
        "kind": "PolicyDevelopmentInput",
        "scope": "development_only",
        "incumbent": current.model_dump(mode="json"),
        "baseline": baseline,
        "observed_development_actions": observed,
        "allowed_actions": ["inspect", "expand", "diagnose", "correct", "stop"],
        "allowed_predicates": [
            "always",
            "baseline_sufficient",
            "diagnosed",
            "correction_supported",
            "failed",
            "improved",
        ],
        "budget": pool.protocol.budget.model_dump(mode="json"),
        "target": pool.protocol.target,
        "goal": "Improve target attainment under the unchanged budget; then reduce tool calls. No family-specific predicates.",
        "memory": "fixed empty in both arms",
    }


def proposal_schema(development_ref: str) -> dict[str, Any]:
    schema = ProposalBatch.model_json_schema()

    def strict(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in value.values():
                strict(child)
        elif isinstance(value, list):
            for child in value:
                strict(child)

    strict(schema)
    schema["properties"]["development_ref"]["enum"] = [development_ref]
    schema["$defs"]["ExplorationPolicy"]["properties"]["origin"]["enum"] = ["agent_proposed"]
    return schema


def better(candidate: dict[str, Any], base: dict[str, Any], *, strict: bool) -> bool:
    if not candidate["complete"] or not base["complete"] or candidate["worlds"] != base["worlds"]:
        return False
    c = {r["fixture_id"]: r for r in candidate["results"]}
    b = {r["fixture_id"]: r for r in base["results"]}
    if set(c) != set(b) or any(bool(b[k]["success"]) and not bool(c[k]["success"]) for k in b):
        return False
    if candidate["successes"] < base["successes"]:
        return False
    gain = (
        candidate["successes"] > base["successes"] or candidate["tool_calls"] < base["tool_calls"]
    )
    return (
        gain
        if strict
        else candidate["tool_calls"] <= base["tool_calls"]
        or candidate["successes"] > base["successes"]
    )


class DreamEngine:
    def __init__(self, store: Store, provider: ResearchProvider | None = None):
        self.store = store
        self.provider = provider

    def create(
        self,
        pool_ref: str,
        *,
        developer: Literal["fixture", "codex"] = "fixture",
        config: AgentConfig | None = None,
        current: ExplorationPolicy | None = None,
    ) -> str:
        pool = verify_pool(self.store, pool_ref)
        current = current or incumbent()
        config = config or AgentConfig(max_calls=1)
        task = task_for(pool.protocol.development[0], collection=True)
        task = task.model_copy(
            update={"budget": task.budget.model_copy(update={"max_llm_tokens": 100000})}
        )
        descriptor = None
        if developer == "codex":
            descriptor = (self.provider or CodexProvider(config)).identity()
        return self.store.create(
            {
                "kind": "DreamRound",
                "pool_ref": pool_ref,
                "task": task.model_dump(mode="json"),
                "world_id": digest(
                    {
                        "pool_ref": pool_ref,
                        "protocol": pool.protocol.model_dump(mode="json"),
                        "incumbent": current.identity,
                    }
                ),
                "incumbent": current.model_dump(mode="json"),
                "incumbent_id": current.identity,
                "developer": developer,
                "provider_identity": descriptor,
                "agent_config": config.model_dump(mode="json"),
                "source_identity": source_identity(),
                "code_provenance": code_provenance(),
            }
        )

    def _propose(
        self, round_id: str, pool: HistoryPool, current: ExplorationPolicy
    ) -> tuple[ExplorationPolicy, ...]:
        manifest = self.store.manifest(round_id)
        previous = [e for e in self.store.events(round_id) if e["event_type"] == "PROPOSALS_SEALED"]
        if previous:
            return tuple(
                ExplorationPolicy.model_validate(p)
                for p in self.store.artifacts.get(previous[0]["payload"]["proposals_ref"])
            )
        summary = development_summary(self.store, pool, current)
        summary_ref = self.store.artifacts.put(summary)
        proposals: tuple[ExplorationPolicy, ...]
        if manifest["developer"] == "fixture":
            proposals = fixture_proposals()
        else:
            config = AgentConfig.model_validate(manifest["agent_config"])
            provider = self.provider or CodexProvider(config)
            if provider.identity() != manifest["provider_identity"]:
                raise IntegrityError("frozen policy developer changed")
            jobs = self.store.jobs(round_id)
            if jobs:
                job = jobs[0]
            else:
                prompt = (
                    "You are a restricted research-exploration Policy Developer. Propose 2 to 5 distinct DSL policies using only this development evidence. Improve research action ordering under the fixed budget. Never generate code, call tools, read files, use family/world IDs, change the target, evaluator or budget. The incumbent is added by the kernel. Mark every proposal agent_proposed. Write rationale in Traditional Chinese. Return the required JSON schema.\n"
                    + json.dumps(
                        {"development_ref": summary_ref, "input": summary}, ensure_ascii=False
                    )
                )
                request = {
                    "world_id": manifest["world_id"],
                    "summary_ref": summary_ref,
                    "prompt": prompt,
                    "output_schema": proposal_schema(summary_ref),
                }
                jid = self.store.reserve(
                    round_id,
                    request,
                    Cost(llm_tokens=config.reserve_tokens, wall_seconds=config.timeout_seconds),
                )
                job = next(j for j in self.store.jobs(round_id) if j["id"] == jid)
            receipt = self.store.read_receipt(job["id"])
            if receipt is None:
                if job["status"] != "QUEUED":
                    raise JarvisError("unknown policy proposal completion; reconciliation required")
                self.store.start_job(job["id"])
                request = self.store.artifacts.get(job["request_ref"])
                try:
                    response = provider.complete(
                        request["prompt"],
                        request["output_schema"],
                        self.store.root / "provider-requests" / job["id"],
                    )
                except Exception as exc:
                    response = {
                        "answer": None,
                        "cost": None,
                        "usage": None,
                        "error": type(exc).__name__,
                    }
                receipt = {
                    **response,
                    "job_id": job["id"],
                    "request_ref": job["request_ref"],
                    "world_id": manifest["world_id"],
                }
                self.store.write_receipt(job["id"], receipt)
            usage = receipt.get("usage") or {}
            if (
                receipt.get("cost") is None
                or any(
                    type(usage.get(k)) is not int or usage[k] < 0
                    for k in ("input_tokens", "output_tokens")
                )
                or receipt["cost"]["llm_tokens"] != usage["input_tokens"] + usage["output_tokens"]
            ):
                raise JarvisError(
                    "policy developer usage unknown; reservation retained, no resubmission"
                )
            rejected = receipt.get("error")
            proposals = ()
            try:
                if rejected:
                    raise ValueError(str(rejected))
                batch = ProposalBatch.model_validate(receipt["answer"])
                if batch.development_ref != summary_ref or any(
                    p.origin != "agent_proposed" for p in batch.proposals
                ):
                    raise ValueError("proposal development scope/origin mismatch")
                if receipt["cost"]["llm_tokens"] > manifest["task"]["budget"]["max_llm_tokens"]:
                    raise ValueError("actual developer usage exceeded the sealed budget")
                proposals = batch.proposals
            except (ValueError, ValidationError) as exc:
                rejected = str(exc)[:1000]
            self.store.finish_auxiliary_job(
                job["id"],
                "POLICY_DEVELOPER_COMPLETED",
                {"rejection": rejected, "summary_ref": summary_ref},
            )
        all_proposals = (current, *tuple(p for p in proposals if p.identity != current.identity))
        unique = {p.identity: p for p in all_proposals}
        result = tuple(unique.values())
        self.store.append(
            round_id,
            "PROPOSALS_SEALED",
            {
                "summary_ref": summary_ref,
                "proposals_ref": self.store.artifacts.put(
                    [p.model_dump(mode="json") for p in result]
                ),
            },
        )
        return result

    def _fresh(
        self, round_id: str, pool: HistoryPool, base: ExplorationPolicy, finalist: ExplorationPolicy
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for fixture in pool.protocol.fresh:
            row: dict[str, Any] = {"fixture_id": fixture.identity, "family": fixture.family}
            for arm, policy in (("incumbent", base), ("finalist", finalist)):
                mapped = [
                    e["payload"]
                    for e in self.store.events(round_id)
                    if e["event_type"] == "FRESH_REGISTERED"
                    and e["payload"]["fixture_id"] == fixture.identity
                    and e["payload"]["arm"] == arm
                ]
                if mapped:
                    child = mapped[0]["campaign_id"]
                else:
                    child = Collector(self.store).create(
                        fixture, budget=pool.protocol.budget, policy=policy
                    )
                    self.store.append(
                        round_id,
                        "FRESH_REGISTERED",
                        {"fixture_id": fixture.identity, "arm": arm, "campaign_id": child},
                    )
                ref = FreshRunner(self.store).run(child)
                result = verify_fresh(self.store, ref)
                row[arm] = {
                    "result_ref": ref,
                    "campaign_id": child,
                    "success": result["success"],
                    "cost": result["cost"],
                    "value": result["value"],
                    "execution_status": result["execution_status"],
                }
            rows.append(row)
        summaries = {}
        for arm in ("incumbent", "finalist"):
            results = [
                {
                    "fixture_id": r["fixture_id"],
                    "family": r["family"],
                    "success": r[arm]["success"],
                    "cost": r[arm]["cost"],
                    "status": "COMPLETE"
                    if r[arm]["execution_status"] == "SUCCEEDED"
                    else r[arm]["execution_status"],
                }
                for r in rows
            ]
            summaries[arm] = aggregate(results)
        return {
            "scope": "FRESH_SYNTHETIC_ONLY",
            "pairs": rows,
            "incumbent": summaries["incumbent"],
            "finalist": summaries["finalist"],
            "passed": better(summaries["finalist"], summaries["incumbent"], strict=True),
            "uncertainty": "Few deterministic problem families; no population significance or Hermes transfer claim",
        }

    def run(self, round_id: str, *, fault: str | None = None) -> str:
        with self.store.campaign_lock(round_id):
            started = time.monotonic()
            manifest = self.store.manifest(round_id)
            if manifest["kind"] != "DreamRound" or manifest["source_identity"] != source_identity():
                raise IntegrityError("Dream runtime changed; new round required")
            events = self.store.events(round_id)
            reports = [e for e in events if e["event_type"] == "DREAM_COMPLETED"]
            if reports:
                ref = str(reports[0]["payload"]["report_ref"])
                verify_round(self.store, round_id)
                return ref
            pool = verify_pool(self.store, manifest["pool_ref"])
            base = ExplorationPolicy.model_validate(manifest["incumbent"])
            if base.identity != manifest["incumbent_id"]:
                raise IntegrityError("incumbent compiler identity changed")
            self.store.claim_policy_families(
                round_id,
                ["suite-family:1:" + f.family for f in pool.protocol.development],
                "development",
            )
            proposals = self._propose(round_id, pool, base)
            frozen = [
                e for e in self.store.events(round_id) if e["event_type"] == "FINALIST_FROZEN"
            ]
            if frozen:
                selected = frozen[0]["payload"]
                finalist = ExplorationPolicy.model_validate(
                    self.store.artifacts.get(selected["policy_ref"])
                )
                development = self.store.artifacts.get(selected["development_ref"])
            else:
                development = {
                    p.identity: evaluate_cohort(self.store, pool, p, "development")
                    for p in proposals
                }
                eligible = [
                    p
                    for p in proposals
                    if p.identity != base.identity
                    and better(development[p.identity], development[base.identity], strict=True)
                ]
                finalist = (
                    max(
                        eligible,
                        key=lambda p: (
                            development[p.identity]["successes"],
                            -development[p.identity]["tool_calls"],
                            -len(p.rules),
                            p.identity,
                        ),
                    )
                    if eligible
                    else base
                )
                self.store.append(
                    round_id,
                    "FINALIST_FROZEN",
                    {
                        "policy_ref": self.store.artifacts.put(finalist),
                        "policy_id": finalist.identity,
                        "development_ref": self.store.artifacts.put(development),
                        "selection_scope": "development_only",
                    },
                )
                if fault == "after_finalist":
                    raise JarvisError("injected interruption after finalist freeze")
            heldout = None
            fresh = None
            status = "NO_PROMOTION"
            reason = "No fully covered development candidate beats the incumbent"
            heldout_available = (
                self.store.claim_policy_families(
                    round_id,
                    ["suite-family:1:" + f.family for f in pool.protocol.heldout],
                    "heldout",
                )
                if finalist.identity != base.identity
                else False
            )
            if finalist.identity != base.identity and not heldout_available:
                reason = (
                    "Policy-heldout families already exposed; independent new families required"
                )
            if finalist.identity != base.identity and heldout_available:
                prior = [
                    e for e in self.store.events(round_id) if e["event_type"] == "HELDOUT_REVEALED"
                ]
                if prior:
                    heldout = self.store.artifacts.get(prior[0]["payload"]["result_ref"])
                else:
                    b = evaluate_cohort(self.store, pool, base, "heldout")
                    c = evaluate_cohort(self.store, pool, finalist, "heldout")
                    heldout = {"incumbent": b, "finalist": c, "passed": better(c, b, strict=False)}
                    self.store.append(
                        round_id,
                        "HELDOUT_REVEALED",
                        {
                            "result_ref": self.store.artifacts.put(heldout),
                            "frozen_policy_id": finalist.identity,
                        },
                    )
                reason = "Independent policy-heldout gate failed or lacks coverage"
                fresh_available = (
                    self.store.claim_policy_families(
                        round_id,
                        ["suite-family:1:" + f.family for f in pool.protocol.fresh],
                        "fresh",
                    )
                    if heldout["passed"]
                    else False
                )
                if heldout["passed"] and not fresh_available:
                    reason = "Fresh validation families already exposed; independent new families required"
                if heldout["passed"] and fresh_available:
                    fresh = self._fresh(round_id, pool, base, finalist)
                    reason = "Fresh paired campaign regression or no reliable fixture benefit"
                    if fresh["passed"]:
                        status = "APPROVED_FOR_RESEARCH"
                        reason = "Independent replay and new paired fixture campaigns passed; synthetic scope only"
            collection_ids = [
                self.store.artifacts.get(m.tree_ref)["collection_id"] for m in pool.members
            ]
            original = {
                k: sum(self.store.resource_totals(cid)["actual"][k] for cid in collection_ids)
                for k in Cost().dimensions()
            }
            report = {
                "kind": "DreamReport",
                "round_id": round_id,
                "status": status,
                "reason": reason,
                "scope": "SYNTHETIC_DREAM_RSI",
                "pool_ref": manifest["pool_ref"],
                "incumbent_id": base.identity,
                "finalist_id": finalist.identity,
                "active_policy_id": finalist.identity
                if status == "APPROVED_FOR_RESEARCH"
                else base.identity,
                "policy_ref": self.store.artifacts.put(finalist),
                "development": development,
                "heldout": heldout,
                "fresh": fresh,
                "original_history_collection_cost": original,
                "dreaming_usage": self.store.resource_totals(round_id),
                "coordinator_seconds_this_attempt": time.monotonic() - started,
                "replay_external_calls": 0,
                "base_agent": "fixed DSL executor; same adapter/tools/empty memory in both arms",
                "limitations": [
                    "Synthetic engineering proof only",
                    "No L2 capability learning, Hermes transfer or financial performance claim",
                    "Historical replay non-regression does not guarantee future non-regression",
                ],
            }
            ref = self.store.artifacts.put(report)
            self.store.append(round_id, "DREAM_COMPLETED", {"report_ref": ref})
            self.store.set_status(round_id, "CLOSED")
            verify_round(self.store, round_id)
            return ref


def verify_round(store: Store, round_id: str) -> dict[str, Any]:
    manifest = store.manifest(round_id)
    pool = verify_pool(store, manifest["pool_ref"])
    events = store.events(round_id)
    frozen = [e for e in events if e["event_type"] == "FINALIST_FROZEN"]
    reports = [e for e in events if e["event_type"] == "DREAM_COMPLETED"]
    if len(frozen) != 1 or len(reports) != 1:
        raise IntegrityError("round requires exactly one frozen finalist and report")
    freeze = frozen[0]
    for event in events:
        if (
            event["event_type"] in {"HELDOUT_REVEALED", "FRESH_REGISTERED"}
            and event["sequence"] <= freeze["sequence"]
        ):
            raise IntegrityError("policy validation leaked before finalist freeze")
    report = store.artifacts.get(reports[0]["payload"]["report_ref"])
    base = ExplorationPolicy.model_validate(manifest["incumbent"])
    finalist = ExplorationPolicy.model_validate(
        store.artifacts.get(freeze["payload"]["policy_ref"])
    )
    proposed = [e for e in events if e["event_type"] == "PROPOSALS_SEALED"]
    if len(proposed) != 1 or proposed[0]["sequence"] >= freeze["sequence"]:
        raise IntegrityError("proposals not frozen before selection")
    proposals = [
        ExplorationPolicy.model_validate(p)
        for p in store.artifacts.get(proposed[0]["payload"]["proposals_ref"])
    ]
    if sum(p.identity == base.identity for p in proposals) != 1:
        raise IntegrityError("incumbent missing or modified")
    summary_ref = proposed[0]["payload"]["summary_ref"]
    if digest(development_summary(store, pool, base)) != summary_ref:
        raise IntegrityError("policy developer received undeclared evidence")
    development = {p.identity: evaluate_cohort(store, pool, p, "development") for p in proposals}
    eligible = [
        p
        for p in proposals
        if p.identity != base.identity
        and better(development[p.identity], development[base.identity], strict=True)
    ]
    selected = (
        max(
            eligible,
            key=lambda p: (
                development[p.identity]["successes"],
                -development[p.identity]["tool_calls"],
                -len(p.rules),
                p.identity,
            ),
        )
        if eligible
        else base
    )
    if (
        selected.identity != finalist.identity
        or digest(development) != digest(report["development"])
        or digest(development) != freeze["payload"]["development_ref"]
    ):
        raise IntegrityError("finalist selection is not development-only or scores changed")
    if (
        report["finalist_id"] != freeze["payload"]["policy_id"]
        or report["finalist_id"] != finalist.identity
        or report["incumbent_id"] != base.identity
        or report["pool_ref"] != manifest["pool_ref"]
    ):
        raise IntegrityError("finalist changed after freeze")
    if report["heldout"]:
        b = evaluate_cohort(store, pool, base, "heldout")
        c = evaluate_cohort(store, pool, finalist, "heldout")
        expected = {"incumbent": b, "finalist": c, "passed": better(c, b, strict=False)}
        reveal = [e for e in events if e["event_type"] == "HELDOUT_REVEALED"]
        if (
            digest(expected) != digest(report["heldout"])
            or len(reveal) != 1
            or reveal[0]["payload"]["result_ref"] != digest(expected)
        ):
            raise IntegrityError("heldout comparison differs from sealed evidence")
    if report["fresh"]:
        if {p["fixture_id"] for p in report["fresh"]["pairs"]} != {
            f.identity for f in pool.protocol.fresh
        } or len(report["fresh"]["pairs"]) != len(pool.protocol.fresh):
            raise IntegrityError("fresh family cohort changed")
        for pair in report["fresh"]["pairs"]:
            for arm in ("incumbent", "finalist"):
                checked = verify_fresh(store, pair[arm]["result_ref"])
                if (
                    checked["policy_id"]
                    != report["incumbent_id" if arm == "incumbent" else "finalist_id"]
                    or checked["success"] != pair[arm]["success"]
                ):
                    raise IntegrityError("fresh arm evidence mismatch")
                child = store.manifest(checked["campaign_id"])
                if (
                    checked["cost"] != pair[arm]["cost"]
                    or child["path_task"]["budget"] != pool.protocol.budget.model_dump(mode="json")
                    or Fixture.model_validate(checked["fixture"]).identity != pair["fixture_id"]
                ):
                    raise IntegrityError("fresh matched budget or cost changed")
        summaries = {}
        for arm in ("incumbent", "finalist"):
            summaries[arm] = aggregate(
                [
                    {
                        "fixture_id": r["fixture_id"],
                        "family": r["family"],
                        "success": r[arm]["success"],
                        "cost": r[arm]["cost"],
                        "status": "COMPLETE"
                        if r[arm]["execution_status"] == "SUCCEEDED"
                        else r[arm]["execution_status"],
                    }
                    for r in report["fresh"]["pairs"]
                ]
            )
        if any(
            digest(summaries[arm]) != digest(report["fresh"][arm]) for arm in summaries
        ) or report["fresh"]["passed"] != better(
            summaries["finalist"], summaries["incumbent"], strict=True
        ):
            raise IntegrityError("fresh comparison gate changed")
    if report["status"] == "APPROVED_FOR_RESEARCH" and (
        not report["heldout"]
        or not report["heldout"]["passed"]
        or not report["fresh"]
        or not report["fresh"]["passed"]
    ):
        raise IntegrityError("promotion without independent evidence")
    expected_active = (
        finalist.identity if report["status"] == "APPROVED_FOR_RESEARCH" else base.identity
    )
    if report["active_policy_id"] != expected_active or report[
        "dreaming_usage"
    ] != store.resource_totals(round_id):
        raise IntegrityError("active policy or provider usage mismatch")
    return {
        "status": "VERIFIED",
        "round_id": round_id,
        "report_ref": reports[0]["payload"]["report_ref"],
        "decision": report["status"],
    }
