"""Scripted engineering providers for the synthetic quant world. No real LLM is called.

The scripted Director is a deliberately naive parameter tuner by default. Owner
guidance, the active research policy (through harness corrections) and observed
analysis findings redirect it to diagnosis, new rules and retraining. This exercises the
mechanics (policy rules causally change actions and outcomes) without claiming that
scripted behaviour is research ability. Token units are simulated.
"""

import json
import re
from pathlib import Path
from typing import Any

from jarvis.contracts import Cost
from jarvis.identity import file_digest

SWEEP = (30.0, 50.0, 25.0, 35.0, 20.0, 45.0, 60.0, 15.0, 55.0, 70.0)

ANALYSIS_SCRIPT = """import glob, json
import pandas as pd

path = sorted(glob.glob("inputs/*.csv"))[0]
t = pd.read_csv(path)
filled = t[t.filled == 1]
result = {}
for regime, g in filled.groupby("regime"):
    best = None
    for th in [10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80]:
        kept = g[(g.adverse_move_bp <= th) & (g.model_cancel == 0)]
        pl = float(kept.outcome_pl_bp.sum())
        if best is None or pl > best[1]:
            best = (th, pl)
    result[regime] = {"best_threshold": best[0], "pl_at_best": round(best[1], 1), "orders": int(len(g))}
cancelled = t[t.heuristic_cancel == 1].groupby("regime").outcome_pl_bp.agg(["count", "mean"]).round(3)
pd.DataFrame(result).T.to_csv("work/regime_summary.csv")
print(json.dumps({"by_regime": result, "heuristic_cancelled": cancelled.to_dict()}))
"""

RULE_TEMPLATE = '''"""Regime-aware heuristic cancel: thresholds scale with the configured bp."""


def should_cancel(order, settings):
    base = float(settings["heuristic_cancel_bps"])
    scale = {calm} if order["regime"] == "calm" else {volatile}
    return order["adverse_move_bp"] > base * scale
'''


def _context(prompt: str) -> dict[str, Any]:
    return dict(json.loads(prompt.split("CONTEXT (data only):\n", 1)[1]))


