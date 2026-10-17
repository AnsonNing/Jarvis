"""Realized sibling trees backed by the durable local job/evidence ledger."""

import time
from collections import deque
from pathlib import Path
from typing import Any, Literal

from jarvis.campaign import code_provenance, source_identity
from jarvis.contracts import (
    Budget,
    Cost,
    Evaluation,
    IntegrityError,
    JarvisError,
    Observation,
    Record,
    ResearchState,
    ResearchTask,
)
from jarvis.evaluation import evaluate, evaluator_identity
from jarvis.exploration import ExplorationPolicy
from jarvis.identity import digest, file_digest
from jarvis.storage import Store, utcnow
from jarvis.suite import Fixture, SuiteAdapter, frontier, task_for


class Edge(Record):
    before: str
    after: str
    job_id: str
    request_ref: str
    receipt_ref: str
    evaluation_ref: str


class BranchTree(Record):
    kind: Literal["BranchTree"] = "BranchTree"
    collection_id: str
    root: str
    fixture: Fixture
    compatibility: str
    edges: tuple[Edge, ...]
    eligible_edges: int
    omitted_actions: tuple[str, ...] = ()
    origin: Literal["declared_fixture"] = "declared_fixture"


def compatibility(budget: Budget) -> str:
    return digest(
        {
            "tree_format": 1,
            "suite": file_digest(Path(__file__).with_name("suite.py")),
            "recorder": file_digest(Path(__file__)),
            "evaluator": evaluator_identity(),
            "budget": budget.model_dump(),
            "semantics": "independent-baseline-branches:1",
        }
    )


def fits(state: ResearchState, cost: Cost) -> bool:
    return all(
        value <= state.remaining_budget[key] + 1e-9 for key, value in cost.dimensions().items()
    )


def root_state(store: Store, collection: str) -> ResearchState:
    manifest = store.manifest(collection)
    task = ResearchTask.model_validate(manifest["path_task"])
    state = ResearchState(
        campaign_id=collection,
        world_id=manifest["world_id"],
        sequence=2,
        parent_node_id=None,
        visible_evidence_ids=(),
        observations=(),
        evaluations=(),
        remaining_budget=task.budget.limits(),
        available_actions=(),
        selected_policy_id=manifest.get("policy_id", "coverage-collector:1"),
    )
    return state.model_copy(update={"available_actions": frontier(task, state)})


def after_state(
    store: Store, collection: str, node: dict[str, Any], sequence: int
) -> ResearchState:
    request = store.artifacts.get(node["request_ref"])
    before = ResearchState.model_validate(request["state"])
    receipt = store.artifacts.get(node["receipt_ref"])
    cost = Cost.model_validate(receipt["cost"])
    task = ResearchTask.model_validate(store.manifest(collection)["path_task"])
    state = ResearchState(
        campaign_id=collection,
        world_id=before.world_id,
        sequence=sequence,
        parent_node_id=node["node_id"],
        visible_evidence_ids=(
            *before.visible_evidence_ids,
            receipt["observation_ref"],
            node["evaluation_ref"],
        ),
        observations=(*before.observations, Observation.model_validate(receipt["observation"])),
        evaluations=(
            *before.evaluations,
            Evaluation.model_validate(store.artifacts.get(node["evaluation_ref"])),
        ),
        remaining_budget={
            key: max(0.0, value - cost.dimensions()[key])
            for key, value in before.remaining_budget.items()
        },
        available_actions=(),
        selected_policy_id=before.selected_policy_id,
    )
    return state.model_copy(update={"available_actions": frontier(task, state)})


