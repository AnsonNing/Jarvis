"""Scripted planning acceptance on a fixed toy objective, with no provider/network calls.

These known artificial cases test protocol behavior, not autonomous research quality.
"""

from pathlib import Path
from typing import Any

from jarvis.autonomy_contracts import (
    AcceptanceProfile,
    AutonomyContract,
    Candidate,
    MetricSpec,
    Objective,
    ResearchPlanning,
    SettingSpec,
)
from jarvis.identity import file_digest
from jarvis.quant_fixture import QuantFixtureProvider
from jarvis.research_executors import Executors, ResolvedCandidate
from jarvis.storage import Store
from jarvis.workbench import Workbench


class ToyEvaluator:
    def identity(self) -> dict[str, Any]:
        return {"kind": "fixed-planning-toy", "version": 1, "source": file_digest(Path(__file__))}

    def run(
        self, candidate: ResolvedCandidate, settings: dict[str, Any], period: str, workdir: Path
    ) -> dict[str, Any]:
        variant = settings["variant"]
        score = 100 - 20 * (variant == "dead_end") + 10 * (variant == "alternative")
        return {
            "status": "SUCCEEDED",
            "metrics": {"score": score, "count": 1},
            "daily": {},
            "tables": [],
            "result_origin": "SIMULATED_FIXTURE",
            "provenance": {"fixture": self.identity(), "not_financial": True},
        }


