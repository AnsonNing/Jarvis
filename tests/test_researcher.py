import json
import sys

import pytest
from pydantic import ValidationError

from jarvis.campaign import CampaignEngine
from jarvis.contracts import AdmissionError
from jarvis.providers import CodexProvider
from jarvis.researcher import (
    AgentConfig,
    ExperimentDesign,
    ExperimentParameters,
    ResearchTurn,
    admit_turn,
    prompt_context,
    render_prompt,
    structured_schema,
    validate_parameters,
)
from jarvis.storage import Store
from jarvis.synthetic import SyntheticAdapter


def proposal(state, action="inspect", parameters=None, request_id="request"):
    template = next(a for a in state.available_actions if a.action_id == action)
    return ResearchTurn(
        request_id=request_id,
        state_hash=state.identity,
        analysis_summary="Measure the declared baseline first",
        evidence_refs=state.visible_evidence_ids,
        hypothesis=template.hypothesis.model_copy(update={"origin": "agent_proposed"}),
        experiment=ExperimentDesign(
            template_id=action,
            intervention="Measure once",
            comparator="frozen_baseline",
            controlled_variables=("dataset",),
            parameters=parameters or ExperimentParameters(),
            expected_observation="A measured result",
            success_criterion="Frozen comparison remains valid",
        ),
        next_direction="STOP" if action == "stop" else "CONTINUE",
        limitations=("Fixture only",),
    )


@pytest.fixture
def state(tmp_path, task):
    store = Store(tmp_path / "evidence")
    engine = CampaignEngine(store)
    return engine.state(engine.create(task), SyntheticAdapter())


def test_research_turn_admits_only_visible_action_and_preserves_contract(state):
    turn = proposal(state)
    decision, action = admit_turn(turn, state, "request")
    assert action.origin == "agent_proposed"
    assert action.tool_id == "synthetic.inspect"
    assert decision.origin == "provider"
    assert action.hypothesis.hypothesis_id.startswith("hyp-")
    with pytest.raises(AdmissionError, match="STALE"):
        admit_turn(turn, state, "another-request")
    with pytest.raises(AdmissionError, match="EVIDENCE"):
        admit_turn(turn.model_copy(update={"evidence_refs": ("future",)}), state, "request")
    with pytest.raises(AdmissionError, match="UNKNOWN"):
        admit_turn(
            turn.model_copy(
                update={"experiment": turn.experiment.model_copy(update={"template_id": "shell"})}
            ),
            state,
            "request",
        )
    with pytest.raises(AdmissionError, match="stop"):
        admit_turn(turn.model_copy(update={"next_direction": "STOP"}), state, "request")


def test_parameter_admission_preserves_one_variable_control():
    validate_parameters("threshold_experiment", {"trading_cutoff_delta": -0.0001})
    validate_parameters("threshold_experiment", {"cancel_mode": "disabled"})
    validate_parameters("conditional_analysis", {"group_by": "day"})
    with pytest.raises(AdmissionError):
        validate_parameters(
            "threshold_experiment", {"cancel_mode": "disabled", "trading_cutoff_delta": 0.0001}
        )
    for args in [{}, {"trading_cutoff_delta": 0.0}, {"additional_cost": 0.1}]:
        with pytest.raises(AdmissionError):
            validate_parameters("threshold_experiment", args)
    with pytest.raises(AdmissionError):
        validate_parameters("inspect", {"additional_cost": 0.1})
    for args in [{"trading_cutoff_delta": 1}, {"additional_cost": -1}, {"arbitrary_command": "rm"}]:
        with pytest.raises(ValidationError):
            ExperimentParameters.model_validate(args)


def test_context_is_bounded_and_schema_closed(state, task):
    context = prompt_context(task, state, [])
    prompt = render_prompt(context)
    assert state.identity in prompt
    assert "locked_baseline" not in prompt
    schema = structured_schema()
    assert schema["required"] == list(schema["properties"])
    assert schema["additionalProperties"] is False
    assert "--" not in json.dumps(schema)


def fake_cli(tmp_path, mode="valid"):
    path = tmp_path / "fake-codex"
    body = """
import json,sys,os
from pathlib import Path
if '--version' in sys.argv:
 print('fixture-codex 1'); sys.exit(0)
prompt=sys.stdin.read()
answer=Path(sys.argv[sys.argv.index('-o')+1])
mode=MODE
print(json.dumps({'type':'thread.started','thread_id':'fixture-thread'}))
if mode=='tool': print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'forbidden'}}))
answer.write_text('{"ok":true}' if mode!='malformed' else 'bad-json')
if mode!='missing_usage': print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':20}}))
if mode=='failure': sys.exit(1)
"""
    path.write_text(f"#!{sys.executable}\n" + body.replace("MODE", repr(mode)))
    path.chmod(0o700)
    return path


@pytest.mark.parametrize(
    "mode,error",
    [
        ("valid", None),
        ("malformed", "INVALID_STRUCTURED_RESPONSE"),
        ("missing_usage", "PROVIDER_INCOMPLETE"),
        ("tool", "FORBIDDEN_PROVIDER_TOOL"),
        ("failure", "PROVIDER_INCOMPLETE"),
    ],
)
def test_real_subprocess_transport_and_usage(tmp_path, mode, error):
    executable = fake_cli(tmp_path, mode)
    provider = CodexProvider(AgentConfig(executable=str(executable)))
    assert provider.identity()["version"] == "fixture-codex 1"
    result = provider.complete("Synthetic prompt only", {"type": "object"}, tmp_path / "request")
    assert result["error"] == error
    if mode in {"missing_usage", "failure"}:
        assert result["cost"] is None
    else:
        assert result["cost"]["llm_tokens"] == 120  # cached input is not added twice
    assert (tmp_path / "request" / "prompt.txt").read_text() == "Synthetic prompt only"


def test_provider_disables_tools_and_does_not_use_shell(tmp_path):
    provider = CodexProvider(AgentConfig(executable=str(fake_cli(tmp_path))))
    command = provider.command(tmp_path)
    assert "--ignore-user-config" in command and "read-only" in command
    assert "features.shell_tool=false" in command
    assert "features.multi_agent=false" in command
    assert "--model" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
