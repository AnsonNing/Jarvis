"""Scripted agents and mathematical objectives, explicitly not market discovery."""

import json
from pathlib import Path
from typing import Any

from jarvis.contracts import Budget, Cost, IntegrityError
from jarvis.critic_fixture import CriticFixture
from jarvis.identity import digest, file_digest
from jarvis.iris_fixture import FixtureIrisTransport
from jarvis.lab_contracts import LabPolicy, LabSpec, ResearchMethod, SourceDocument
from jarvis.lab_evolution import Axis
from jarvis.lab_memory import capability_definition
from jarvis.research_fixture import (
    FixtureBackends,
    fixture_environment,
    fixture_plan,
    fixture_trial,
)
from jarvis.research_lab import ResearchLab, lab_report
from jarvis.research_program import ResearchProgramSpec
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


class LabFixtureProvider:
    def identity(self) -> dict[str, Any]:
        return {
            "provider": "declared-lab-control",
            "usage_origin": "SIMULATED_TOKEN_UNITS",
            "source": file_digest(Path(__file__)),
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        c = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        context_id = c["context_id"]
        kind = schema.get("title")
        if kind == "CriticReview":
            return CriticFixture().complete(prompt, schema, directory)
        if kind == "DirectionBatch":
            support = c.get("research_support", {})
            method = support.get("method", {})
            selected = "entry"
            if method.get("evidence_first"):
                for observation in support.get("observations", []):
                    value = observation["value"]
                    if observation["action"] == "diagnose" and value.get("best_group") in {
                        "entry",
                        "exit",
                    }:
                        selected = value["best_group"]
                    if observation["action"] == "retrieve" and value.get("accepted"):
                        exp = value["accepted"][0]["experience"]
                        best = max(exp["observations"], key=lambda e: e["result"]["entry_only_pl"])
                        if best["experiment"]["family"] != "baseline":
                            selected = best["experiment"]["family"]
            trial = fixture_trial(
                "lab-" + selected,
                selected,
                **(
                    {"take_profit_bps": 20}
                    if selected == "exit"
                    else {"trading_cutoff_delta": 0.0004}
                ),
            )
            trial = trial.model_copy(
                update={
                    "origin": "agent_proposed",
                    "evidence_refs": tuple(c["visible_evidence_ids"]),
                }
            )
            answer = {
                "schema_version": 1,
                "kind": "ResearchDirections",
                "context_id": context_id,
                "analysis_summary": "Declared artificial control; decision follows selected method and visible diagnostic/experience",
                "experiments": [trial.model_dump(mode="json")],
                "capability_requests": [],
                "stop_reason": None,
            }
        elif kind == "TeamPlan":
            answer = {
                "kind": "ResearchTeamPlan",
                "context_id": context_id,
                "roles": c["allowed_roles"],
                "reason": "Consult distinct declared mechanisms and measurement perspectives",
            }
        elif kind == "ExpertOpinion":
            target = "analyst" if c["expert"] == "mechanism" else "mechanism"
            answer = {
                "kind": "ExpertOpinion",
                "context_id": context_id,
                "hypothesis": "Mechanism-specific explanation from declared artificial sources: "
                + c["expert"],
                "counterargument": "This is an engineering hypothesis, not an observed causal or financial conclusion",
                "test": "Use one-factor fixed-baseline comparison; cite actual results",
                "evidence_refs": c["visible_evidence_ids"],
                "question_for": target if target in c["allowed_roles"] else None,
                "question": "Which counterexample distinguishes this explanation?",
            }
        elif kind == "ExperienceProposal":
            refs = c["visible_evidence_ids"]
            answer = {
                "kind": "ExperienceProposal",
                "context_id": context_id,
                "claim": "Retain measured single-factor outcomes within this artificial family",
                "limitations": "One development task; interpretations remain provisional; no market transfer",
                "evidence_refs": refs,
                "counter_evidence_refs": refs[:1],
            }
        elif kind == "CapabilityProposal":
            answer = {
                "context_id": context_id,
                "capability": {
                    "kind": "ResearchCapability",
                    "name": "grouped-development-diagnostic",
                    "operation": "group_mean_argmax",
                    "source_refs": [
                        r["ref"] for r in c["evidence"] if r["value"]["kind"] == "SourceDocument"
                    ],
                    "rationale": "Compile a closed numerical recipe; source only motivates it; fixed cases test fidelity",
                    "origin": "declared_fixture",
                },
            }
        elif kind == "CapabilityChoice":
            answer = {
                "context_id": context_id,
                "capability_ref": c["available_capabilities"][0]["ref"]
                if c["input_groups"]
                else None,
                "reason": "Select a declared registered diagnostic compatible with the supplied development input groups",
            }
        elif kind == "MethodCandidates":
            base = ResearchMethod.model_validate(c["incumbent"])
            improved = base.model_copy(
                update={
                    "name": "Evidence-M1",
                    "evidence_first": True,
                    "director_guidance": "Use scoped actual diagnostic observations and experience before forming the intervention. Preserve counter-evidence and use one-factor controls.",
                    "origin": "declared_fixture",
                }
            )
            answer = {"context_id": context_id, "methods": [improved.model_dump(mode="json")]}
        elif kind == "PolicyCandidates":
            improved_policy = LabPolicy(
                name="Focused-Q1", actions=("retrieve", "diagnose"), origin="declared_fixture"
            )
            answer = {
                "context_id": context_id,
                "policies": [improved_policy.model_dump(mode="json")],
            }
        else:
            raise IntegrityError("unsupported declared lab schema: " + str(kind))
        answer.setdefault("schema_version", 1)
        return {
            "answer": answer,
            "cost": Cost(llm_tokens=100, wall_seconds=0.01).model_dump(),
            "usage": {"input_tokens": 80, "output_tokens": 20},
            "error": None,
        }


class FixtureSourceSearch:
    def identity(self) -> dict[str, Any]:
        return {"adapter": "declared-synthetic-sources", "version": 1}

    def search(self, public_question: str) -> list[SourceDocument]:
        return [
            SourceDocument(
                uri="urn:jarvis:fixture:grouped-diagnostics",
                title="Declared grouped development diagnostic",
                text="For the artificial control, compute means of declared development observations by group and compare them. This does not establish a causal effect or future result.",
                version="1",
                origin="declared_fixture",
                read_scope="full_text",
                license_note="Synthetic engineering source, no real paper or market claim",
            )
        ]


class LabMathematicalBackend(FixtureIrisTransport):
    """Additional truly different artificial functions for separate validation cohorts."""

    def identity(self) -> dict[str, Any]:
        return {**super().identity(), "lab_mathematical_controls": file_digest(Path(__file__))}

    def collect(self, request: dict[str, Any], directory: Path) -> dict[str, Any]:
        import math
        import re

        output = super().collect(request, directory)
        family = self.environment.model_lineage.rsplit("-", 1)[0]
        index = int(self.environment.model_lineage.rsplit("-", 1)[1])
        functions = {
            "cap-linear": lambda u: u / 2,
            "cap-log": lambda u: math.log1p(max(0, u)),
            "cap-step": lambda u: 5.0 if u >= 10 else 0.0,
            "policy-power": lambda u: max(0, u) ** 0.5,
            "policy-rational": lambda u: 10 * u / (1 + abs(u)),
            "policy-trig": lambda u: 2 + math.sin(u / 20) if u else 0.0,
            "policy-bounded": lambda u: 4 * math.tanh(u / 10),
        }
        if index:
            functions.update(
                {
                    "quadratic": lambda u: 4 - ((10 - u + 5 * (index % 3)) / 10) ** 2,
                    "piecewise": lambda u: (3 - 0.25 * index) if u >= 10 + index else 0.0,
                    "saturating": lambda u: u / 10 * (1 + 0.25 * index),
                }
            )
        if family not in functions:
            return output
        text = Path(request["candidate_path"]).read_text()
        constants = {
            k: float(v) for k, v in re.findall(r"constexpr double (\w+) = ([-+0-9.eE]+);", text)
        }
        t, x, h = constants["toy_entry"], constants["toy_exit"], constants["toy_heuristic"]
        baseline = t == 0 and x == 0.004 and h == 0.004
        pl = (
            1.0
            if baseline
            else 1
            + functions[family](40 - x * 10000)
            * (1 + 0.1 * index if family.startswith("policy-") else 1)
            - abs(t * 10000)
            - abs(h * 10000 - 40) / 10
        )
        dates = self.environment.period.trading_dates
        output.update(
            entry_only_pl=pl,
            mdd=min(0.0, pl),
            worst_day=pl / len(dates),
            daily_pl={str(d): pl / len(dates) for d in dates},
            risk={"fixture_only": True, "best_day_removed_pl": pl - pl / len(dates)},
        )
        return output


class LabBackends(FixtureBackends):
    def __call__(self, env: Any) -> FixtureIrisTransport:
        key = digest(env)
        if key not in self.values:
            self.values[key] = LabMathematicalBackend(
                env, shift=env.model_lineage.startswith("shifted")
            )
        return self.values[key]


def fixture_lab_spec(
    store: Store,
    *,
    family: str = "quadratic",
    index: int = 0,
    env: Any = None,
    method: ResearchMethod | None = None,
    capability_ref: str | None = None,
) -> LabSpec:
    if env is None:
        import uuid

        env = fixture_environment(store.root / "lab-inputs" / uuid.uuid4().hex)
    plan = fixture_plan(env, family, index)
    task_env = plan.queue.environment.model_copy(
        update={"period": plan.queue.environment.period.model_copy(update={"group_id": family})}
    )
    assert plan.queue.authorization is not None
    queue = plan.queue.model_copy(
        update={
            "environment": task_env,
            "authorization": plan.queue.authorization.model_copy(
                update={"environment_id": digest(task_env)}
            ),
        }
    )
    role = AgentConfig(reserve_tokens=1000, timeout_seconds=5, max_calls=32)
    program = ResearchProgramSpec(
        queue=queue,
        directions=("Optimize declared mathematical entry-only objective with fixed controls",),
        agent=role.model_copy(update={"max_calls": 1}),
        critic=role.model_copy(update={"max_calls": 2}),
        max_direction_batches=1,
        experiments_per_batch=1,
        execution_budget=Budget(
            max_actions=30,
            max_tool_calls=8,
            max_compute_units=30,
            max_llm_tokens=8000,
            max_wall_seconds=500,
            max_parallel_jobs=3,
        ),
    )
    source = FixtureSourceSearch().search("public artificial diagnostic")[0]
    return LabSpec(
        task_id=family + "-" + str(index),
        family=family,
        program=program,
        method=method or ResearchMethod(name="Research-M0", origin="declared_fixture"),
        role_config=role,
        source_refs=(store.artifacts.put(source),),
        capability_ref=capability_ref,
        diagnostic_groups={"entry": (0.0, 0.1), "exit": (1.0, 2.0)},
        budget=Budget(
            max_actions=100,
            max_tool_calls=20,
            max_compute_units=100,
            max_llm_tokens=40000,
            max_wall_seconds=2000,
            max_parallel_jobs=4,
        ),
    )


def fixture_lab_engine(store: Store) -> tuple[ResearchLab, FixtureBackends]:
    provider, backends = LabFixtureProvider(), LabBackends()
    return ResearchLab(
        store,
        provider_factory=lambda _: provider,
        transport_factory=backends,
        search=FixtureSourceSearch(),
    ), backends


def finish_lab(engine: ResearchLab, owner: str) -> dict[str, Any]:
    for _ in range(80):
        result = engine.advance(owner)
        if result.get("status") == "CLOSED":
            return result
        if result.get("status") in {
            "NEEDS_RECONCILIATION",
            "BUDGET_EXHAUSTED",
            "LAB_NEEDS_ATTENTION",
        }:
            raise IntegrityError("artificial research stopped: " + str(result))
    raise IntegrityError("bounded lab fixture did not close")


def run_lab_fixture(store: Store) -> dict[str, Any]:
    from jarvis.lab_evolution import compare_episodes

    engine, backends = fixture_lab_engine(store)
    spec = fixture_lab_spec(store)
    seed = engine.create(spec)
    cap = engine.propose_capability(seed)
    capability_definition(store, cap)
    finish_lab(engine, seed)
    improved = spec.model_copy(
        update={
            "task_id": "diagnostic-method",
            "capability_ref": cap,
            "method": spec.method.model_copy(
                update={"name": "Evidence-M1", "evidence_first": True}
            ),
        }
    )
    owner = engine.create(improved)
    report = finish_lab(engine, owner)
    reuse_spec = fixture_lab_spec(
        store,
        family="quadratic",
        index=1,
        env=spec.program.queue.environment,
        method=improved.method,
    )
    memory_baseline = engine.create(reuse_spec.model_copy(update={"task_id": "fresh-reuse"}))
    finish_lab(engine, memory_baseline)
    repeated = engine.create(
        reuse_spec.model_copy(
            update={
                "task_id": "fresh-reuse",
                "capability_ref": None,
                "experience_refs": (report["experience"][0]["experience_ref"],),
            }
        )
    )
    reuse = finish_lab(engine, repeated)
    return {
        "kind": "ResearchLabAcceptance",
        "status": "VERIFIED",
        "seed": seed,
        "episode": owner,
        "reuse": repeated,
        "memory_baseline": memory_baseline,
        "memory_comparison": compare_episodes(store, memory_baseline, repeated, "memory"),
        "capability": cap,
        "scores": [
            lab_report(store, p)["program"]["best_development_candidate"]["result"]["entry_only_pl"]
            for p in [seed, owner, repeated]
        ],
        "full_usage": report["full_usage"],
        "reuse_verification": reuse["verification"],
        "artificial_executions": sum(len(b.submissions) for b in backends.values.values()),
        "external_llm_calls": 0,
        "remote_iris_submissions": 0,
        "token_usage_origin": "SIMULATED_TOKEN_UNITS",
        "hermes_research": "NOT_STARTED",
    }


def fixture_evolution_spec(
    store: Store, axis: Axis, capability: str, *, shifted: bool = True
) -> Any:
    import uuid

    from jarvis.lab_evolution import LabEvolutionSpec

    env = fixture_environment(store.root / "evolution-inputs" / uuid.uuid4().hex)
    method = ResearchMethod(
        name="Evidence-M1" if axis != "method" else "Research-M0",
        evidence_first=axis != "method",
        origin="declared_fixture",
    )

    def task(family: str, index: int = 0, *, unseen: bool = False) -> LabSpec:
        spec = fixture_lab_spec(
            store,
            family=family,
            index=index,
            env=env,
            method=method,
            capability_ref=None if axis == "capability" else capability,
        )
        if unseen:
            task_env = spec.program.queue.environment.model_copy(
                update={
                    "period": spec.program.queue.environment.period.model_copy(
                        update={"exposure": "sealed_unseen"}
                    )
                }
            )
            grant = spec.program.queue.authorization
            assert grant is not None
            spec = spec.model_copy(
                update={
                    "program": spec.program.model_copy(
                        update={
                            "queue": spec.program.queue.model_copy(
                                update={
                                    "environment": task_env,
                                    "authorization": grant.model_copy(
                                        update={"environment_id": digest(task_env)}
                                    ),
                                }
                            )
                        }
                    )
                }
            )
        return spec

    development = tuple(
        task(f, i)
        for f in ["quadratic", "piecewise", "saturating"]
        for i in range(2 if axis == "policy" else 1)
    )
    heldout_families = (
        ["policy-power", "policy-rational"]
        if axis == "policy"
        else ["cap-linear"]
        if axis == "capability"
        else ["cubic"]
    )
    fresh_family = (
        "policy-trig" if axis == "policy" else "cap-log" if axis == "capability" else "exponential"
    )
    canary_family = (
        "policy-bounded"
        if axis == "policy"
        else "cap-step"
        if axis == "capability"
        else "shifted"
        if shifted
        else "hinge"
    )
    heldout = tuple(
        task(f, i, unseen=True)
        for f in heldout_families
        for i in range(2 if axis == "policy" else 1)
    )
    return LabEvolutionSpec(
        axis=axis,
        development=development,
        heldout=heldout,
        fresh=(task(fresh_family, unseen=True),),
        canary=(task(canary_family, unseen=True),),
        continuation=(task("quadratic-continuation", 1), task("quadratic-continuation", 2)),
        candidate_capability=capability if axis == "capability" else None,
        developer=development[0].role_config,
        budget=Budget(
            max_actions=10000,
            max_tool_calls=10000,
            max_compute_units=10000,
            max_llm_tokens=1000000,
            max_wall_seconds=20000,
            max_parallel_jobs=8,
        ),
    )


def run_improvement_fixture(store: Store) -> dict[str, Any]:
    from jarvis.lab_evolution import LabEvolution, adopt_profile, replay_lab
    from jarvis.lab_memory import retrieve

    base = run_lab_fixture(store)
    lab, backends = fixture_lab_engine(store)
    engine = LabEvolution(store, lab)
    studies: dict[str, dict[str, Any]] = {}
    axes: tuple[Axis, ...] = ("method", "capability", "policy")
    for axis in axes:
        spec = fixture_evolution_spec(store, axis, base["capability"], shifted=axis == "method")
        owner = engine.create(spec)
        for _ in range(1200):
            report = engine.advance(owner)
            if report["status"] == "CLOSED":
                break
        else:
            raise IntegrityError("bounded improvement fixture did not close")
        studies[axis] = {"study": owner, "report": report}
        adopted = adopt_profile(store, spec.continuation[1], owner)
        # A further actual research task pins the selected/fallback profile.
        follow = lab.create(adopted.model_copy(update={"task_id": "post-study-" + axis}))
        finish_lab(lab, follow)
        studies[axis]["subsequent_episode"] = follow
    seed_spec = LabSpec.model_validate(store.manifest(base["reuse"])["spec"])
    bad = fixture_lab_spec(store, family="piecewise").model_copy(
        update={"experience_refs": seed_spec.experience_refs}
    )
    rejected = retrieve(store, bad, before=store.manifest(base["reuse"])["created_at"])
    exact = replay_lab(
        store,
        base["episode"],
        LabSpec.model_validate(store.manifest(base["episode"])["spec"]).policy,
    )
    unknown = replay_lab(
        store, base["episode"], LabPolicy(name="unrecorded", actions=("diagnose",))
    )
    result = {
        "kind": "ResearchSelfImprovementAcceptance",
        "status": "VERIFIED",
        "foundation": base,
        "studies": {
            axis: {
                "study": v["study"],
                "outcome": v["report"]["outcome"],
                "verification": v["report"]["verification"],
                "full_usage": v["report"]["full_usage"],
                "subsequent_episode": v["subsequent_episode"],
            }
            for axis, v in studies.items()
        },
        "negative_memory_control": rejected,
        "replay": {"exact": exact["status"], "unrecorded": unknown["status"], "external_calls": 0},
        "artificial_executions": base["artificial_executions"]
        + sum(len(b.submissions) for b in backends.values.values()),
        "external_llm_calls": 0,
        "remote_iris_submissions": 0,
        "token_usage_origin": "SIMULATED_TOKEN_UNITS",
        "hermes_research": "NOT_STARTED",
    }
    output = store.root / "reports" / "self-improvement.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result
