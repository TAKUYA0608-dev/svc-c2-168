"""SVC-C2-168 — inner Step 3: CatalogueDeltaReconcile (deterministic, the Tool-equivalent core).

Everything this node produces is reproducible by a plain reconciler: an offering-reference join, a
field-level diff, a presence check, and a normalised comparison of definition notes. No free text is
read here — that is Step 4's job, and keeping the two apart is the Agent-vs-Tool boundary the SoT
committed to in §2.

The node never repairs a non-match. A row that does not join, a reference that did not resolve and a
missing acknowledgement are all *reported*; none of them is guessed at.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import (
    BASIS_ABSENT,
    acknowledgement_findings,
    effective_date_findings,
    reconcile_delta,
)
from src.utils.audit import emit_trace_event


def _gap(kind: str, cited: Any, basis: str | None) -> dict[str, Any]:
    return {"gap_kind": kind, "cited_source_refs": [r for r in [cited] if r], "evidence_basis": basis}


class CatalogueDeltaReconcileNode(FunctionNode):
    """Join the old/new extracts, check required records are present, compare definition notes."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code"):
            # Skipping is a decision the audit trail has to show. Returning silently left a
            # degraded run with no domain event on this step at all (S-4 gap).
            emit_trace_event("catalogue_delta_reconcile.skipped", {"reason": state.get("error_code")}, state)
            return {}

        packet = json.loads(state.get("user_input", "{}") or "{}")
        delta = reconcile_delta(packet)
        acknowledgements = acknowledgement_findings(packet)
        dates, contradictions = effective_date_findings(packet)
        notice_body = str((packet.get("change_notice") or {}).get("body") or "")

        gaps: list[dict[str, Any]] = []
        if not acknowledgements:
            # The absence IS the claim here, and the packet itself evidences it.
            gaps.append(_gap("owner_acknowledgement_missing", None, BASIS_ABSENT))
        if not dates:
            gaps.append(_gap("effective_date_unstated", None, BASIS_ABSENT))
        # A row whose `source` did not name an authorised system produces no citation. Reporting it
        # explicitly is what turns an S-3 withholding into an actionable message; the withholding
        # itself comes from the *statement* built on that row, which has no reference to bind.
        unresolved = sum(
            1
            for row in packet.get("old_extract", []) + packet.get("new_extract", [])
            if not row.get("cited_source_ref")
        )
        if unresolved:
            gaps.append(_gap("source_ref_missing", None, BASIS_ABSENT))

        if not any(
            (delta["field_changes"], delta["no_change_statements"], acknowledgements, dates, notice_body.strip())
        ):
            # 0-hit (SoT §4 note): nothing in the packet can be read as a stated change, an
            # acknowledgement, a date or a notice. Answer out of scope rather than inventing one.
            emit_trace_event("catalogue_delta_reconcile.out_of_scope", {"reason": "no_catalogue_delta"}, state)
            return {
                "delta_record": "{}",
                "change_count": 0,
                "gap_count": 0,
                "error_code": "NO_CATALOGUE_DELTA",
                "status": AgentStatus.SUCCESS.value,
            }

        emit_trace_event(
            "catalogue_delta_reconcile.complete",
            {
                "offering_count": delta["offering_count"],
                "field_change_count": len(delta["field_changes"]),
                "no_change_count": len(delta["no_change_statements"]),
                "acknowledgement_count": len(acknowledgements),
                "effective_date_count": len(dates),
                "contradiction_count": len(contradictions),
                "gap_count": len(gaps),
                "unresolved_source_rows": unresolved,
            },
            state,
        )
        return {
            "delta_record": json.dumps(delta, ensure_ascii=False),
            "acknowledgements": json.dumps(acknowledgements, ensure_ascii=False),
            "effective_dates": json.dumps(dates, ensure_ascii=False),
            "contradictions": json.dumps(contradictions, ensure_ascii=False),
            "evidence_gaps": json.dumps(gaps, ensure_ascii=False),
            "change_count": len(delta["field_changes"]),
            "gap_count": len(gaps),
            "status": AgentStatus.SUCCESS.value,
        }
