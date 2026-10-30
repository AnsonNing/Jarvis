"""Unattended native/recovery acceptance. All scores/roles are deterministic toys."""

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from jarvis.autonomous_research import table_record
from jarvis.autonomy_contracts import AnalysisDelivery, AutonomyContract, ResearchStages
from jarvis.contracts import AdmissionError, Cost
from jarvis.identity import file_digest
from jarvis.planning_fixture import PlanningFixtureProvider, hypothesis
from jarvis.planning_fixture import declared_world as planning_world
from jarvis.research_executors import Executors
from jarvis.storage import Store
from jarvis.workbench import AnalysisEnvironment, NativeExecution, Workbench, WorkbenchLimits


def native_environment() -> AnalysisEnvironment:
    compiler = shutil.which("clang++") or shutil.which("g++")
    toolchain: list[str] = []
    flags = ["-std=c++17", "-O0"]
    if sys.platform == "darwin" and shutil.which("xcrun"):
        compiler = subprocess.check_output(["xcrun", "--find", "clang++"], text=True).strip()
        sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
        toolchain.append(sdk)
        flags += ["-isysroot", sdk]
    if not compiler:
        raise AdmissionError("synthetic native fixture needs an installed C++ compiler")
    compiler_path = Path(compiler).absolute()
    toolchain.append(str(compiler_path.parent.parent))
    return AnalysisEnvironment(
        native=NativeExecution(
            compiler=str(compiler_path),
            compiler_sha256=file_digest(compiler_path),
            flags=tuple(flags),
        ),
        toolchain_paths=tuple(toolchain),
        limits=WorkbenchLimits(wall_seconds=20, cpu_seconds=15),
    )