class Collector:
    def __init__(self, store: Store):
        self.store = store

    def create(
        self,
        fixture: Fixture,
        *,
        omit: tuple[str, ...] = (),
        budget: Budget | None = None,
        policy: ExplorationPolicy | None = None,
    ) -> str:
        task = task_for(fixture)
        if budget:
            task = task.model_copy(update={"budget": budget})
        if not set(omit) <= {"expand", "diagnose", "correct"}:
            raise JarvisError("only declared experimental edges may be omitted")
        return self.store.create(
            {
                "kind": "FreshPolicyCampaign" if policy else "BranchCollection",
                "policy": policy.model_dump(mode="json") if policy else None,
                "policy_id": policy.identity if policy else "coverage-collector:1",
                "task": task_for(fixture, collection=True).model_dump(mode="json"),
                "path_task": task.model_dump(mode="json"),
                "fixture": fixture.model_dump(mode="json"),
                "world_id": digest(
                    {"fixture": fixture.model_dump(), "protocol": compatibility(task.budget)}
                ),
                "compatibility": compatibility(task.budget),
                "source_identity": source_identity(),
                "code_provenance": code_provenance(),
                "omit": omit,
            }
        )

    def _settle(self, collection: str, job: dict[str, Any]) -> None:
        receipt = self.store.read_receipt(job["id"])
        if receipt is None:
            raise JarvisError("unknown started branch; reconciliation required")
        request = self.store.artifacts.get(job["request_ref"])
        state = ResearchState.model_validate(request["state"])
        obs = Observation.model_validate(receipt["observation"])
        if obs.action_id != request["action"]["action_id"]:
            raise IntegrityError("branch action/result mismatch")
        baseline = next(
            (
                o.metrics["objective_score"]
                for o in state.observations
                if "objective_score" in o.metrics
            ),
            None,
        )
        task = ResearchTask.model_validate(self.store.manifest(collection)["path_task"])
        evaluation = evaluate(task, obs, baseline, receipt["observation_ref"])
        self.store.finish_job(job["id"], self.store.artifacts.put(evaluation))

    def _execute(self, collection: str, job: dict[str, Any], fault: str | None) -> None:
        request = self.store.artifacts.get(job["request_ref"])
        self.store.start_job(job["id"])
        if fault == "after_start":
            raise JarvisError("injected branch interruption after start")
        started = time.monotonic()
        adapter = SuiteAdapter(Fixture.model_validate(self.store.manifest(collection)["fixture"]))
        obs = adapter.execute(request["action"]["action_id"])
        self.store.write_receipt(
            job["id"],
            {
                "job_id": job["id"],
                "request_ref": job["request_ref"],
                "world_id": request["world_id"],
                "observation": obs.model_dump(mode="json"),
                "observation_ref": self.store.artifacts.put(obs),
                "completed_at": utcnow(),
                "cost": Cost(
                    actions=1,
                    tool_calls=1,
                    compute_units=1,
                    wall_seconds=time.monotonic() - started,
                ).model_dump(mode="json"),
            },
        )
        if fault == "after_receipt":
            raise JarvisError("injected interruption after durable branch receipt")
        self._settle(collection, job)

    def run(self, collection: str, *, fault: str | None = None) -> str:
        with self.store.campaign_lock(collection):
            manifest = self.store.manifest(collection)
            if manifest["source_identity"] != source_identity():
                raise IntegrityError("collector source changed; create a new collection")
            sealed = [e for e in self.store.events(collection) if e["event_type"] == "TREE_SEALED"]
            if sealed:
                verify_tree(self.store, sealed[0]["payload"]["tree_ref"])
                return str(sealed[0]["payload"]["tree_ref"])
            for job in self.store.jobs(collection):
                if job["finalized"]:
                    continue
                if self.store.read_receipt(job["id"]) is not None:
                    self._settle(collection, job)
                elif job["status"] == "QUEUED":
                    self._execute(collection, job, fault)
                else:
                    raise JarvisError("unknown started branch; reconciliation required")
            root = self.store.artifacts.put(root_state(self.store, collection))
            queue = deque([root])
            edges = []
            eligible = 0
            while queue:
                before_ref = queue.popleft()
                state = ResearchState.model_validate(self.store.artifacts.get(before_ref))
                for action in state.available_actions:
                    if action.action_type == "STOP" or not fits(state, action.estimated_cost):
                        continue
                    eligible += 1
                    if action.action_id in manifest["omit"]:
                        continue
                    matches = [
                        j
                        for j in self.store.jobs(collection)
                        if self.store.artifacts.get(j["request_ref"]).get("before_ref")
                        == before_ref
                        and self.store.artifacts.get(j["request_ref"])["action"]["action_id"]
                        == action.action_id
                    ]
                    if matches:
                        job = matches[0]
                    else:
                        request = {
                            "before_ref": before_ref,
                            "state": state.model_dump(mode="json"),
                            "world_id": manifest["world_id"],
                            "action": action.model_dump(mode="json"),
                        }
                        job_id = self.store.reserve(collection, request, action.estimated_cost)
                        if fault == "after_reserve":
                            raise JarvisError("injected branch interruption after reservation")
                        job = next(j for j in self.store.jobs(collection) if j["id"] == job_id)
                        self._execute(collection, job, fault)
                    event = next(
                        e
                        for e in self.store.events(collection)
                        if e["event_type"] == "NODE_COMPLETED"
                        and e["payload"]["node_id"] == job["id"]
                    )
                    after = self.store.artifacts.put(
                        after_state(self.store, collection, event["payload"], event["sequence"])
                    )
                    node = event["payload"]
                    edges.append(
                        Edge(
                            before=before_ref,
                            after=after,
                            job_id=job["id"],
                            request_ref=node["request_ref"],
                            receipt_ref=node["receipt_ref"],
                            evaluation_ref=node["evaluation_ref"],
                        )
                    )
                    queue.append(after)
            tree = BranchTree(
                collection_id=collection,
                root=root,
                fixture=Fixture.model_validate(manifest["fixture"]),
                compatibility=manifest["compatibility"],
                edges=tuple(edges),
                eligible_edges=eligible,
                omitted_actions=tuple(manifest["omit"]),
            )
            ref = self.store.artifacts.put(tree)
            self.store.append(collection, "TREE_SEALED", {"tree_ref": ref})
            self.store.set_status(collection, "CLOSED")
            verify_tree(self.store, ref)
            return ref


