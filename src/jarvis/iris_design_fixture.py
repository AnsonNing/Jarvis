"""Compiled artificial strategy design fixture; no market or execution claims."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal, cast

from jarvis.contracts import Cost, IntegrityError
from jarvis.identity import canonical, file_digest
from jarvis.iris_admission import attach_design
from jarvis.iris_contracts import (
    IrisAuthorization,
    IrisBinding,
    IrisEnvironment,
    IrisPeriod,
    IrisQueueSpec,
)
from jarvis.iris_design import (
    IrisBuildProfile,
    IrisDesignProfile,
    IrisRuleBinding,
    IrisSignalBinding,
    region_bodies,
)
from jarvis.iris_fixture import FixtureIrisTransport
from jarvis.providers import CodexProvider, ResearchProvider
from jarvis.research_program import ResearchProgram, ResearchProgramSpec, program_report
from jarvis.researcher import AgentConfig
from jarvis.storage import Store

PREFIX = "#include <cmath>\n#include <cstdint>\n#include <iostream>\n"
TAIL = r"""
int main() {
    struct Row { int day; double clock, score, risk, spread, pending, position, payoff; };
    Row rows[] = {{0,32700000,.6,.7,1,60,150,8}, {0,33000000,.7,.6,8,50,100,-9},
                  {0,36300000,.9,.9,2,80,50,-8}, {0,43800000,.8,.4,2,150,250,7},
                  {1,32640000,.65,.2,1,40,100,4}, {1,45600000,.8,.7,3,90,180,-3},
                  {1,50400000,.8,.2,1,20,20,5}};
    auto ms=[](uint32_t c){return ((c/10000000*60+c/100000%100)*60+c/1000%100)*1000+c%1000;};
    int i=0;
    for(auto row:rows) {
        bool entry=row.clock>=ms(toy_begin) && row.clock<ms(toy_end)
            && row.pending<=toy_pending && row.position<=toy_position
            && toyEntry(row.score,row.spread,row.clock)
            && !toyCancel(row.risk,row.spread,row.clock,false);
        std::cout<<i++<<" "<<row.day<<" "<<entry<<" "<<(entry?row.payoff*toy_volume:0)<<"\n";
    }
}
"""


def design_environment(folder: Path) -> IrisEnvironment:
    from jarvis.identity import digest

    folder.mkdir(parents=True, exist_ok=False)
    compiler = shutil.which("clang++") or shutil.which("g++")
    if compiler is None:
        raise IntegrityError("C++17 compiler required for compiled design acceptance")
    # The driver name selects C++ standard-library linkage. On Linux clang++
    # commonly links to clang; resolving that symlink changes driver behavior.
    # Hashing the retained path still pins the underlying executable bytes.
    compiler = str(Path(compiler).absolute())
    template = folder / "toy.txt"
    template.write_text(
        "// protected artificial strategy\n// @start:config\n"
        "constexpr double toy_entry = 0.5;\nconstexpr double toy_cancel = 0.8;\n"
        "constexpr double toy_volume = 0.5;\nconstexpr double toy_pending = 100;\nconstexpr double toy_position = 200;\n"
        "constexpr uint32_t toy_begin = 9'00'00'000;\nconstexpr uint32_t toy_end = 13'25'00'000;\nconstexpr uint32_t toy_flat = 13'25'30'000;\n"
        "// @end:config\n// @start:entry\n"
        "bool toyEntry(double score, double spread, double clock) { return score > toy_entry; }\n"
        "// @end:entry\n// @start:cancel\n"
        "bool toyCancel(double risk, double spread, double clock, bool expired) { if (expired) return true; return risk > toy_cancel; }\n"
        "// @end:cancel\n// protected end\n"
    )
    agent, evaluator = folder / "iris_agent.py", folder / "evaluator.py"
    agent.write_text("# Inert compiled fixture; zero service access\n")
    evaluator.write_text("# Fixed artificial evaluator; not market evidence\n")
    prefix, tail = folder / "prefix.cpp", folder / "tail.cpp"
    prefix.write_text(PREFIX)
    tail.write_text(TAIL)
    scalars = [
        {"setting": name, "symbol": symbol, "region": "config", "syntax": syntax}
        for name, symbol, syntax in [
            ("session_start_ms", "toy_begin", "clock"),
            ("session_end_ms", "toy_end", "clock"),
            ("flat_time_ms", "toy_flat", "clock"),
            ("hang_volume_ratio", "toy_volume", "double"),
            ("max_pending_notional", "toy_pending", "double"),
            ("max_position_notional", "toy_position", "double"),
        ]
    ]
    rules = []
    for setting, region, key, expression, anchor, replacement in [
        (
            "entry_rule",
            "entry",
            "entry_score",
            "score",
            "return score > toy_entry;",
            "return score > toy_entry && {{rule}};",
        ),
        (
            "cancel_rule",
            "cancel",
            "model_risk",
            "risk",
            "return risk > toy_cancel;",
            "return {{rule}};",
        ),
    ]:
        stage: Literal["entry_decision", "cancel_decision"] = (
            "entry_decision" if region == "entry" else "cancel_decision"
        )
        signals = [
            IrisSignalBinding(
                key=k,
                expression=e,
                description=d,
                units=u,
                minimum=lo,
                maximum=hi,
                availability=stage,
            )
            for k, e, d, u, lo, hi in [
                (key, expression, "Artificial decision score", "toy-score", 0, 1),
                ("spread_bps", "spread", "Artificial spread input", "bps", 0, 100),
                ("clock_ms", "clock", "Decision time since midnight", "ms", 0, 86399999),
            ]
        ]
        rules.append(
            IrisRuleBinding(
                setting=cast(Literal["entry_rule", "cancel_rule"], setting),
                region=region,
                anchor=anchor,
                replacement=replacement,
                signals=tuple(signals),
                preserved_invariants=(
                    "entry baseline guard"
                    if region == "entry"
                    else "expiry checked before custom rule",
                ),
            )
        )
    profile = IrisDesignProfile(
        rules=tuple(rules),
        scalar_bindings=tuple(IrisBinding.model_validate(s) for s in scalars),
        build=IrisBuildProfile(
            compiler=compiler, chunks=(str(prefix), "config", "entry", "cancel", str(tail))
        ),
    )
    profile_path = folder / "design.json"
    profile_path.write_bytes(canonical(profile) + b"\n")
    env = IrisEnvironment(
        root=str(folder),
        python="unused-fixture-runtime",
        branch="toy",
        template=str(template),
        template_sha256=file_digest(template),
        evaluator=str(evaluator),
        evaluator_sha256=file_digest(evaluator),
        agent_script_sha256=file_digest(agent),
        accepted_inputs={str(template): file_digest(template)},
        model_lineage=digest(
            {"scope": "compiled-artificial-design", "evaluator": file_digest(tail)}
        ),
        side="long",
        result_origin="SIMULATED_FIXTURE",
        bindings=tuple(
            IrisBinding.model_validate(b)
            for b in (
                {
                    "setting": "trading_cutoff_delta",
                    "symbol": "toy_entry",
                    "region": "config",
                    "syntax": "double",
                    "mode": "delta",
                },
                {
                    "setting": "cancel_risk_delta",
                    "symbol": "toy_cancel",
                    "region": "config",
                    "syntax": "double",
                    "mode": "delta",
                },
            )
        ),
        period=IrisPeriod.model_validate(
            {
                "start": "2026-01-05",
                "end": "2026-01-06",
                "trading_dates": ["2026-01-05", "2026-01-06"],
                "group_id": "compiled-toy-development",
            }
        ),
    )
    return attach_design(env, profile_path)


class CompiledDesignTransport(FixtureIrisTransport):
    def collect(self, request: dict[str, Any], directory: Path) -> dict[str, Any]:
        from jarvis.iris_design import read_profile

        profile = read_profile(self.environment)
        assert profile is not None
        directory.mkdir(parents=True, exist_ok=True)
        text = Path(request["candidate_path"]).read_text()
        regions = region_bodies(text)
        source = "\n".join(
            Path(c).read_text() if Path(c).is_absolute() else regions[c]
            for c in profile.build.chunks
        )
        cpp, binary = directory / "toy.cpp", directory / "toy"
        cpp.write_text(source)
        subprocess.run(
            [profile.build.compiler, "-std=c++17", str(cpp), "-o", str(binary)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        ran = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=5)
        values = [line.split() for line in ran.stdout.splitlines()]
        dates = [str(d) for d in self.environment.period.trading_dates]
        daily = {
            d: sum(float(row[3]) for row in values if int(row[1]) == i) for i, d in enumerate(dates)
        }
        pl = sum(daily.values())
        equity = peak = drawdown = 0.0
        for value in daily.values():
            equity += value
            peak = max(peak, equity)
            drawdown = min(drawdown, equity - peak)
        result = super().collect(request, directory)
        result.update(
            entry_only_pl=pl,
            count=sum(int(r[2]) for r in values),
            daily_pl=daily,
            mdd=drawdown,
            worst_day=min(daily.values()),
            risk={
                "fixture_only": True,
                "best_day_removed_pl": pl - max(daily.values()),
                "daily_realized_mdd": drawdown,
                "capacity": "NOT_VALIDATED",
            },
        )
        result["provenance"].update(
            compiled_fixture=True,
            compiler_sha256=file_digest(Path(profile.build.compiler)),
            output_sha256=file_digest(cpp),
            observed_rows=len(values),
        )
        return result


class DesignDirector:
    def __init__(self) -> None:
        self.calls = 0

    def identity(self) -> dict[str, Any]:
        return {
            "provider": "declared-compiled-design-fixture",
            "implementation": file_digest(Path(__file__)),
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        context = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        self.calls += 1
        designs = [
            (
                "entry",
                {
                    "entry_rule": {
                        "combine": "all",
                        "predicates": [{"signal": "spread_bps", "op": "le", "value": 3}],
                    }
                },
            ),
            (
                "cancel",
                {
                    "cancel_rule": {
                        "combine": "all",
                        "predicates": [
                            {"signal": "model_risk", "op": "gt", "value": 0.85},
                            {"signal": "spread_bps", "op": "gt", "value": 3},
                        ],
                    }
                },
            ),
            ("timing", {"session_end_ms": 45000000}),
            ("entry", {"max_position_notional": 160}),
        ]
        start = 0 if self.calls == 1 else 3
        chosen = designs[start : start + 3]
        return {
            "answer": {
                "kind": "ResearchDirections",
                "schema_version": 1,
                "context_id": context["context_id"],
                "analysis_summary": "Declared control: compare rule, timing and position interventions; retain negative outcomes before revising.",
                "experiments": [
                    {
                        "kind": "IrisExperiment",
                        "schema_version": 1,
                        "label": f"design{start + i}",
                        "family": family,
                        "hypothesis": "Artificial strategy-design hypothesis using only current signals",
                        "falsification": "No matched objective gain or risk regression",
                        "settings": settings,
                        "evidence_refs": context["visible_evidence_ids"],
                        "origin": "agent_proposed",
                    }
                    for i, (family, settings) in enumerate(chosen)
                ],
                "capability_requests": [],
                "stop_reason": "Declared two-batch fixture complete" if self.calls >= 2 else None,
            },
            "error": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "cost": Cost().model_dump(),
        }


def run_design_fixture(
    store: Store, *, agent: Literal["fixture", "codex"] = "fixture", executable: str = "codex"
) -> dict[str, Any]:
    import uuid

    from jarvis.identity import digest

    env = design_environment(store.root / "fixture-inputs" / uuid.uuid4().hex)
    transport = CompiledDesignTransport(env)
    cfg = AgentConfig(executable=executable, max_calls=2, reserve_tokens=65536, timeout_seconds=180)
    provider: ResearchProvider = CodexProvider(cfg) if agent == "codex" else DesignDirector()
    engine = ResearchProgram(
        store, provider_factory=lambda _: provider, transport_factory=lambda _: transport
    )
    pid = engine.create(
        ResearchProgramSpec(
            queue=IrisQueueSpec(
                goal="Maximize artificial entry-only PL; retain complete risk and negative findings",
                environment=env,
                authorization=IrisAuthorization(
                    environment_id=digest(env),
                    max_submissions=8,
                    expires_at=None,
                    owner_note="Artificial design acceptance only",
                ),
            ),
            directions=(
                "Explore declared cancellation/entry rules, schedules and position limits after observing results",
            ),
            max_direction_batches=2,
            agent=cfg,
        )
    )
    for _ in range(100):
        if engine.advance(pid)["status"] == "CLOSED":
            break
    else:
        raise IntegrityError("compiled design fixture did not close")
    report = program_report(store, pid)
    return {
        "kind": "CompiledStrategyDesignAcceptance",
        "origin": "SIMULATED_FIXTURE",
        "director": agent,
        "remote_iris_submissions": 0,
        "program_id": pid,
        "report_ref": store.artifacts.put(report),
        "fixture_executions": len(transport.submissions),
        "verification": report["program_verification"],
    }
