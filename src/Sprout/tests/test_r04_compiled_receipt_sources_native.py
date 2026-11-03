"""Real compiler and native Local Receipt publication over conditional retries."""

import json
import os
import tempfile
import traceback
from dataclasses import asdict, replace
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from Sprout.config.loader import default_settings
from Sprout.llm.messages import LLMResponse, ToolCall
from Sprout.orchestration.compiler import ExecutionGraphBuilder
from Sprout.orchestration.models import ExecutionNode, NodeType
from Sprout.tests.r04_g1_native_cases import git
from Sprout.tests.test_r04_e_apply_native import prepare_apply_fixture

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Explicit Windows native entry required",
)


@pytest.mark.parametrize("mode", [
    "first_success", "second_success", "both_failed", "ambiguous",
    "wrong_owner", "wrong_source", "no_retry",
])
async def test_compiled_receipt_sources_native(tmp_path, monkeypatch, mode):
    ctx = await prepare_apply_fixture(tmp_path, monkeypatch, "cli")
    runtime, store, task = ctx["runtime"], ctx["storage"].metadata, ctx["task"]
    discovered = "tests/test_discovery.py"
    (ctx["root"] / discovered).write_text("def test_discovery():\n    assert True\n")
    git(ctx["root"], "add", "--", discovered)
    git(ctx["root"], "commit", "-m", "ordinary test discovery fixture")
    task = replace(task, metadata={**task.metadata,
        "required_paths": [*ctx["files"], discovered]})
    await store.save_task(task)
    runtime._graph_builder = ExecutionGraphBuilder()  # Actual product compiler, not fixture graph.
    runtime._settings = default_settings()
    runtime._settings.runtime.verify_loops = 1 if mode == "no_retry" else 2
    messages = []
    chat = ctx["model"].chat

    async def observed_chat(history, **kwargs):
        messages.append([str(m.content or "") for m in history])
        return await chat(history, **kwargs)

    monkeypatch.setattr(ctx["model"], "chat", observed_chat)
    admit = runtime._admit_node_products

    async def traced_admission(scope, nodes):
        try:
            return await admit(scope, nodes)
        except PermissionError:
            (tmp_path / "ADMISSION_FAILURE.txt").write_text(traceback.format_exc())
            raise

    monkeypatch.setattr(runtime, "_admit_node_products", traced_admission)
    load = runtime._load_node_product

    async def traced_load(scope, node):
        try:
            return await load(scope, node)
        except PermissionError:
            source = await store.get_node_result_source(node.id)
            (tmp_path / "LOAD_FAILURE.json").write_text(json.dumps({
                "consumer": scope.node.id, "source_node": node.id,
                "source_type": node.type.value, "guard": node.metadata.get("execution_guard"),
                "source_record": str(source),
            }, default=str, indent=2))
            raise

    monkeypatch.setattr(runtime, "_load_node_product", traced_load)
    if mode in {"second_success", "both_failed"}:
        ctx["model"].responses[1] = LLMResponse(tool_calls=(ToolCall(
            str(uuid4()), "sandbox_write_file",
            {"path": "src/app.py", "content": "def echo(value):\n    return 99\n"}),))
        text = ctx["new"] if mode == "second_success" else "def echo(value):\n    return 99\n"
        ctx["model"].responses.extend([
            LLMResponse(tool_calls=(ToolCall(str(uuid4()), "sandbox_read_file",
                {"path": "src/app.py"}),)),
            LLMResponse(tool_calls=(ToolCall(str(uuid4()), "sandbox_write_file",
                {"path": "src/app.py", "content": text}),)),
            LLMResponse(content="Finished retry"),
        ])
    if mode == "ambiguous":
        # Explicit Host fixture with two independent successful evaluations.
        # A READ barrier prevents the scheduler's normal retry-skip optimization;
        # both Receipts still require real execution and completed Guards.
        compiler = runtime._graph_builder

        class IndependentEvaluations(ExecutionGraphBuilder):
            def build(self, *args, **kwargs):
                graph = compiler.build(*args, **kwargs)
                nodes = []
                for n in graph.nodes:
                    if n.type is NodeType.AGENT and n.id.endswith(":agent:1"):
                        continue
                    if n.type is NodeType.EVALUATION and n.id.endswith(":evaluation:1"):
                        n = replace(n, dependencies=(task.id + ":independent-read",))
                    nodes.append(n)
                    if n.type is NodeType.EVALUATION and n.id.endswith(":evaluation"):
                        nodes.append(ExecutionNode(
                            id=task.id + ":independent-read", task_id=task.id,
                            type=NodeType.READ, dependencies=(n.id,)))
                return replace(graph, nodes=tuple(nodes))

        runtime._graph_builder = IndependentEvaluations()
        ctx["model"].responses.append(LLMResponse(content="No further edit necessary"))
    await runtime.configure_local_git_review(
        task.id, task.actor, executable=ctx["git"],
        executable_sha256="cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96",
        export_parent=tempfile.mkdtemp(prefix="r04-receipt-sources-"))
    publish = runtime._prepare_local_apply_proposal
    observed = {}

    async def publishing(frame):
        receipts = [r for r in runtime._local_acceptance_receipts if r.task_id == task.id]
        observed["receipts"] = [{"node": r.node_id, "owner": r.owner,
                                  "sha256": r.payload_sha256} for r in receipts]
        observed["dependencies"] = list(frame.node.dependencies)
        if receipts and mode in {"wrong_owner", "wrong_source"}:
            # Negative tampering only; never manufacture a successful credential.
            object.__setattr__(receipts[0], "owner" if mode == "wrong_owner" else "node_id",
                               "not-the-authoritative-source")
        return await publish(frame)

    monkeypatch.setattr(runtime, "_prepare_local_apply_proposal", publishing)
    try:
        result = await runtime._execute_task(task, artifact_snapshot=None)
        nodes = await store.list_execution_nodes(task.id)
        proposals = await store.list_change_proposals(task.id)
        evaluations = [n for n in nodes if n.type is NodeType.EVALUATION]
        approval = next((n for n in nodes if n.type is NodeType.APPROVAL), None)
        sources = {}
        for n in nodes:
            source = await store.get_node_result_source(n.id)
            if source is not None:
                sources[n.id] = asdict(source)
        (tmp_path / "SOURCE_EVIDENCE.json").write_text(json.dumps({
            "mode": mode, "task": task.id, "result": str(result), "observed": observed,
            "nodes": [{"id": n.id, "type": n.type.value, "status": n.status.value,
                       "dependencies": n.dependencies, "metadata": n.metadata} for n in nodes],
            "proposals": [p.id for p in proposals],
            "sources": sources, "model_messages": messages,
        }, default=str, indent=2))
        assert approval is not None, [(n.type.value, n.metadata.get("error")) for n in nodes]
        assert set(approval.dependencies) == {n.id for n in evaluations}
        if mode in {"second_success", "both_failed"}:
            agents = [n for n in nodes if n.type is NodeType.AGENT]
            assert len(agents) == 2 and all(n.status.value == "completed" for n in agents)
            assert all(n.status.value == "completed" for n in evaluations)
            assert evaluations[0].metadata["output"]["status"] == "failed"
            assert "Previous verification feedback:" in str(messages)
            assert "return 99" in str(messages)  # Actual Broker read in retry ToolCall.
            for n in [*agents, *evaluations]:
                source = sources[n.id]
                assert source["owner"] == n.metadata["execution_guard"]["owner"]
                assert source["task_id"] == task.id
            assert all(n.id in sources for n in agents)
            for agent in agents:
                evidence = agent.metadata["output"]["sandbox_tool_results"]
                assert evidence and all(e["task_id"] == task.id for e in evidence)
                assert all(e["owner"] == sources[agent.id]["owner"] for e in evidence)
        if mode in {"first_success", "second_success", "no_retry"}:
            assert len(proposals) == 1, [(n.type, n.metadata.get("error")) for n in nodes]
            chosen = observed["receipts"]
            assert len(chosen) == 1
            expected = task.id + (":evaluation:1" if mode == "second_success" else ":evaluation")
            assert chosen[0]["node"] == expected
            assert proposals[0].metadata["git_diff_status"] == "available"
            if mode == "first_success":
                retry = next(n for n in evaluations if n.id.endswith(":evaluation:1"))
                assert retry.status.value == "skipped"
                assert next(n for n in nodes if n.id.endswith(":agent:1")).status.value == "skipped"
            if mode in {"first_success", "second_success"}:
                from Sprout.gateway.rpc_gateway import RPCGateway

                response = await RPCGateway(runtime).handle({"id": 1,
                    "method": "proposal.approve", "params": {"proposal_id": proposals[0].id}},
                    principal=task.actor)
                assert "error" not in response
                applied = await store.get_change_proposal(proposals[0].id)
                assert applied.status.value == "applied"
                assert (ctx["root"] / "src/app.py").read_bytes() == (
                    applied.metadata["apply_plan"]["files"][0]["new_text"].encode())
                (tmp_path / "APPLY_EVIDENCE.json").write_text(json.dumps({
                    "proposal": applied.id, "metadata": applied.metadata,
                    "workspace_sha256": sha256(
                        (ctx["root"] / "src/app.py").read_bytes()).hexdigest(),
                    "nodes": [{"id": n.id, "status": n.status.value, "metadata": n.metadata}
                              for n in await store.list_execution_nodes(task.id)],
                }, default=str, indent=2))
        else:
            assert not proposals
            if mode == "ambiguous":
                assert len(observed["receipts"]) == 2
            if mode == "both_failed":
                assert not runtime._local_acceptance_receipts
                assert (ctx["root"] / "src/app.py").read_text() == ctx["old"]
                assert all(n.metadata["output"]["status"] == "failed" for n in evaluations)
                assert approval.status.value == "completed"
                assert approval.metadata["output"]["status"] == "failed"
                assert approval.metadata["output"]["reason"] == "local_acceptance_failed"
        if mode in {"first_success", "second_success"}:
            transaction = await store.get_apply_transaction(str(uuid5(
                NAMESPACE_URL, "ordinary-apply:" + proposals[0].id)))
            assert transaction["phase"] == "COMPLETED"
        else:
            assert not await store._db.fetchall(
                "SELECT id FROM isolated_git_transactions WHERE operation='apply_v1'")
    finally:
        await runtime.stop()
        store.close()