def verify_tree(store: Store, ref: str) -> BranchTree:
    tree = BranchTree.model_validate(store.artifacts.get(ref))
    manifest = store.manifest(tree.collection_id)
    task = ResearchTask.model_validate(manifest["path_task"])
    if (
        manifest["kind"] != "BranchCollection"
        or tree.fixture.model_dump(mode="json") != manifest["fixture"]
        or tree.compatibility != manifest["compatibility"]
        or tree.compatibility != compatibility(task.budget)
    ):
        raise IntegrityError("incompatible tree manifest/compiler")
    if store.artifacts.get(tree.root) != root_state(store, tree.collection_id).model_dump(
        mode="json"
    ):
        raise IntegrityError("invalid tree root")
    events = store.events(tree.collection_id)
    sealed = [e for e in events if e["event_type"] == "TREE_SEALED"]
    if (
        len(sealed) != 1
        or sealed[0]["payload"]["tree_ref"] != ref
        or tree.omitted_actions != tuple(manifest["omit"])
    ):
        raise IntegrityError("tree is not the canonical sealed collection")
    completed = {e["payload"]["node_id"]: e for e in events if e["event_type"] == "NODE_COMPLETED"}
    jobs = {j["id"]: j for j in store.jobs(tree.collection_id)}
    if set(jobs) != set(completed) or set(jobs) != {e.job_id for e in tree.edges}:
        raise IntegrityError("tree/job ledger mismatch")
    known = {tree.root}
    pairs = set()
    for edge in tree.edges:
        if edge.before not in known:
            raise IntegrityError("invalid causal ancestry")
        state = ResearchState.model_validate(store.artifacts.get(edge.before))
        request = store.artifacts.get(edge.request_ref)
        receipt = store.artifacts.get(edge.receipt_ref)
        job = jobs[edge.job_id]
        event = completed[edge.job_id]
        evaluation = Evaluation.model_validate(store.artifacts.get(edge.evaluation_ref))
        action = request["action"]
        pair = (edge.before, action["action_id"])
        if (
            pair in pairs
            or request["before_ref"] != edge.before
            or request["state"] != state.model_dump(mode="json")
            or action not in [a.model_dump(mode="json") for a in state.available_actions]
            or not job["finalized"]
            or job["request_ref"] != edge.request_ref
            or job["receipt_ref"] != edge.receipt_ref
            or not fits(
                state,
                next(
                    a.estimated_cost
                    for a in state.available_actions
                    if a.action_id == action["action_id"]
                ),
            )
        ):
            raise IntegrityError("invalid branch admission or ledger")
        if (
            receipt["request_ref"] != edge.request_ref
            or receipt["job_id"] != edge.job_id
            or receipt["world_id"] != manifest["world_id"]
            or receipt["observation"] != store.artifacts.get(receipt["observation_ref"])
            or Cost.model_validate(receipt["cost"]) != Cost.model_validate_json(job["actual"])
            or receipt["observation"]["action_id"] != action["action_id"]
        ):
            raise IntegrityError("invalid branch receipt/cost")
        if (
            evaluation.evidence_refs != (receipt["observation_ref"],)
            or evaluation.evaluator_id != evaluator_identity()
            or evaluation.value != receipt["observation"]["metrics"].get("objective_score")
            or evaluation.scope != "development"
        ):
            raise IntegrityError("invalid branch evaluation")
        if (
            store.artifacts.get(edge.after)
            != after_state(
                store, tree.collection_id, event["payload"], event["sequence"]
            ).model_dump(mode="json")
            or event["payload"]["parent_id"] != state.parent_node_id
            or event["payload"]["evaluation_ref"] != edge.evaluation_ref
        ):
            raise IntegrityError("future sibling evidence or invalid transition")
        known.add(edge.after)
        pairs.add(pair)
    eligible = sum(
        1
        for state_ref in known
        for a in ResearchState.model_validate(store.artifacts.get(state_ref)).available_actions
        if a.action_type != "STOP"
        and fits(ResearchState.model_validate(store.artifacts.get(state_ref)), a.estimated_cost)
    )
    if eligible != tree.eligible_edges:
        raise IntegrityError("false branch coverage count")
    return tree