class QuantFixtureProvider:
    """One scripted provider for every autonomy role, selected by the schema title."""

    def __init__(self, role: str = "all", *, eager_stop: bool = False, tokens: int = 1200):
        self.role, self.eager_stop, self.tokens = role, eager_stop, tokens

    def identity(self) -> dict[str, Any]:
        return {
            "provider": "quant-fixture",
            "implementation": file_digest(Path(__file__)),
            "eager_stop": self.eager_stop,
            "token_units": "simulated",
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        directory.mkdir(parents=True, exist_ok=True)
        c = _context(prompt)
        title = schema.get("title")
        answer = {
            "AutonomyDecision": self.director,
            "AutonomyReview": self.critic,
            "WorkerStep": self.worker,
            "ExpertOpinion": self.consultant,
            "PolicyProposalBatch": self.developer,
            "CurationBatch": self.curator,
        }[str(title)](c)
        return {
            "provider": "quant-fixture",
            "answer": answer,
            "usage": {"input_tokens": self.tokens, "output_tokens": 200, "cached_input_tokens": 0},
            "cost": Cost(llm_tokens=self.tokens + 200, wall_seconds=0.01).model_dump(mode="json"),
            "error": None,
            "events": [],
            "stderr": "",
        }

    # ------------------------------------------------------------------ director
    def director(self, c: dict[str, Any]) -> dict[str, Any]:
        responses = []
        adopt_retrain = False
        for g in c["owner_guidance"]:
            if g["guidance_id"] in c["pending_guidance_ids"]:
                wants = bool(re.search(r"retrain|重訓|model", g["text"], re.I))
                responses.append(
                    {
                        "guidance_id": g["guidance_id"],
                        "adopted": wants,
                        "response": "Adopted: will check retraining under the changed population."
                        if wants
                        else "Noted; current evidence does not change the next step.",
                    }
                )
                adopt_retrain = adopt_retrain or wants
            elif (
                g.get("response")
                and g["response"]["adopted"]
                and re.search(r"retrain|重訓|model", g["text"], re.I)
            ):
                adopt_retrain = True
        plan = c["research_plan"]
        branches = {b["branch_id"]: b for b in plan["branches"]}
        questions = {q["question_id"]: q for q in plan["questions"]}
        correction = (c.get("correction") or {}).get("reason", "")
        refs = c["visible_evidence_ids"]
        obs = [o for o in c["observations"]]
        # Every known backtest, including earlier sessions' (research memory).
        rows = [dict(r) for r in c.get("experiment_ledger", []) if r.get("comparable", True)]
        baseline = next(
            (r for r in rows if r["label"] == "baseline" and r["status"] == "SUCCEEDED"), None
        )
        ok = [r for r in rows if r["status"] == "SUCCEEDED"]
        # The scripted plan follows its own candidates; remembered ones are context only.
        cand_by_label = {x["label"]: x for x in c["candidates"] if not x.get("from_memory")}

        def decision(
            branch: dict[str, Any] | None,
            question: dict[str, Any] | None,
            action: dict[str, Any],
            situation: str,
        ) -> dict[str, Any]:
            bid = branch["branch_id"] if branch else action["branch_id"]
            qid = (
                question["question_id"]
                if question
                else next(
                    (q["question_id"] for q in questions.values() if q["branch_id"] == bid), None
                )
            )
            hyps = question["hypotheses"] if question else questions[qid]["hypotheses"]
            return {
                "context_id": c["context_id"],
                "situation": situation,
                "branch": branch,
                "question": question,
                "guidance_responses": responses,
                "action": {
                    "branch_id": bid,
                    "question_id": qid,
                    "hypothesis_ids": [h["hypothesis_id"] for h in hyps],
                    "rationale": situation,
                    "expected_observation": "A discriminating observation for the open hypotheses.",
                    "evidence_refs": refs[-3:],
                    **action,
                },
            }

        def new_branch(
            bid: str, kind: str, goal: str, qid: str, question: str, h: list[tuple[str, str]]
        ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
            if bid in branches:
                return None, None
            return (
                {"branch_id": bid, "kind": kind, "goal": goal},
                {
                    "question_id": qid,
                    "parent_id": None,
                    "question": question,
                    "hypotheses": [
                        {
                            "hypothesis_id": hid,
                            "claim": claim,
                            "prediction": "Observable in the next action's result.",
                            "falsification": "The opposite pattern in the observed result.",
                            "evidence_refs": refs[:1],
                        }
                        for hid, claim in h
                    ],
                },
            )

        def ensure(
            bid: str,
            kind: str,
            goal: str,
            qid: str,
            question: str,
            h: list[tuple[str, str]],
            action: dict[str, Any],
            situation: str,
        ) -> dict[str, Any]:
            b, q = new_branch(bid, kind, goal, qid, question, h)
            return decision(b, q, {"branch_id": bid, **action}, situation)

        # 0. start: read the sources in a parameter branch
        if not branches:
            return ensure(
                "b-param",
                "parameter",
                "Tune the heuristic cancel bp",
                "q-param",
                "Is a single bp change enough to beat the baseline?",
                [
                    ("h-tune", "A different single bp improves PL"),
                    ("h-structure", "Gains need a structural change"),
                ],
                {
                    "kind": "READ_SOURCE",
                    "scopes": [s["name"] for s in c["research_contract"]["source_scopes"]],
                },
                "Read the strategy and pipeline first.",
            )
        if c["running_actions"]:
            return decision(
                None,
                None,
                {
                    "branch_id": next(
                        iter(b for b, v in branches.items() if v["status"] == "open")
                    ),
                    "kind": "WAIT",
                },
                "Waiting for running actions.",
            )
        if not rows:
            return decision(
                None,
                None,
                {
                    "branch_id": "b-param",
                    "kind": "BACKTEST",
                    "design": "Baseline control plus two bp alternatives on the same period",
                    "experiments": [
                        self._exp(
                            "baseline", c["research_contract"]["baseline_candidate_ref"], None
                        ),
                        self._exp("bp30", c["research_contract"]["baseline_candidate_ref"], 30.0),
                        self._exp("bp50", c["research_contract"]["baseline_candidate_ref"], 50.0),
                    ],
                },
                "Establish the baseline and bracket the bp.",
            )
        if (
            self.eager_stop
            and not any(o["kind"] == "COMPARE" for o in obs)
            and "stop guard" not in correction
        ):
            return decision(
                None,
                None,
                {
                    "branch_id": "b-param",
                    "kind": "STOP",
                    "scope": "session",
                    "conclusion": "research_complete",
                    "closures": [
                        {
                            "branch_id": "b-param",
                            "conclusion": "supported",
                            "reason": "premature",
                            "evidence_refs": refs[-1:],
                        }
                    ],
                    "finalist_result_ref": None,
                },
                "Premature stop attempt (fixture).",
            )
        # 1. policy correction: explain gains
        m = re.search(r"trades=(sha256:[a-f0-9]{64})", correction)
        fallback = None
        if (
            "policy:" in correction
            and not m
            and "open a model branch" not in correction
            and "parameter enumeration limit" not in correction
        ):
            # Any other method rule (analysis before stop, backtest streaks): diagnose latest trades.
            fallback = next(
                (t["ref"] for t in reversed(c["tables"]) if t["name"] == "trades"), None
            )
        if ("explain passing results" in correction and m) or fallback:
            table = m.group(1) if m else fallback
            return ensure(
                "b-diag",
                "diagnosis",
                "Explain where the bp gain comes from",
                "q-diag",
                "Which orders and regimes drive the change?",
                [
                    ("h-regime", "The optimal threshold differs by regime"),
                    ("h-uniform", "One threshold is optimal everywhere"),
                ],
                {
                    "kind": "ANALYZE",
                    "question": "Break PL of cancelled and kept orders down by regime and adverse move; find per-regime best thresholds.",
                    "input_refs": [table],
                },
                "Policy asks to explain the passing result before more tuning.",
            )
        analyses = [
            o
            for o in obs
            if o["kind"] == "ANALYZE" and o.get("result", {}).get("status") == "OBSERVED"
        ]
        findings = {}
        for o in analyses:
            for f in o["result"].get("findings", []):
                findings[f["statement"]] = f["value"]
        rule_done = any(o["kind"] == "IMPLEMENT" for o in obs)
        # 2. analysis found regime structure -> implement a rule
        if "best_threshold_calm" in findings and not rule_done:
            calm = float(findings["best_threshold_calm"]) / 40.0
            vol = float(findings["best_threshold_volatile"]) / 40.0
            return ensure(
                "b-rule",
                "rule",
                "Regime-aware heuristic cancel",
                "q-rule",
                "Does a regime-aware threshold beat any single bp?",
                [
                    ("h-rule", "Regime-aware thresholds beat the best single bp"),
                    ("h-norule", "No gain over the best single bp"),
                ],
                {
                    "kind": "IMPLEMENT",
                    "label": "regime_rule",
                    "target": "cancel_rule",
                    "base_candidate_ref": c["research_contract"]["baseline_candidate_ref"],
                    "requirement": f"Regime-aware threshold: calm_multiplier={calm:.4g} volatile_multiplier={vol:.4g} of settings['heuristic_cancel_bps']",
                },
                "Analysis shows different optimal thresholds per regime.",
            )
        tested = {r["label"] for r in rows}
        rule_candidate = cand_by_label.get("regime_rule")
        if rule_candidate and "regime_rule" not in tested:
            return decision(
                None,
                None,
                {
                    "branch_id": "b-rule",
                    "kind": "BACKTEST",
                    "design": "New rule vs baseline",
                    "experiments": [self._exp("regime_rule", rule_candidate["ref"], None)],
                },
                "Test the implemented rule.",
            )
        # 3. model branch (policy or guidance)
        trained = [x for x in cand_by_label.values() if x["origin"] == "agent_trained"]
        wants_model = "open a model branch" in correction or adopt_retrain
        best = self._best(ok, baseline)
        if (
            wants_model
            and not trained
            and not any(o["kind"] == "TRAIN_MODEL" for o in obs)
            and best
        ):
            table = best["tables"].get("trades")
            base_label = best["label"]
            base_ref = (
                cand_by_label[base_label]["ref"]
                if base_label in cand_by_label
                else c["research_contract"]["baseline_candidate_ref"]
            )
            return ensure(
                "b-model",
                "model",
                "Retrain the cancel model on the changed order population",
                "q-model",
                "Is the baseline model miscalibrated on the new population?",
                [
                    ("h-shift", "Retraining on the new population improves results"),
                    ("h-stable", "The model is unaffected"),
                ],
                {
                    "kind": "TRAIN_MODEL",
                    "label": "retrained",
                    "base_candidate_ref": base_ref,
                    "training_data_ref": table,
                    "pipeline_patch_ref": None,
                    "config": [{"name": "epochs", "value": 120}],
                },
                "The passing candidate changed what the model sees.",
            )
        if trained and "retrained" not in tested:
            source = next(
                (
                    t
                    for t in c["tables"]
                    if trained[0]["data_policy"].find(t["lineage"].get("experiment", "\0")) >= 0
                ),
                None,
            )
            settings = source["lineage"].get("settings", {}) if source else {}
            return decision(
                None,
                None,
                {
                    "branch_id": "b-model",
                    "kind": "BACKTEST",
                    "design": "Retrained model at the best configuration",
                    "experiments": [
                        self._exp(
                            "retrained", trained[0]["ref"], settings.get("heuristic_cancel_bps")
                        )
                    ],
                },
                "Test the retrained model.",
            )
        # 4. naive sweep around the best bp
        tried = {r["changes"].get("heuristic_cancel_bps") for r in rows}
        limit = next(
            (
                int(x)
                for rule in c["research_policy"]["enforced_rules"]
                for x in re.findall(r"At most (\d+) distinct values", rule)
            ),
            len(SWEEP),
        )
        room = limit - len(tried - {None})
        if (
            self._improving(ok, baseline)
            and room > 0
            and "parameter enumeration limit" not in correction
        ):
            todo = [v for v in SWEEP if v not in tried][: min(2, room)]
            if todo and len(c["observations"]) < 18:
                return decision(
                    None,
                    None,
                    {
                        "branch_id": "b-param",
                        "kind": "BACKTEST",
                        "design": "Continue the bp sweep",
                        "experiments": [
                            self._exp(
                                f"bp{int(v)}", c["research_contract"]["baseline_candidate_ref"], v
                            )
                            for v in todo
                        ],
                    },
                    "Keep tuning the bp.",
                )
        # 5. compare and stop
        compares = [o for o in obs if o["kind"] == "COMPARE"]
        # Older observations may be compacted (no "result"): coverage is judged by action order.
        last_compare = max((o["index"] for o in compares), default=-1)
        others = [r for r in ok if baseline and r["result_ref"] != baseline["result_ref"]]
        uncompared = [
            r
            for r in others
            if not compares or (not r.get("from_memory") and r["action_index"] > last_compare)
        ]
        if uncompared:
            open_bid = next(b for b, v in branches.items() if v["status"] == "open")
            assert baseline is not None
            return decision(
                None,
                None,
                {
                    "branch_id": open_bid,
                    "kind": "COMPARE",
                    "result_refs": [r["result_ref"] for r in others],
                    "baseline_result_ref": baseline["result_ref"],
                },
                "Compare all candidates under the declared profiles.",
            )
        finalist = (
            compares[-1].get("result", {}).get("finalist_by_declared_rule", {"status": "NONE"})
            if compares
            else {"status": "NONE"}
        )
        last = compares[-1]["result_ref"] if compares else refs[-1]
        closures = [
            {
                "branch_id": b,
                "conclusion": "supported"
                if finalist.get("status") == "SELECTED"
                else "inconclusive",
                "reason": "Closed after the final comparison.",
                "evidence_refs": [last],
            }
            for b, v in branches.items()
            if v["status"] == "open"
        ]
        return decision(
            None,
            None,
            {
                "branch_id": closures[0]["branch_id"],
                "kind": "STOP",
                "scope": "session",
                "conclusion": "research_complete"
                if finalist.get("status") == "SELECTED"
                else "inconclusive",
                "closures": closures,
                "finalist_result_ref": finalist.get("result_ref"),
            },
            "Research complete under the declared finalist rule.",
        )

    @staticmethod
    def _exp(label: str, candidate: str, bp: float | None) -> dict[str, Any]:
        return {
            "label": label,
            "candidate_ref": candidate,
            "settings": [] if bp is None else [{"name": "heuristic_cancel_bps", "value": bp}],
            "period": "dev",
            "hypothesis": f"{label} changes PL versus the baseline",
            "falsification": "No PL change versus the baseline",
        }

    @staticmethod
    def _best(ok: list[dict[str, Any]], baseline: dict[str, Any] | None) -> dict[str, Any] | None:
        if not baseline:
            return None
        better = [r for r in ok if r["metrics"].get("pl", 0) > baseline["metrics"]["pl"]]
        return max(better, key=lambda r: r["metrics"]["pl"]) if better else None

    @staticmethod
    def _improving(ok: list[dict[str, Any]], baseline: dict[str, Any] | None) -> bool:
        if baseline is None:
            return False
        return any(r["metrics"].get("pl", 0) > baseline["metrics"]["pl"] for r in ok)

    # ------------------------------------------------------------------ critic
    def critic(self, c: dict[str, Any]) -> dict[str, Any]:
        result = c["result"]
        status = result.get("status")
        action = c["decision"]["action"]
        assessments = []
        if action["kind"] == "ANALYZE" and status == "OBSERVED":
            findings = {f["statement"]: f["value"] for f in result.get("findings", [])}
            if "best_threshold_calm" in findings and findings[
                "best_threshold_calm"
            ] != findings.get("best_threshold_volatile"):
                assessments.append(
                    {
                        "hypothesis_id": "h-regime",
                        "verdict": "supported",
                        "evidence_refs": [c["result_ref"]],
                        "explanation": "Per-regime best thresholds differ in the observed trades.",
                    }
                )
        recommendation = (
            "investigate_further" if status in {"FAILED", "REJECTED", "STEP_LIMIT"} else "continue"
        )
        return {
            "context_id": c["context_id"],
            "summary": f"Reviewed {action['kind']} with status {status}.",
            "evidence_refs": [c["result_ref"]],
            "assessments": assessments,
            "concerns": ["Synthetic development data; repeated selection inflates apparent gains."],
            "recommendation": recommendation,
            "next_questions": [],
        }

    # ------------------------------------------------------------------ worker
    def worker(self, c: dict[str, Any]) -> dict[str, Any]:
        task, transcript = c["task"], c["transcript"]
        step = len(transcript)
        base = {
            "context_id": c["context_id"],
            "path": None,
            "content": None,
            "arguments": [],
            "summary": None,
            "findings": [],
            "output_files": [],
            "final_script": None,
        }
        if task["kind"] == "analysis":
            skill = next(
                (
                    s
                    for s in task.get("available_skills", [])
                    if s["name"] == "regime_threshold_scan"
                ),
                None,
            )
            if skill and step == 0:
                return {
                    **base,
                    "note": "Reuse the remembered regime scan skill.",
                    "tool": "run_python",
                    "path": "work/analysis.py",
                    "content": "exec(open('skills/regime_threshold_scan/analysis.py').read())\n",
                }
            if skill and step == 1:
                step = 2  # continue with the report step below
            if step == 0:
                return {
                    **base,
                    "note": "Write the regime analysis.",
                    "tool": "write_file",
                    "path": "work/analysis.py",
                    "content": ANALYSIS_SCRIPT,
                }
            if step == 1:
                return {
                    **base,
                    "note": "Run it.",
                    "tool": "run_python",
                    "arguments": ["analysis.py"],
                }
            out = transcript[-1]["observation"]
            if out.get("status") != "PASS":
                return {
                    **base,
                    "note": "Analysis failed; report it.",
                    "tool": "finish",
                    "summary": "Analysis script failed: " + str(out.get("stderr", ""))[-300:],
                }
            data = json.loads(out["stdout"].strip().splitlines()[-1])
            regimes = data["by_regime"]
            findings = [
                {"statement": f"best_threshold_{r}", "value": str(v["best_threshold"])}
                for r, v in sorted(regimes.items())
            ]
            findings += [
                {"statement": f"pl_at_best_{r}", "value": str(v["pl_at_best"])}
                for r, v in sorted(regimes.items())
            ]
            return {
                **base,
                "note": "Report.",
                "tool": "finish",
                "summary": "Per-regime threshold scan on the observed trades: "
                + json.dumps(regimes),
                "findings": findings,
                "output_files": ["work/regime_summary.csv"],
                "final_script": "work/analysis.py",
            }
        m = re.search(
            r"calm_multiplier=([0-9.]+) volatile_multiplier=([0-9.]+)", task["requirement"]
        )
        calm, vol = (m.group(1), m.group(2)) if m else ("1.0", "1.0")
        if step == 0:
            return {
                **base,
                "note": "Implement the rule.",
                "tool": "write_file",
                "path": "work/cancel_rule.py",
                "content": RULE_TEMPLATE.format(calm=calm, volatile=vol),
            }
        if step == 1:
            return {**base, "note": "Validate.", "tool": "validate"}
        return {
            **base,
            "note": "Done.",
            "tool": "finish",
            "summary": f"Regime-aware rule calm x{calm}, volatile x{vol}; validator passed.",
            "output_files": ["cancel_rule.py"],
        }

    def consultant(self, c: dict[str, Any]) -> dict[str, Any]:
        return {
            "context_id": c["context_id"],
            "hypothesis": "The cancel model was fit to the old order population.",
            "counterargument": "The model inputs may be unaffected by the heuristic.",
            "test": "Retrain on the new population and backtest at the same bp.",
            "evidence_refs": c["visible_evidence_ids"][-1:],
            "question_for": None,
            "question": None,
        }

    # ------------------------------------------------------------------ curator
    def curator(self, c: dict[str, Any]) -> dict[str, Any]:
        facts = c["facts"]
        lessons, skills = [], []
        finalist = c.get("finalist") or {}
        if finalist.get("status") == "SELECTED":
            lessons.append(
                {
                    "statement": f"Best candidate under {finalist['profile']} in this world: result {finalist['result_ref'][7:15]}.",
                    "kind": "finding",
                    "evidence_refs": [finalist["result_ref"]],
                    "applicability": "same synthetic world family",
                    "confidence": "medium",
                }
            )
        for analysis in c["reproducible_analyses"][:1]:
            lessons.append(
                {
                    "statement": "Per-regime threshold scans explain bp gains: "
                    + analysis["summary"][:300],
                    "kind": "method",
                    "evidence_refs": [analysis["result_ref"]],
                    "applicability": "cancel-threshold research",
                    "confidence": "medium",
                }
            )
            if not any(s["name"] == "regime_threshold_scan" for s in c["existing_skills"]):
                skills.append(
                    {
                        "analysis_result_ref": analysis["result_ref"],
                        "name": "regime_threshold_scan",
                        "description": "Scan cancel thresholds per regime on an observed trades table.",
                        "usage": "exec(open('skills/regime_threshold_scan/analysis.py').read()) from the workspace root; "
                        "reads inputs/*.csv and writes work/regime_summary.csv",
                        "inputs": "one trades CSV with regime, filled, adverse_move_bp, model_cancel, outcome_pl_bp",
                    }
                )
        for failed in facts.get("failed_actions", [])[:1]:
            lessons.append(
                {
                    "statement": f"{failed['kind']} failed: {failed['error'][:200]}",
                    "kind": "warning",
                    "evidence_refs": [failed["result_ref"]],
                    "applicability": "same setup",
                    "confidence": "low",
                }
            )
        refs = [x for x in [finalist.get("result_ref")] if x] or c["visible_evidence_ids"][:1]
        return {
            "context_id": c["context_id"],
            "summary": "Scripted curation of the finished session.",
            "lessons": lessons,
            "skills": skills,
            "superseded_lessons": [],
            "evidence_refs": refs,
        }

    # ------------------------------------------------------------------ Dream developer
    def developer(self, c: dict[str, Any]) -> dict[str, Any]:
        summary = c["development_summary"]
        sweeps = sum(s["action_counts"].get("BACKTEST", 0) for s in summary["sessions"])
        analyses = sum(s["action_counts"].get("ANALYZE", 0) for s in summary["sessions"])
        candidates = [
            {
                "kind": "AutonomyResearchPolicy",
                "name": "explain-then-structure",
                "guidance": "After any gain, explain it with trade-level analysis before more tuning; check retraining when inputs shift.",
                "max_consecutive_backtests": None,
                "explain_gains_before_more_backtests": True,
                "require_model_branch_before_stop": True,
                "min_analyses_before_stop": 1,
                "max_values_per_setting": 4,
                "branch_shares": [],
                "rationale": f"Development histories used {sweeps} backtests and {analyses} analyses; gains came from parameter sweeps only.",
                "origin": "agent_proposed",
                "schema_version": 1,
            },
            {
                "kind": "AutonomyResearchPolicy",
                "name": "analysis-heavy",
                "guidance": "Analyze extensively before any experiment.",
                "max_consecutive_backtests": 1,
                "explain_gains_before_more_backtests": False,
                "require_model_branch_before_stop": False,
                "min_analyses_before_stop": 6,
                "max_values_per_setting": None,
                "branch_shares": [],
                "rationale": "Alternative: force analysis between all backtests.",
                "origin": "agent_proposed",
                "schema_version": 1,
            },
        ]
        return {
            "context_id": c["context_id"],
            "analysis": "Sessions stopped after parameter sweeps without explaining gains or checking the model.",
            "candidates": candidates,
            "evidence_refs": c["visible_evidence_ids"][:1],
        }