def declared_world(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    built = planning_world(store, {})
    original = AutonomyContract.model_validate(store.artifacts.get(built["contract_ref"]))
    env = native_environment()
    contract = original.model_copy(
        update={
            "task_id": "native_runtime_acceptance",
            "analysis_environment": env,
            "periods": ("dev", "confirm"),
            "analysis_delivery": AnalysisDelivery(),
            "stages": ResearchStages(selection="dev", confirmation=("confirm",)),
            "constraints": (
                "SIMULATED_FIXTURE; scripted roles and toy evaluator; no real research, Iris, provider, training or Dream calls",
            ),
        }
    )
    data = store.root / "runtime-fixture-input.csv"
    data.write_text("value\n1\n2\n3\n")
    input_ref = table_record(
        store,
        data,
        name="arithmetic",
        description="Synthetic arithmetic values",
        lineage={"origin": "declared_fixture"},
        exposure="synthetic",
    )
    return {
        **built,
        "contract_ref": store.artifacts.put(contract),
        "input_refs": (input_ref,),
        "executors": Executors(Workbench(env), backtester=built["executors"].backtester),
    }


class RuntimeFixtureProvider(PlanningFixtureProvider):
    def identity(self) -> dict[str, Any]:
        return {
            "provider": "runtime-fixture",
            "source": file_digest(Path(__file__)),
            "token_units": "simulated",
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        c = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        # Proved-ended, receipted transient transport error, injected exactly once
        # into each session. RoleCaller must retry the same frozen context itself.
        if schema["title"] == "AutonomyDecision" and not c["research_plan"]["branches"]:
            directory.mkdir(parents=True, exist_ok=True)
            marker = directory.parent / "fixture-transient-injected"
            if not marker.exists():
                marker.write_text("SIMULATED_FIXTURE")
                return {
                    "answer": None,
                    "error": "PROVIDER_TRANSIENT:synthetic",
                    "usage": {"input_tokens": 17},
                    "cost": Cost(llm_tokens=17).model_dump(),
                }
        return super().complete(prompt, schema, directory)

    def director(self, c: dict[str, Any]) -> dict[str, Any]:
        native = next((b for b in c["research_plan"]["branches"] if b["branch_id"] == "N"), None)
        if native is None or native["status"] == "open":
            first = native is None
            card = {
                "direction_id": "N",
                "kind": "diagnosis",
                "hypothesis": hypothesis("N"),
                "next_action": "ANALYZE",
                "effort": "low",
                "disposition": "ready" if first else "retired",
                "reason": "Execute the arithmetic native probe and regenerate its observed summary",
                "origin": "declared_fixture",
            }
            action = (
                {
                    "kind": "ANALYZE",
                    "question": "Compile and run a native sum assertion; report the sealed observed sum",
                    "input_refs": c["analyzable_evidence_ids"][:1],
                }
                if first
                else self.stop(
                    "N", [c["observations"][-1]["result_ref"]], success=False, scope="branch"
                )
            )
            if not first:
                action["conclusion"] = "inconclusive"
                action["closures"][0].update(
                    conclusion="supported",
                    reason="Native execution evidence recorded; no scientific improvement claim",
                )
            return {
                "context_id": c["context_id"],
                "situation": "Synthetic native pipeline acceptance",
                "guidance_responses": [],
                "branch": {
                    "branch_id": "N",
                    "kind": "diagnosis",
                    "goal": "Native evidence is executable",
                }
                if first
                else None,
                "question": {
                    "question_id": "q_N",
                    "parent_id": None,
                    "question": "Can the trusted native path execute?",
                    "hypotheses": [hypothesis("N"), hypothesis("null_N")],
                }
                if first
                else None,
                "action": {
                    "branch_id": "N",
                    "question_id": "q_N",
                    "hypothesis_ids": ["h_N"],
                    "rationale": "Synthetic protocol acceptance",
                    "expected_observation": "Receipted native result",
                    "evidence_refs": [],
                    **action,
                },
                "planning_review": {
                    "summary": "Native preflight",
                    "directions": [card],
                    "selected_direction_id": "N" if first else None,
                    "selection_reason": "Check callable capability before toy search",
                    "stop_basis": None,
                    "owner_question": None,
                },
            }
        view = copy.deepcopy(c)
        view["research_plan"]["branches"] = [
            b for b in view["research_plan"]["branches"] if b["branch_id"] != "N"
        ]
        view["observations"] = [o for o in view["observations"] if o["branch_id"] != "N"]
        answer = super().director(view)
        action = answer["action"]
        if (
            action["kind"] == "STOP"
            and action["scope"] == "session"
            and action["conclusion"] == "research_complete"
            and c["stage_progress"]["gate"] != "PASS"
        ):
            confirm = [r for r in c["experiment_ledger"] if r["period"] == "confirm"]
            action = (
                self.batch(
                    c["research_contract"]["baseline_candidate_ref"],
                    [("confirm_base", "baseline"), ("confirm_candidate", "alternative")],
                )
                if not confirm
                else {
                    "kind": "COMPARE",
                    "baseline_result_ref": confirm[0]["result_ref"],
                    "result_refs": [confirm[1]["result_ref"]],
                }
            )
            if action["kind"] == "BACKTEST":
                for experiment in action["experiments"]:
                    experiment["period"] = "confirm"
            answer["action"] = {**answer["action"], **action}
            for key in ("scope", "conclusion", "closures", "finalist_result_ref"):
                answer["action"].pop(key, None)
            card = answer["planning_review"]["directions"][-1]
            card.update(disposition="ready", next_action=action["kind"])
            answer["planning_review"].update(selected_direction_id="B", stop_basis=None)
        return answer

    def worker(self, c: dict[str, Any]) -> dict[str, Any]:
        transcript = c["transcript"]
        blank = {
            "context_id": c["context_id"],
            "note": "Synthetic native work",
            "path": None,
            "content": None,
            "arguments": [],
            "summary": None,
            "findings": [],
            "output_files": [],
            "final_script": None,
        }
        index = len(transcript)
        if index == 0:
            return {
                **blank,
                "tool": "write_file",
                "path": "work/probe.cpp",
                "content": '#include <iostream>\n#include <vector>\n#include <functional>\n#include <stdexcept>\nint main(){std::vector<int> v{1,2,3};std::function<int()> sum=[&](){return v.at(0)+v.at(1)+v.at(2);};try{throw std::runtime_error("synthetic");}catch(const std::runtime_error& e){if(!e.what())return 3;}int s=sum();if(s!=6)return 2;std::cout<<s<<std::endl;return 0;}\n',
            }
        if index == 1:
            return {**blank, "tool": "run_native", "path": "work/probe.cpp"}
        if index == 2:
            return {
                **blank,
                "tool": "run_python",
                "path": "work/summary.py",
                "content": "import glob,json\nr=json.load(open(glob.glob('inputs/native-*.json')[0]))\nassert r['status']=='PASS'\ns=int(r['execution']['stdout'].strip())\nassert s==6\nopen('work/answer.csv','w').write('sum\\n'+str(s)+'\\n')\nprint(s)\n",
            }
        return {
            **blank,
            "tool": "finish",
            "summary": "Observed synthetic native sum 6",
            "output_files": ["answer.csv"],
            "final_script": "summary.py",
        }
