"""SVC-C2-168 — inner domain workflow (SoT §4 Steps 3–6), wrapped by the GraphNode in graph.py.

    START → catalogue_delta_reconcile → change_statement_interpret
          → citation_anchor_retrieve → handover_summary_compose → END

  3 CatalogueDeltaReconcile   — offering join, field diff, presence, definition comparison
  4 ChangeStatementInterpret  — LLM: bounded classification into the closed taxonomy
  5 CitationAnchorRetrieve    — closed evidence index and citation list
  6 HandoverSummaryCompose    — the fixed handover template, deterministically filled

**Linear with per-node skip guards, deliberately.** ``add_conditional_edges`` does not branch when
the graph is driven through a ``GraphNode``, so every shipped Cat 2 template in this portfolio uses a
linear chain where each node returns ``{}`` early if an upstream step degraded. Reproducing that here
keeps the behaviour identical whether the graph is invoked directly or through the outer agent.

Step 4 is the LLM-essential one. The client reaches it through ``config["llm"]``, forwarded by the
outer ``GraphNode`` from the agent's own config; it issues one batched call for the whole packet.
When no client is configured the step degrades to the seeded deterministic path and declares it
(``interpretation_mode``).
"""

from __future__ import annotations

from typing import Any

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus
from langgraph.graph import END, START

from src.nodes.catalogue_delta_reconcile_node import CatalogueDeltaReconcileNode
from src.nodes.change_statement_interpret_node import ChangeStatementInterpretNode
from src.nodes.citation_anchor_retrieve_node import CitationAnchorRetrieveNode
from src.nodes.handover_summary_compose_node import HandoverSummaryComposeNode
from src.schemas.state import State

_SLOTS = (
    "catalogue_delta_reconcile",
    "change_statement_interpret",
    "citation_anchor_retrieve",
    "handover_summary_compose",
)


class CatalogueChangeHandoverWorkflow(BaseGraph):
    """Inner workflow: minimised catalogue change packet → composed handover."""

    @property
    def name(self) -> str:
        return "CatalogueChangeHandoverWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        """No mandatory inner-graph config.

        The taxonomy, the seeded vocabularies and the band boundaries are static, and ``llm`` is
        deliberately optional: a deployment without a configured client still produces a usable,
        fully grounded handover — it is simply marked
        ``interpretation_mode = "deterministic_fallback"`` instead of failing to compile.
        """
        return None

    def register_nodes(self) -> None:
        # No super() call — BaseGraph.register_nodes() is abstract, and initialize / finalize stay
        # an outer-backbone concern handled by AgentBaseGraph in graph.py.
        llm = self.config.get("llm")
        self._nodes["catalogue_delta_reconcile"] = CatalogueDeltaReconcileNode()
        self._nodes["change_statement_interpret"] = ChangeStatementInterpretNode(llm)
        self._nodes["citation_anchor_retrieve"] = CitationAnchorRetrieveNode()
        self._nodes["handover_summary_compose"] = HandoverSummaryComposeNode()

    def add_edges(self) -> None:
        self._sg.add_edge(START, _SLOTS[0])
        for current, following in zip(_SLOTS, _SLOTS[1:]):
            self._sg.add_edge(current, following)
        self._sg.add_edge(_SLOTS[-1], END)

    def route(self, state: dict[str, Any]) -> str:
        """Required by the BaseGraph ABC; never called on this linear topology.

        Degradation is handled by per-node skip guards rather than branching, so a degraded run still
        reaches the composing step and, through it, the outer S-3 gate.
        """
        return END if state.get("status") == AgentStatus.ERROR.value else _SLOTS[-1]

    def get_output(self, state: dict[str, Any]) -> dict[str, Any]:
        """Shape the sub_result consumed by the outer node's merge_output()."""
        return {
            "handover": state.get("handover", "{}"),
            "stated_changes": state.get("stated_changes", "[]"),
            "acknowledgements": state.get("acknowledgements", "[]"),
            "effective_dates": state.get("effective_dates", "[]"),
            "contradictions": state.get("contradictions", "[]"),
            "evidence_gaps": state.get("evidence_gaps", "[]"),
            "citations": state.get("citations", "[]"),
            "evidence_index": state.get("evidence_index", "[]"),
            "delta_record": state.get("delta_record", "{}"),
            "change_count": state.get("change_count", 0),
            "gap_count": state.get("gap_count", 0),
            "human_review_required": state.get("human_review_required", False),
            "interpretation_mode": state.get("interpretation_mode"),
            "error_code": state.get("error_code"),
            "status": state.get("status"),
        }
