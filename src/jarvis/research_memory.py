"""Cross-session research memory and skills: what every finished session leaves behind.

After a session ends (closed, budget exhausted, needs attention, paused or cancelled),
`KnowledgeBase.consolidate` records:

- trusted facts (no LLM): every backtest observation, produced candidates/models/patches,
  observed tables, rejected implementations, failed actions, branch closures, the
  declared-rule finalist and open questions;
- curated lessons (Curator role): findings, negative results, methods, data quirks and
  open questions, each citing evidence from that session; contradicted older lessons are
  marked superseded, never deleted;
- skills: reproducible analysis scripts (re-run byte-identically) promoted to a versioned
  library with usage notes.

`snapshot(domain)` gives new sessions this memory: prior experiments (identical work is
not repeated), prior candidates (they can be backtested again or improved), tables,
active lessons, and the skill library mounted read-only in every analysis workspace.
The domain's research-policy registry (what Dream-RSI adopts) also lives here.
"""

import json
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from jarvis.bounded_roles import evidence_role
from jarvis.contracts import AdmissionError, Budget, IntegrityError, JarvisError, Record
from jarvis.identity import digest
from jarvis.research_contract import Name, Ref
from jarvis.researcher import AgentConfig
from jarvis.storage import Store

TERMINAL = {"CLOSED", "BUDGET_EXHAUSTED", "NEEDS_ATTENTION"}
REF = re.compile(r"^sha256:[a-f0-9]{64}$")


class MemoryLesson(Record):
    statement: str = Field(min_length=1, max_length=1500)
    kind: Literal["finding", "negative_result", "method", "data_quirk", "open_question", "warning"]
    evidence_refs: tuple[Ref, ...] = Field(min_length=1, max_length=8)
    applicability: str = Field(min_length=1, max_length=600)
    confidence: Literal["low", "medium", "high"]


class SkillProposal(Record):
    analysis_result_ref: Ref
    name: Name
    description: str = Field(min_length=1, max_length=800)
    usage: str = Field(min_length=1, max_length=1500)
    inputs: str = Field(min_length=1, max_length=800)


class CurationBatch(Record):
    context_id: Ref
    summary: str = Field(min_length=1, max_length=3000)
    lessons: tuple[MemoryLesson, ...] = Field(max_length=20)
    skills: tuple[SkillProposal, ...] = Field(max_length=5)
    superseded_lessons: tuple[Ref, ...] = Field(max_length=20)
    evidence_refs: tuple[Ref, ...] = Field(min_length=1, max_length=24)


def _payloads(store: Store, owner: str, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in store.events(owner) if e["event_type"] == kind]


def session_domain(store: Store, session: str) -> str:
    spec = store.manifest(session)["spec"]
    return str(store.artifacts.get(spec["contract_ref"])["domain"])