def declared_world(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    candidate = Candidate(
        label="toy_baseline",
        parent_ref=None,
        model_ref=None,
        origin="owner_baseline",
        data_policy="SIMULATED_FIXTURE",
    )
    contract = AutonomyContract(
        task_id="planning_acceptance",
        instruction="Find an improvement to the declared toy objective; collaborate when useful.",
        objective="Improve fixed toy score",
        focus="Alternative hypotheses and honest task stopping",
        domain="synthetic-planning",
        evaluator_id="fixed-toy-score-v1",
        dataset_id="toy-v1",
        periods=("dev",),
        exposure="synthetic",
        baseline_candidate_ref=store.artifacts.put(candidate),
        settings=(
            SettingSpec(
                name="variant",
                kind="choice",
                baseline="baseline",
                choices=("baseline", "dead_end", "alternative"),
                description="Toy algorithm route",
            ),
        ),
        metrics=(
            MetricSpec(name="score", direction="maximize", description="Artificial objective"),
        ),
        acceptance_profiles=(
            AcceptanceProfile(
                name="score",
                objectives=(Objective(metric="score"),),
                min_count_fraction=0,
                description="Toy score increases",
            ),
        ),
        constraints=(
            "Synthetic only. No market data, external tools, training, provider calls or Dream.",
        ),
        origin="declared_fixture",
        planning=ResearchPlanning(),
    )
    return {
        "contract_ref": store.artifacts.put(contract),
        "executors": Executors(Workbench(), backtester=ToyEvaluator()),
        "input_refs": (),
        "identity": "planning-toy-v1",
    }


def hypothesis(name: str) -> dict[str, Any]:
    return {
        "hypothesis_id": "h_" + name,
        "claim": f"Route {name} improves the fixed objective",
        "prediction": "Score exceeds the unchanged baseline",
        "falsification": "Score does not exceed baseline",
        "evidence_refs": [],
    }


def direction(name: str, disposition: str = "ready", *, blocked: bool = False) -> dict[str, Any]:
    return {
        "direction_id": name,
        "kind": "parameter" if name == "A" else "rule",
        "hypothesis": hypothesis(name),
        "next_action": "TRAIN_MODEL" if blocked else "BACKTEST",
        "effort": "low" if name == "A" else "medium",
        "disposition": disposition,
        "reason": "Cheap initial test"
        if name == "A"
        else (
            "Required executor is absent; preserve the untested direction"
            if blocked
            else "Distinct route could distinguish mechanism from parameter failure; additional effort is justified"
        ),
        "origin": "declared_fixture",
    }


class PlanningFixtureProvider(QuantFixtureProvider):
    def __init__(self, role: str = "all", *, mode: str = "alternative"):
        super().__init__(role)
        self.mode = mode

    def identity(self) -> dict[str, Any]:
        return {
            "provider": "planning-fixture",
            "source": file_digest(Path(__file__)),
            "mode": self.mode,
            "token_units": "simulated",
        }

    def director(self, c: dict[str, Any]) -> dict[str, Any]:
        branches = {b["branch_id"]: b for b in c["research_plan"]["branches"]}
        rows, obs = c["experiment_ledger"], c["observations"]
        base = c["research_contract"]["baseline_candidate_ref"]
        single = self.mode == "single"
        blocked = self.mode == "blocked"
        cards = [direction("A", "retired" if branches else "ready")]
        if not single:
            cards.append(direction("B", "blocked" if blocked else "ready", blocked=blocked))
        action: dict[str, Any]
        selected, basis, question = None, None, None
        branch, q = None, None
        name = "A"
        if not branches:
            selected = "A"
            specs = [("baseline", "baseline"), ("test_A", "alternative" if single else "dead_end")]
            action = self.batch(base, specs)
        elif not single and branches["A"]["status"] == "open":
            cited = [obs[-1]["result_ref"]]
            checkpoint_done = any(o["kind"] == "STOP" for o in obs)
            if self.mode == "checkpoint" and not checkpoint_done:
                action = {
                    "kind": "STOP",
                    "scope": "checkpoint",
                    "conclusion": "inconclusive",
                    "closures": [],
                    "finalist_result_ref": None,
                }
                basis, question = (
                    "owner_checkpoint",
                    "Continue the distinct route B, or report the limited A-only result?",
                )
            elif blocked:
                action = self.stop("A", cited, success=False, scope="session")
                basis = "no_justified_next_step"
            elif self.mode == "premature_once" and "correction" not in c:
                action = self.stop("A", cited, success=False, scope="session")
                basis = "no_justified_next_step"  # intentionally invalid: B is still ready
            else:
                action = self.stop("A", cited, success=False, scope="branch")
        elif not single and "B" not in branches:
            name, selected = "B", "B"
            action = self.batch(base, [("test_B", "alternative")])
        elif not any(o["kind"] == "COMPARE" for o in obs):
            name = "A" if single else "B"
            selected = name
            cards = [direction("A", "ready" if single else "retired")]
            if not single:
                cards.append(direction("B"))
            cards[-1]["next_action"] = "COMPARE"
            baseline = next(r for r in rows if not r["changes"])
            winner = next(r for r in rows if r["changes"].get("variant") == "alternative")
            action = {
                "kind": "COMPARE",
                "baseline_result_ref": baseline["result_ref"],
                "result_refs": [winner["result_ref"]],
            }
        else:
            name = "A" if single else "B"
            cards = [direction("A", "retired")]
            if not single:
                cards.append(direction("B", "retired"))
            winner = next(r for r in rows if r["changes"].get("variant") == "alternative")
            action = self.stop(name, [obs[-1]["result_ref"]], success=True, scope="session")
            action["finalist_result_ref"] = winner["result_ref"]
            basis = "goal_met"
        if name not in branches:
            branch = {
                "branch_id": name,
                "kind": "parameter" if name == "A" else "rule",
                "goal": f"Test route {name}",
            }
            h = hypothesis(name)
            null = {
                **h,
                "hypothesis_id": "null_" + name,
                "claim": "No improvement",
                "prediction": "Score does not increase",
                "falsification": "Score increases",
            }
            q = {
                "question_id": "q_" + name,
                "parent_id": "q_A" if name == "B" else None,
                "question": f"Does route {name} improve the toy objective?",
                "hypotheses": [h, null],
            }
        return {
            "context_id": c["context_id"],
            "situation": "Scripted synthetic planning acceptance",
            "branch": branch,
            "question": q,
            "guidance_responses": [
                {
                    "guidance_id": g,
                    "adopted": True,
                    "response": "Owner preference recorded; continue B within unchanged scope and gates.",
                }
                for g in c["pending_guidance_ids"]
            ],
            "action": {
                "branch_id": name,
                "question_id": "q_" + name,
                "hypothesis_ids": ["h_" + name],
                "rationale": "Use only recorded toy evidence; branch failure does not establish mission exhaustion",
                "expected_observation": "Declared synthetic protocol outcome",
                "evidence_refs": [],
                **action,
            },
            "planning_review": {
                "summary": "Compare routes without imposing a minimum branch count",
                "directions": cards,
                "selected_direction_id": selected,
                "selection_reason": "Prefer useful evidence per effort; preserve untried alternatives and owner collaboration",
                "stop_basis": basis,
                "owner_question": question,
            },
        }

    @staticmethod
    def batch(base: str, specs: list[tuple[str, str]]) -> dict[str, Any]:
        return {
            "kind": "BACKTEST",
            "design": "Fixed toy comparator, no private/financial data",
            "experiments": [
                {
                    "label": label,
                    "candidate_ref": base,
                    "settings": [{"name": "variant", "value": variant}],
                    "period": "dev",
                    "hypothesis": "Toy route improves score",
                    "falsification": "Score does not improve",
                }
                for label, variant in specs
            ],
        }

    @staticmethod
    def stop(name: str, refs: list[str], *, success: bool, scope: str) -> dict[str, Any]:
        return {
            "kind": "STOP",
            "scope": scope,
            "conclusion": "research_complete" if success else "negative",
            "closures": [
                {
                    "branch_id": name,
                    "conclusion": "supported" if success else "negative",
                    "reason": "Observed fixed toy result",
                    "evidence_refs": refs,
                }
            ],
            "finalist_result_ref": None,
        }

    def critic(self, c: dict[str, Any]) -> dict[str, Any]:
        answer = super().critic(c)
        action = c["decision"]["action"]
        if action["kind"] == "BACKTEST" and action["branch_id"] == "A" and self.mode != "single":
            answer["recommendation"] = "close_branch"
            answer["next_questions"] = [
                "Consider distinct B or explain its deferral; A's failure is not a task-wide result"
            ]
        return answer

    def curator(self, c: dict[str, Any]) -> dict[str, Any]:
        return {
            "context_id": c["context_id"],
            "summary": "Synthetic protocol facts; no real-domain capability claim",
            "lessons": [],
            "skills": [],
            "superseded_lessons": [],
            "evidence_refs": c["visible_evidence_ids"][:1],
        }
