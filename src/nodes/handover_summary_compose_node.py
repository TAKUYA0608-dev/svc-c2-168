"""SVC-C2-168 — inner Step 6: HandoverSummaryCompose.

Deterministic on purpose. The deliverable is a *handover statement*, and the SoT's whole
over-statement argument (§2-3, §11 risk 2) is that a generated narrative silently drops the qualifier
that makes the statement true. So no sentence here is model-authored: every summary line is built
from the taxonomy key, the structural fields and the anchors, and the caller's own words appear only
inside `evidence_span.quote` and `qualifier`, marked as quoted evidence.

`summary_line` is therefore the surface this template *asserts*, and it is exactly what the S-3
determination check scans. It contains no caller free text at all, so a determination appearing there
can only come from a code change or from a path that bypassed this node — which is the case S-3
exists to catch.

The reviewer questions are fixed constants keyed by finding kind (`REVIEWER_QUESTIONS`), phrased as
questions so that none of them states an outcome.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import (
    CITATION_BASIS,
    REVIEWER_QUESTIONS,
    RULE_VERSION,
)
from src.utils.audit import emit_trace_event

PUBLISHED_KIND = "catalogue_change_handover"

_HUMAN_REVIEW_MESSAGE = (
    "本 handover は needs-review の下書きです。各 gap と reviewer question の解決、および公開可否・"
    "施行日の確定は、指名された accountable catalogue owner が行ってください。"
)


def _summary_line(statement: dict[str, Any]) -> str:
    """One agent-authored line per statement — structural fields only, never caller prose."""
    parts = [
        f"offering={statement.get('offering_ref')}",
        f"field={statement.get('change_field')}",
        f"kind={statement.get('change_kind')}",
    ]
    if statement.get("price_direction"):
        parts.append(f"price={statement['price_direction']} ({statement.get('price_band')})")
    refs = ",".join(statement.get("cited_source_refs") or []) or "none"
    parts.append(f"cited={refs}")
    return " / ".join(parts)


class HandoverSummaryComposeNode(FunctionNode):
    """Fill the fixed handover template from the structured findings."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code"):
            emit_trace_event("handover_summary_compose.skipped", {"reason": state.get("error_code")}, state)
            return {}

        packet = json.loads(state.get("user_input", "{}") or "{}")
        statements = json.loads(state.get("stated_changes", "[]") or "[]")
        acknowledgements = json.loads(state.get("acknowledgements", "[]") or "[]")
        dates = json.loads(state.get("effective_dates", "[]") or "[]")
        contradictions = json.loads(state.get("contradictions", "[]") or "[]")
        gaps = json.loads(state.get("evidence_gaps", "[]") or "[]")
        citations = json.loads(state.get("citations", "[]") or "[]")
        delta = json.loads(state.get("delta_record", "{}") or "{}")

        for statement in statements:
            statement["summary_line"] = _summary_line(statement)

        kinds = (
            [s.get("change_kind") for s in statements]
            + [a.get("ack_kind") for a in acknowledgements]
            + [c.get("kind") for c in contradictions]
            + [g.get("gap_kind") for g in gaps]
        )
        questions = [REVIEWER_QUESTIONS[k] for k in dict.fromkeys(kinds) if k in REVIEWER_QUESTIONS]

        handover = {
            "status_kind": PUBLISHED_KIND,
            "packet_id": packet.get("packet_id"),
            "packet_version_ref": packet.get("packet_version_ref"),
            "handover_template_version": packet.get("handover_template_version"),
            "catalogue_baseline_refs": packet.get("catalogue_baseline_refs") or {},
            "rule_version": RULE_VERSION,
            "interpretation_mode": state.get("interpretation_mode"),
            "citation_basis": CITATION_BASIS,
            "stated_changes": statements,
            "owner_acknowledgements": acknowledgements,
            "effective_date_statements": dates,
            "no_change_statements": delta.get("no_change_statements", []),
            "contradictions": contradictions,
            "evidence_gaps": gaps,
            "reviewer_questions": questions,
            "citations": citations,
            "human_review": {"required": True, "status": "pending_owner_review", "message": _HUMAN_REVIEW_MESSAGE},
        }

        emit_trace_event(
            "handover_summary_compose.complete",
            {
                "statement_count": len(statements),
                "acknowledgement_count": len(acknowledgements),
                "effective_date_count": len(dates),
                "contradiction_count": len(contradictions),
                "gap_count": len(gaps),
                "reviewer_question_count": len(questions),
                "rule_version": RULE_VERSION,
            },
            state,
        )
        return {
            "handover": json.dumps(handover, ensure_ascii=False),
            "human_review_required": True,
            "status": AgentStatus.SUCCESS.value,
        }
