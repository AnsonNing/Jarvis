import json
from pathlib import Path

import pytest

from jarvis.contracts import AdmissionError
from jarvis.native_execution import capability, execute_native
from jarvis.runtime_fixture import native_environment
from jarvis.storage import Store
from jarvis.workbench import NativeExecution, Workbench
from jarvis.workspace_agent import WorkerStep, execute_step, prepare_workspace


def test_native_cpp_driver_alias_links_stl_and_seals_receipt_inputs(tmp_path):
    env = native_environment()
    alias_dir = tmp_path / "compiler-bin"
    alias_dir.mkdir()
    alias = alias_dir / "clang++"
    alias.symlink_to(Path(env.native.compiler).resolve())
    spec = env.native.model_copy(update={"compiler": str(alias)})
    bench = Workbench(
        env.model_copy(
            update={"native": spec, "toolchain_paths": (*env.toolchain_paths, str(alias_dir))}
        )
    )
    if not capability(bench)["available"]:
        pytest.skip("OS sandbox unavailable")
    workspace = prepare_workspace(tmp_path / "workspace", inputs={}, sources={}, base={})
    (workspace / "work/probe.cpp").write_text(
        "#include <iostream>\n#include <vector>\n#include <functional>\n#include <stdexcept>\n"
        "int main(){std::vector<int> v{1,2,3};std::function<int()> f=[&](){return v.at(0)+v.at(1)+v.at(2);};"
        'try{throw std::runtime_error("synthetic");}catch(const std::runtime_error& e){if(!e.what())return 3;}'
        "std::cout<<f()<<std::endl;return f()==6?0:2;}\n"
    )
    s = Store(tmp_path / "store")
    first = execute_native(s, bench, workspace, "work/probe.cpp", 0)
    assert first["status"] == "PASS", first
    assert first["execution"]["stdout"].strip() == "6"
    assert first["compile_argv"][0] == str(alias)
    assert first["compiler_resolved"] == str(alias.resolve())
    assert first["compiler_unchanged"] is True
    assert "-lc++" not in first["compile_argv"] and "-lstdc++" not in first["compile_argv"]
    original = (workspace / first["input_path"]).read_bytes()
    second = execute_native(s, bench, workspace, "work/probe.cpp", 1)
    assert second["status"] == "PASS", second
    assert first["input_path"] in second["source_manifest"]
    assert first["input_path"] in second["source_blobs"]
    assert (workspace / first["input_path"]).read_bytes() == original


def test_native_code_cannot_read_host_write_outside_bind_network_or_fork(tmp_path):
    bench = Workbench(native_environment())
    if not capability(bench)["available"]:
        pytest.skip("OS sandbox unavailable")
    sentinel = tmp_path / "private-sentinel"
    sentinel.write_text("SYNTHETIC_SENTINEL")
    outside = tmp_path / "outside-write"
    workspace = prepare_workspace(tmp_path / "workspace", inputs={}, sources={}, base={})
    code = (
        "#include <cstdio>\n#include <unistd.h>\n#include <sys/socket.h>\n#include <netinet/in.h>\n"
        "int main(){\n"
        f'if(std::fopen({json.dumps(str(sentinel))},"r"))return 11;\n'
        f'if(std::fopen({json.dumps(str(outside))},"w"))return 12;\n'
        "int fd=socket(AF_INET,SOCK_STREAM,0); if(fd>=0){sockaddr_in a{};a.sin_family=AF_INET;a.sin_port=0;a.sin_addr.s_addr=htonl(INADDR_LOOPBACK);if(bind(fd,(sockaddr*)&a,sizeof(a))==0)return 13;close(fd);}\n"
        "int pid=fork();if(pid==0)_exit(14);if(pid>0)return 14;\n"
        'std::puts("isolation checks passed");return 0;}\n'
    )
    (workspace / "work/probe.cpp").write_text(code)
    s = Store(tmp_path / "store")
    result = execute_native(s, bench, workspace, "work/probe.cpp", 0)
    assert result["status"] == "PASS", result
    assert result["execution"]["stdout"].strip() == "isolation checks passed"
    assert sentinel.read_text() == "SYNTHETIC_SENTINEL" and not outside.exists()
    assert (
        s.artifacts.get(result["receipt_ref"])["source_blobs"]["work/probe.cpp"]
        in result["source_manifest"].values()
    )


def test_native_capability_does_not_follow_missing_or_changed_toolchain(tmp_path):
    env = native_environment()
    assert "native" not in env.model_copy(update={"native": None}).model_dump()
    env = env.model_copy(
        update={
            "native": NativeExecution(
                compiler=env.native.compiler, compiler_sha256="sha256:" + "0" * 64
            )
        }
    )
    bench = Workbench(env)
    assert capability(bench)["available"] is False
    with pytest.raises(AdmissionError, match="NATIVE_UNAVAILABLE"):
        execute_native(Store(tmp_path / "store"), bench, tmp_path, "work/probe.cpp", 0)


def test_worker_cannot_supply_native_command_flags_or_host_paths(tmp_path):
    bench = Workbench(native_environment())
    s = Store(tmp_path / "store")
    from jarvis.contracts import Budget

    o = s.create(
        {"world_id": "synthetic", "task": {"budget": Budget(max_compute_units=100).model_dump()}}
    )
    workspace = prepare_workspace(tmp_path / "workspace", inputs={}, sources={}, base={})
    step = WorkerStep(
        context_id="sha256:" + "1" * 64,
        note="synthetic denial",
        tool="run_native",
        path="work/probe.cpp",
        content=None,
        arguments=("-Xclang",),
        summary=None,
        findings=(),
        output_files=(),
        final_script=None,
    )
    result = execute_step(s, o, 0, step, workspace, bench, None)
    assert result["status"] == "ERROR" and "cannot be supplied" in result["error"]
    with pytest.raises(AdmissionError, match="work/"):
        execute_native(s, bench, workspace, "/tmp/undeclared.cpp", 1)
