import pytest
from test_research_critic import close, setup_program

from jarvis.contracts import JarvisError
from jarvis.critic_fixture import ReviewedDirectorFixture
from jarvis.research_lab import payloads
from jarvis.research_program import ResearchProgram, ResearchProgramSpec, verify_program
from jarvis.research_roles import RoleCaller


class MalformedDirector(ReviewedDirectorFixture):
    repairs = 0

    def complete(self, *args):
        response = super().complete(*args)
        if len(self.calls) == 1:
            response["answer"]["experiments"][0]["settings"]["trading_cutoff_delta"] = 0.0001
        self.repairs += 1
        return response


def repair_program(tmp_path):
    store, base, original, _, critic, backends = setup_program(tmp_path, rounds=3)
    provider = MalformedDirector()
    engine = ResearchProgram(
        store,
        provider_factory=lambda _: provider,
        critic_provider_factory=lambda _: critic,
        transport_factory=backends,
    )
    spec = ResearchProgramSpec.model_validate(store.manifest(original)["spec"])
    owner = engine.create(spec.model_copy(update={"bounded_role_repairs": True}))
    return store, engine, owner, provider


def test_known_invalid_joint_annotation_is_corrected_without_weakening_gate(tmp_path):
    store, engine, owner, provider = repair_program(tmp_path)
    close(engine, owner)
    rejected = payloads(store, owner, "PROGRAM_ROLE_REJECTED")
    assert len(rejected) == 1 and "single-factor" in rejected[0]["reason"]
    assert any(
        p.get("role_key", "").endswith(":repair:1")
        for p in payloads(store, owner, "DIRECTION_BATCH_PREPARED")
    )
    assert provider.repairs == 3
    assert verify_program(store, owner)["status"] == "VERIFIED"


def test_unknown_direction_never_enters_response_repair(tmp_path, monkeypatch):
    store, engine, owner, provider = repair_program(tmp_path)
    original = RoleCaller.call
    injected = False

    def interrupt(self, target, role, context, schema, **kwargs):
        nonlocal injected
        if role == "direction" and not injected:
            injected = True
            return original(self, target, role, context, schema, fault="after_start", **kwargs)
        return original(self, target, role, context, schema, **kwargs)

    monkeypatch.setattr(RoleCaller, "call", interrupt)
    with pytest.raises(JarvisError, match="injected"):
        for _ in range(10):
            engine.advance(owner)
    with pytest.raises(JarvisError, match="unknown"):
        engine.advance(owner)
    assert not payloads(store, owner, "PROGRAM_ROLE_REJECTED")
    assert provider.repairs == 0
    assert store.resource_totals(owner)["pending"]["llm_tokens"] == 65536
