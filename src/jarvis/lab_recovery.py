"""Explicit successor recovery of known, completed research; no backend replay."""

from typing import Any

from jarvis.bounded_roles import evidence_role
from jarvis.campaign import source_identity
from jarvis.contracts import Budget, IntegrityError
from jarvis.identity import digest
from jarvis.lab_contracts import ExperienceProposal, LabSpec
from jarvis.lab_memory import make_experience
from jarvis.providers import CodexProvider, ResearchProvider
from jarvis.research_lab import payloads, verify_lab
from jarvis.research_program import program_report
from jarvis.research_roles import verify_roles
from jarvis.storage import Store


def verify_recovery_origin(store: Store, owner: str) -> dict[str, Any]:
    m = store.manifest(owner)
    origin = m["origin"]
    original = store.manifest(origin["episode"])
    if (
        m["kind"] != "ResearchExperienceRecovery"
        or not origin["authority"]
        or m["world_id"] != digest(origin)
        or store.artifacts.get(origin["episode_manifest_ref"]) != original
        or m["spec"] != original["spec"]
        or original["kind"] != "ResearchLabEpisode"
        or [x["program_id"] for x in payloads(store, origin["episode"], "LAB_PROGRAM_CREATED")]
        != [origin["program"]]
        or any(store.resource_totals_tree(origin["episode"])["pending"].values())
    ):
        raise IntegrityError("recovery requires immutable source lineage and settled original work")
    verify_lab(store, origin["episode"])
    report = program_report(store, origin["program"])
    if report["status"] != "CLOSED" or not report["experiments"]:
        raise IntegrityError("only a closed measured program may produce recovered experience")
    return report


class ExperienceRecovery:
    def __init__(self, store: Store, provider_factory: Any = CodexProvider):
        self.store, self.provider_factory = store, provider_factory

    def create(self, episode: str, authority: str, *, budget_owner: str | None = None) -> str:
        source = self.store.manifest(episode)
        links = payloads(self.store, episode, "LAB_PROGRAM_CREATED")
        if len(links) != 1 or not authority:
            raise IntegrityError("explicit recovery authority and one known program required")
        spec = LabSpec.model_validate(source["spec"])
        origin = {
            "episode": episode,
            "program": links[0]["program_id"],
            "authority": authority,
            "episode_manifest_ref": self.store.artifacts.put(source),
        }
        # Validate before creating anything chargeable.
        if any(self.store.resource_totals_tree(episode)["pending"].values()):
            raise IntegrityError("unknown original work must not be retried")
        verify_lab(self.store, episode)
        if program_report(self.store, origin["program"])["status"] != "CLOSED":
            raise IntegrityError("original research is not closed")
        return self.store.create(
            {
                "kind": "ResearchExperienceRecovery",
                "origin": origin,
                "world_id": digest(origin),
                "spec": source["spec"],
                "source_identity": source_identity(),
                "provider": self.provider_factory(spec.role_config).identity(),
                **({"budget_owner": budget_owner} if budget_owner else {}),
                "task": {
                    "budget": Budget(
                        max_actions=20,
                        max_tool_calls=20,
                        max_llm_tokens=262144,
                        max_wall_seconds=1500,
                        max_compute_units=20,
                    ).model_dump()
                },
            }
        )

    def advance(self, owner: str) -> dict[str, Any]:
        with self.store.campaign_lock(owner, scope="experience-recovery"):
            if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
                return {"status": self.store.get_control(owner)}
            m = self.store.manifest(owner)
            if m["source_identity"] != source_identity():
                raise IntegrityError("recovery runtime changed")
            report = verify_recovery_origin(self.store, owner)
            if not payloads(self.store, owner, "LAB_EXPERIENCE_CREATED"):
                spec = LabSpec.model_validate(m["spec"])
                provider: ResearchProvider = self.provider_factory(spec.role_config)
                if provider.identity() != m["provider"]:
                    raise IntegrityError("recovery provider changed")
                original = payloads(self.store, m["origin"]["episode"], "LAB_ROLE_RECORDED")
                rejected = [p for p in original if p["role"] == "curator"]
                context = {
                    "task_id": spec.task_id,
                    "family": spec.family,
                    "goal": spec.program.queue.goal,
                    "scope": spec.program.queue.environment.period.exposure,
                    "visible_evidence_ids": [r["result_ref"] for r in report["experiments"]],
                    "experiments": report["experiments"],
                    "baseline_comparisons": report["baseline_comparisons"],
                    "recovery": {
                        "source_episode": m["origin"]["episode"],
                        "previous_paid_curator_refs": [p["answer_ref"] for p in rejected],
                        "reason": "Previous curation could not be admitted; preserve original answer and costs. Cite exact completed result IDs only; no source/opinion IDs. No experiment is rerun.",
                    },
                }
                answer = evidence_role(
                    self.store,
                    owner,
                    "curate-recovered",
                    "curator",
                    context,
                    ExperienceProposal,
                    provider,
                    spec.role_config,
                    "Recover a provisional evidence-backed experience from known results. Preserve negative findings, counter-evidence and missing causal observations. Use only exact allowed result IDs.",
                )
                assert isinstance(answer, ExperienceProposal)
                ref = make_experience(self.store, owner, m["origin"]["program"], answer)
                self.store.append(owner, "LAB_EXPERIENCE_CREATED", {"experience_ref": ref})
                self.store.append(
                    owner,
                    "EXPERIENCE_RECOVERY_COMPLETED",
                    {
                        "source_episode": m["origin"]["episode"],
                        "experience_ref": ref,
                        "original_history_changed": False,
                        "backend_calls": 0,
                    },
                )
            self.store.set_status(owner, "CLOSED")
            verify_roles(self.store, owner)
            return {
                "status": "CLOSED",
                "recovery": owner,
                "experience_ref": payloads(self.store, owner, "LAB_EXPERIENCE_CREATED")[0][
                    "experience_ref"
                ],
                "usage": self.store.resource_totals_tree(owner),
                "backend_calls": 0,
            }
