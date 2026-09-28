"""SVC-C2-168 — inner Step 5: CitationAnchorRetrieve.

Retrieval only. Every anchor published by this run is minted from a value that already passed S-1's
single resolution point, so the closed set is derived from the minimised packet rather than from the
statements composed downstream — a statement that names an anchor the packet never carried therefore
cannot be self-certifying, and S-3 catches it against this index.

Nothing here invents a reference. An annex number, a version or an owner name that is not in the
packet has no anchor, and the statement that needed it fails the S-3 citation check instead.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import surrogates_in
from src.utils.audit import emit_trace_event


class CitationAnchorRetrieveNode(FunctionNode):
    """Mint the closed evidence index and the citation list from the minimised packet."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code"):
            emit_trace_event("citation_anchor_retrieve.skipped", {"reason": state.get("error_code")}, state)
            return {}

        # Every surrogate the packet carries after S-1/S-2: resolved provenance (`src:`) and
        # tokenised join keys (`ofr:`, `own:`, `pkt:` …). Reading them off the packet — rather than
        # off the composed statements — is what makes the set *closed*.
        minted = surrogates_in(state.get("user_input", "") or "")
        citations = [{"source": ref} for ref in sorted(r for r in minted if r.startswith("src:"))]

        emit_trace_event(
            "citation_anchor_retrieve.complete", {"anchor_count": len(minted), "citation_count": len(citations)}, state
        )
        return {
            "citations": json.dumps(citations, ensure_ascii=False),
            "evidence_index": json.dumps(sorted(minted), ensure_ascii=False),
            "status": AgentStatus.SUCCESS.value,
        }
