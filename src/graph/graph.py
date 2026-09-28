"""SVC-C2-168 — outer graph (Cat 2).

AgentBaseGraph 5-node backbone; the domain complexity lives in the ``main`` slot behind
``CatalogueChangeHandoverWorkflowGraphNode``, a GraphNode wrapping the inner
``CatalogueChangeHandoverWorkflow``.

    START → initialize → pre_process → main(GraphNode) → post_process → finalize → END

The GraphNode is defined here, next to the outer graph, and **never under ``src/nodes/``**: PB-6
invokes every concrete node discovered under ``src/nodes/`` with a bare state, and
``GraphNode.execute()`` builds an ``InvocationContext`` from that state, so a GraphNode placed there
fails the boundary proof with ``KeyError: 'session_id'``. This matches the shipped Cat 2 templates.

``GraphNode.execute()`` is **not** overridden and there is no hand-rolled linear driver. Calling the
inner nodes' ``execute()`` directly would bypass ``BaseNode.__call__`` — the S-1 trust gate, the
S-2/S-3 hooks and the framework's lifecycle/S-4 events — so their declared
``required_trust_level = VERIFIED_EXTERNAL`` would not be enforced on the path production takes.
``tests/integration/test_inner_node_boundary.py`` asserts the traversal rather than the output,
because the published envelope is identical either way.
"""

from __future__ import annotations

from typing import Any, ClassVar

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.trust_level import TrustLevel

from src.graph.domain_workflow_graph import CatalogueChangeHandoverWorkflow
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State


class CatalogueChangeHandoverWorkflowGraphNode(GraphNode):
    """`main` slot — runs the handover workflow and merges its result back.

    Also the seam that carries the agent's LLM client into the inner workflow:
    ``Graph.register_nodes()`` hands over ``config["llm"]`` and ``_parent_config()`` forwards it to
    ``CatalogueChangeHandoverWorkflow``, which passes it to the one LLM-essential step. Without this
    hop the interpretation node would be constructed with no client and every production run would
    silently take the deterministic path.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    # Fail fast: an inner exception is a real defect, not a degraded answer.
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    # ★ Class-level cache, keyed on the identity of the injected llm client, with a capacity ceiling.
    #
    # Compiling the inner graph is expensive, so it is cached (``BaseGraph.invoke``'s
    # ``_ensure_compiled`` is idempotent). A plain single-slot cache would be wrong once a client can
    # be injected: the first construction wins, so an agent configured WITH a client could be handed
    # the deterministic subgraph an earlier construction cached. The client is held alongside the
    # workflow so identity is re-checked rather than merely hashed, and the ceiling stops a
    # long-running process accumulating one entry per client it has ever seen.
    _subgraph: ClassVar[dict[int, tuple[Any, CatalogueChangeHandoverWorkflow]]] = {}
    _subgraph_capacity: ClassVar[int] = 8

    def __init__(self, llm: Any = None) -> None:
        super().__init__()
        self._llm = llm

    def get_subgraph(self) -> CatalogueChangeHandoverWorkflow:
        cls = CatalogueChangeHandoverWorkflowGraphNode
        key = id(self._llm)
        cached = cls._subgraph.get(key)
        if cached is not None and cached[0] is self._llm:
            return cached[1]
        workflow = CatalogueChangeHandoverWorkflow(config=self._parent_config())
        entries = list(cls._subgraph.items())
        if len(entries) >= cls._subgraph_capacity:
            entries = entries[1:]
        cls._subgraph = dict(entries + [(key, (self._llm, workflow))])
        return workflow

    def _parent_config(self) -> dict[str, Any]:
        """Config handed to the inner workflow (SDK how-to: compose-agents-graphnode)."""
        return {"llm": self._llm}

    def extract_input(self, state: dict[str, Any]) -> str:
        """The inner graph reads the S-1/S-2 output, never the caller's raw body."""
        return state.get("validated_input", state.get("user_input", "{}")) or "{}"

    def merge_output(self, state: dict[str, Any], sub_result: dict[str, Any]) -> dict[str, Any]:
        merged = {
            "handover": sub_result.get("handover", "{}"),
            "stated_changes": sub_result.get("stated_changes", "[]"),
            "acknowledgements": sub_result.get("acknowledgements", "[]"),
            "effective_dates": sub_result.get("effective_dates", "[]"),
            "contradictions": sub_result.get("contradictions", "[]"),
            "evidence_gaps": sub_result.get("evidence_gaps", "[]"),
            "citations": sub_result.get("citations", "[]"),
            "evidence_index": sub_result.get("evidence_index", "[]"),
            "delta_record": sub_result.get("delta_record", "{}"),
            "change_count": sub_result.get("change_count", 0),
            "gap_count": sub_result.get("gap_count", 0),
            "human_review_required": bool(sub_result.get("human_review_required")),
            "interpretation_mode": sub_result.get("interpretation_mode"),
            "status": sub_result.get("status"),
        }
        # An error_code already set on the outer state (e.g. by pre_process) takes precedence: the
        # inner graph runs on the discarded body and would otherwise overwrite a rejection with
        # NO_CATALOGUE_DELTA.
        error_code = state.get("error_code") or sub_result.get("error_code")
        if error_code:
            merged["error_code"] = error_code
        return merged


class Graph(AgentBaseGraph):
    """Fixed-pipeline outer graph (Cat 2)."""

    @property
    def name(self) -> str:
        return "CatalogueChangeHandoverSummarizerAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        # super() injects the "initialize" and "finalize" slots automatically.
        super().register_nodes()

        # Domain pipeline slots (required — fill all three). The LLM client is read from config here
        # (the AgentCore convention) and threaded to the interpretation step via the GraphNode;
        # `.get()` rather than `["llm"]` because the deterministic fallback is a supported, declared
        # deployment state rather than a compile error.
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = CatalogueChangeHandoverWorkflowGraphNode((self.config or {}).get("llm"))
        self._nodes["post_process"] = PostProcessNode()

    def get_output(self, state: dict[str, Any]) -> dict[str, Any]:
        """Framework default, plus the guarantee that a success is never empty.

        The Marketplace runner rejects a successful invocation whose output is
        missing — verified on a deployed Pod — and a degraded run
        (SUCCESS + error_code) produces no artefact for the framework default
        to surface. Report the degradation instead: this states what happened,
        it does not invent an answer.

        Only on SUCCESS. A request refused by the framework's S-2 gate (status
        ERROR) must keep publishing nothing — answering a hostile input with a
        notice would undo the refusal, and the runner treats a non-success
        invocation as a failure regardless, so there is nothing to rescue.
        """
        out: dict[str, Any] = super().get_output(state)
        if not out.get("output") and str(state.get("status", "")).lower().endswith("success"):
            code = state.get("error_code") or "NO_CONTENT"
            out["output"] = (
                "This request could not be completed "
                f"(error_code={code}). No content was produced; "
                "see error_code and error_log for the degradation cause."
            )
        return out


# Registry alias: config/agent.yaml `class:` resolves to this name.
CatalogueChangeHandoverSummarizerAgent = Graph