class KnowledgeBase:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------ identity
    def find(self, domain: str) -> str | None:
        with self.store.connect() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM campaigns")]
        for cid in ids:
            m = self.store.manifest(cid)
            if m.get("kind") == "ResearchKnowledgeBase" and m.get("domain") == domain:
                return cid
        return None

    def ensure(self, domain: str) -> str:
        """One knowledge base (and research-policy registry) per domain per store."""
        found = self.find(domain)
        if found:
            return found
        from jarvis.autonomy_dream import PolicyRegistry

        registry = PolicyRegistry(self.store).create(scope=f"autonomy:{domain}")
        return self.store.create(
            {
                "kind": "ResearchKnowledgeBase",
                "domain": domain,
                "policy_registry": registry,
                "world_id": digest(
                    {"kind": "ResearchKnowledgeBase", "domain": domain, "registry": registry}
                ),
                "task": {
                    "budget": Budget(
                        max_actions=100000,
                        max_tool_calls=100000,
                        max_llm_tokens=200000000,
                        max_wall_seconds=10**9,
                        max_compute_units=10**6,
                        max_parallel_jobs=16,
                    ).model_dump()
                },
            }
        )

    def registry(self, domain: str) -> str:
        return str(self.store.manifest(self.ensure(domain))["policy_registry"])

    def consolidated(self, kb: str) -> dict[str, dict[str, Any]]:
        return {p["session"]: p for p in _payloads(self.store, kb, "SESSION_CONSOLIDATED")}

    # ------------------------------------------------------------------ facts
    def facts(self, session: str) -> dict[str, Any]:
        """Trusted, LLM-free record of what the session observed and produced."""
        from jarvis.autonomous_research import AutonomousResearch
        from jarvis.autonomy_report import session_outcome

        engine = AutonomousResearch.__new__(AutonomousResearch)
        engine.store = self.store
        state = engine.state(session)
        contract = self.store.artifacts.get(self.store.manifest(session)["spec"]["contract_ref"])
        own = lambda kind: [  # noqa: E731
            r for p in _payloads(self.store, session, "ARTIFACTS_REGISTERED") for r in p[kind]
        ]
        failed, rejected, analyses = [], [], []
        for index, done in sorted(state["completed"].items()):
            result = self.store.artifacts.get(done["result_ref"])
            if done["kind"] == "IMPLEMENT" and result.get("status") == "REJECTED":
                rejected.append(
                    {
                        "result_ref": done["result_ref"],
                        "validation": str(result.get("validation"))[:600],
                    }
                )
            elif result.get("status") in {
                "FAILED",
                "BUDGET_EXCEEDED",
                "INVALID_FINISH",
                "STEP_LIMIT",
            }:
                failed.append(
                    {
                        "index": index,
                        "kind": done["kind"],
                        "result_ref": done["result_ref"],
                        "error": str(result.get("error") or result.get("status"))[:400],
                    }
                )
            if done["kind"] == "ANALYZE" and result.get("status") == "OBSERVED":
                analyses.append(
                    {
                        "result_ref": done["result_ref"],
                        "question": str(result.get("question"))[:600],
                        "summary": str(result.get("summary"))[:1200],
                        "reproducible": bool(
                            (result.get("reproducible") or {}).get("all_identical")
                        ),
                        "scripts": sorted(result.get("scripts", {})),
                    }
                )
        questions = sorted(
            {
                q
                for r in state["reviews"].values()
                for q in self.store.artifacts.get(r["review_ref"])["next_questions"]
            }
        )[:12]
        outcome = session_outcome(self.store, session)
        stops = _payloads(self.store, session, "SESSION_STOPPED")
        return {
            "kind": "SessionMemoryFacts",
            "session": session,
            "domain": contract["domain"],
            "task_id": contract["task_id"],
            "status": self.store.status(session),
            "conclusion": stops[-1]["conclusion"] if stops else None,
            "observations": own("observations"),
            "candidates": own("candidates"),
            "models": own("models"),
            "patches": own("patches"),
            "tables": own("tables"),
            "analyses": analyses,
            "rejected_implementations": rejected,
            "failed_actions": failed,
            "branch_closures": [
                {
                    k: v
                    for k, v in b["closure"].items()
                    if k in {"branch_id", "conclusion", "reason"}
                }
                | {"kind": b["kind"], "goal": b["goal"]}
                for b in state["branches"].values()
                if b["closure"]
            ],
            "finalist": outcome.get("finalist"),
            "open_questions": questions,
            "usage": outcome["usage"]["actual"],
        }

    # ------------------------------------------------------------------ consolidation
    def consolidate(
        self,
        session: str,
        *,
        provider_factory: Any = None,
        config: AgentConfig | None = None,
    ) -> dict[str, Any]:
        """Idempotent. Without a provider only trusted facts are recorded (no lessons/skills)."""
        if self.store.manifest(session).get("kind") != "AutonomousResearchSession":
            raise AdmissionError("only autonomous research sessions are consolidated")
        status = self.store.status(session)
        if status not in TERMINAL and self.store.get_control(session) not in {
            "PAUSED",
            "CANCELLED",
        }:
            raise AdmissionError(
                f"session is still {status}; consolidate after it ends or is paused"
            )
        kb = self.ensure(session_domain(self.store, session))
        done = self.consolidated(kb).get(session)
        if done:
            return dict(done)
        facts = self.facts(session)
        facts_ref = self.store.artifacts.put(facts)
        lessons: list[str] = []
        skills: list[str] = []
        superseded: list[str] = []
        curation_ref = None
        if provider_factory is not None:
            curation = self._curate(kb, session, facts, provider_factory, config or AgentConfig())
            curation_ref = self.store.artifacts.put(curation)
            for lesson in curation.lessons:
                lessons.append(
                    self.store.artifacts.put(
                        {
                            "kind": "MemoryLessonEntry",
                            "session": session,
                            "domain": facts["domain"],
                            **lesson.model_dump(mode="json"),
                        }
                    )
                )
            superseded = list(curation.superseded_lessons)
            for proposal in curation.skills:
                skills.append(self._promote(kb, facts, proposal))
        value = {
            "session": session,
            "facts_ref": facts_ref,
            "curation_ref": curation_ref,
            "lessons": lessons,
            "skills": skills,
            "superseded": superseded,
        }
        self.store.append(kb, "SESSION_CONSOLIDATED", value)
        return value

    def _curate(
        self,
        kb: str,
        session: str,
        facts: dict[str, Any],
        provider_factory: Any,
        config: AgentConfig,
    ) -> CurationBatch:
        from jarvis.autonomy_report import deliverable

        report = deliverable(self.store, session)
        memory = self.snapshot(facts["domain"])
        active = {x["ref"] for x in memory["lessons"]}
        reproducible = {a["result_ref"] for a in facts["analyses"] if a["reproducible"]}
        from jarvis.autonomous_research import AutonomousResearch, clip

        engine = AutonomousResearch.__new__(AutonomousResearch)
        engine.store = self.store
        visible = list(dict.fromkeys([*engine.state(session)["visible"], *active]))
        context = {
            "session": session,
            "task": report["task"],
            "research_plan": clip(report["research_plan"], 600, 30),
            "actions": clip(report["actions"], 400, 60),
            "comparison": clip(report["comparison"], 300, 40),
            "finalist": report["finalist"],
            "facts": clip(facts, 400, 60),
            "reproducible_analyses": [a for a in facts["analyses"] if a["reproducible"]],
            "existing_lessons": clip(memory["lessons"], 400, 60),
            "existing_skills": [
                {"name": s["name"], "version": s["version"], "description": s["description"]}
                for s in memory["skills"]
            ],
            "instruction": "Distil what future research in this domain should remember. Lessons must cite this "
            "session's evidence. Propose skills only from reproducible analyses whose scripts would "
            "help later sessions. Mark existing lessons that this session's evidence contradicts.",
            "visible_evidence_ids": visible,
        }

        def check(answer: Record, frozen: dict[str, Any]) -> None:
            assert isinstance(answer, CurationBatch)
            allowed = set(frozen["visible_evidence_ids"])
            for lesson in answer.lessons:
                if not set(lesson.evidence_refs) <= allowed:
                    raise ValueError(
                        "lesson evidence must be visible evidence from this session or memory"
                    )
            if not set(answer.superseded_lessons) <= active:
                raise ValueError("only existing active lessons can be superseded")
            names = [s.name for s in answer.skills]
            if len(names) != len(set(names)):
                raise ValueError("skill names must be unique")
            for proposal in answer.skills:
                if proposal.analysis_result_ref not in reproducible:
                    raise ValueError("skills come only from reproducible analyses of this session")

        batch = evidence_role(
            self.store,
            kb,
            f"curate:{session}",
            "autonomy_curator",
            context,
            CurationBatch,
            provider_factory(config, "curator"),
            config,
            "",
            validate=check,
        )
        assert isinstance(batch, CurationBatch)
        return batch

    def _promote(self, kb: str, facts: dict[str, Any], proposal: SkillProposal) -> str:
        analysis = self.store.artifacts.get(proposal.analysis_result_ref)
        existing = [
            s for s in self.snapshot(facts["domain"])["skills"] if s["name"] == proposal.name
        ]
        skill = {
            "kind": "ResearchSkill",
            "domain": facts["domain"],
            "name": proposal.name,
            "version": (max(s["version"] for s in existing) + 1) if existing else 1,
            "description": proposal.description,
            "usage": proposal.usage,
            "inputs": proposal.inputs,
            "files": dict(analysis.get("scripts", {})),
            "origin": {
                "session": facts["session"],
                "analysis_result_ref": proposal.analysis_result_ref,
                "question": analysis.get("question"),
            },
            "validation": {
                "reproduced_byte_identical": True,
                "rerun": analysis.get("reproducible"),
            },
        }
        if not skill["files"]:
            raise IntegrityError("a skill needs at least one script")
        for ref in skill["files"].values():
            self.store.blobs.get(ref)
        return self.store.artifacts.put(skill)

    # ------------------------------------------------------------------ snapshot
    def snapshot(self, domain: str) -> dict[str, Any]:
        kb = self.find(domain)
        empty: dict[str, Any] = {
            "kind": "ResearchMemorySnapshot",
            "domain": domain,
            "knowledge_base": kb,
            "sessions": [],
            "lessons": [],
            "experiments": [],
            "candidates": [],
            "tables": [],
            "skills": [],
            "open_questions": [],
        }
        if kb is None:
            return empty
        records = list(self.consolidated(kb).values())
        superseded = {r for p in records for r in p["superseded"]}
        sessions, experiments, candidates, tables, questions = [], [], [], [], []
        lessons: list[dict[str, Any]] = []
        skills_by_name: dict[str, dict[str, Any]] = {}
        for p in records:
            facts = self.store.artifacts.get(p["facts_ref"])
            sessions.append(
                {
                    "session": facts["session"],
                    "task_id": facts["task_id"],
                    "status": facts["status"],
                    "conclusion": facts["conclusion"],
                    "finalist": facts["finalist"],
                    "failed_actions": len(facts["failed_actions"]),
                    "rejected_implementations": facts["rejected_implementations"][:5],
                    "branch_closures": facts["branch_closures"],
                }
            )
            experiments += facts["observations"]
            candidates += facts["candidates"]
            tables += facts["tables"]
            questions += facts["open_questions"]
            for ref in p["lessons"]:
                statement = self.store.artifacts.get(ref)["statement"]
                if ref not in superseded and statement not in {x["statement"] for x in lessons}:
                    lessons.append(
                        {
                            "ref": ref,
                            **{
                                k: v
                                for k, v in self.store.artifacts.get(ref).items()
                                if k
                                in {"statement", "kind", "applicability", "confidence", "session"}
                            },
                        }
                    )
            for ref in p["skills"]:
                skill = self.store.artifacts.get(ref)
                current = skills_by_name.get(skill["name"])
                if current is None or skill["version"] > current["version"]:
                    skills_by_name[skill["name"]] = {
                        "ref": ref,
                        **{
                            k: skill[k]
                            for k in ("name", "version", "description", "usage", "inputs")
                        },
                    }
        return {
            **empty,
            "sessions": sessions,
            "lessons": lessons,
            "experiments": list(dict.fromkeys(experiments)),
            "candidates": list(dict.fromkeys(candidates)),
            "tables": list(dict.fromkeys(tables)),
            "skills": sorted(skills_by_name.values(), key=lambda s: s["name"]),
            "open_questions": list(dict.fromkeys(questions))[-20:],
        }

    def pin(self, domain: str) -> str:
        """Immutable memory snapshot artifact for a new session."""
        return self.store.artifacts.put(self.snapshot(domain))

    # ------------------------------------------------------------------ store-level operations
    def consolidate_store(
        self, *, provider_factory: Any = None, config: AgentConfig | None = None
    ) -> list[dict[str, Any]]:
        with self.store.connect() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM campaigns")]
        results = []
        for cid in ids:
            m = self.store.manifest(cid)
            if m.get("kind") != "AutonomousResearchSession" or m.get("budget_owner"):
                continue  # Dream-owned sessions are consolidated by their round's histories
            try:
                results.append(
                    self.consolidate(cid, provider_factory=provider_factory, config=config)
                )
            except (AdmissionError, JarvisError) as exc:
                results.append({"session": cid, "skipped": str(exc)[:200]})
        return results

    def export(self, domain: str, target: Path) -> dict[str, Any]:
        """Self-contained bundle: knowledge events plus every referenced artifact and blob."""
        kb = self.find(domain)
        if kb is None:
            raise AdmissionError("no knowledge for domain " + domain)
        records = list(self.consolidated(kb).values())
        artifacts, blobs = set(), set()
        stack = [
            r
            for p in records
            for r in [p["facts_ref"], p["curation_ref"], *p["lessons"], *p["skills"]]
            if r
        ]
        while stack:
            ref = stack.pop()
            if ref in artifacts or ref in blobs:
                continue
            if self.store.artifacts.path(ref).exists():
                artifacts.add(ref)
                stack += [v for v in _strings(self.store.artifacts.get(ref)) if REF.match(v)]
            elif self.store.blobs.path(ref).exists():
                blobs.add(ref)
        target.mkdir(parents=True, exist_ok=True)
        for kind, refs, src in (
            ("artifacts", artifacts, self.store.artifacts),
            ("blobs", blobs, self.store.blobs),
        ):
            for ref in refs:
                dest = target / kind / ref[7:9] / ref[7:]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src.path(ref), dest)
        (target / "knowledge.json").write_text(
            json.dumps({"domain": domain, "records": records}, indent=1)
        )
        return {
            "domain": domain,
            "sessions": len(records),
            "artifacts": len(artifacts),
            "blobs": len(blobs),
        }

    def import_bundle(self, source: Path) -> dict[str, Any]:
        bundle = json.loads((source / "knowledge.json").read_text())
        for kind, dst in (("artifacts", self.store.artifacts), ("blobs", self.store.blobs)):
            for path in (source / kind).rglob("*"):
                if path.is_file():
                    ref = "sha256:" + path.name
                    data = path.read_bytes()
                    stored = dst.put(json.loads(data)) if kind == "artifacts" else dst.put(data)
                    if stored != ref:
                        raise IntegrityError("imported content hash mismatch")
        kb = self.ensure(bundle["domain"])
        known = self.consolidated(kb)
        added = 0
        for record in bundle["records"]:
            if record["session"] not in known:
                self.store.append(
                    kb, "SESSION_CONSOLIDATED", {**record, "imported_from": str(source)}
                )
                added += 1
        return {"domain": bundle["domain"], "imported_sessions": added}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)] + [
            k for k in value if isinstance(k, str)
        ]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []
