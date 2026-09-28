"""SVC-C2-168 — inner Step 4: ChangeStatementInterpret (the LLM-essential step, SoT §2-4).

This is the step the deterministic reconciler cannot do. A catalogue change is *stated* in prose:
「各契約の次回更新月より適用し、現行契約期間中は従前の金額を据え置く」 is a price change with an
application boundary, 「変更点は別紙2「カタログ差分表（第3版）」のとおり」 is a delegation and not a
missing field, and a signed-off-looking acknowledgement note may say the signature is still to come.
A field diff can only report that something differs; it cannot say under what condition, from which
record, or with what still unresolved.

**pre-LLM containment (a layer of its own, independent of S-2).** The notice body arrives as *quoted
data*, the answer schema is restricted to the closed taxonomy, and the returned qualifier must be
present **verbatim** in that quoted text. That last check is the one that matters: it makes it
impossible for the model to introduce a statement that was not in the evidence. Anything outside the
taxonomy, or not found in the quoted text, is refused — and the deterministic classification stands.

**The deterministic path is a declared fallback, not a silent default.** With no client configured,
on any client failure, or on an unusable response, `interpretation_mode` becomes
``deterministic_fallback`` and travels into the published envelope.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import (
    BASIS_CITED_RECORD,
    accept_interpretation,
    annex_delegation,
    build_interpretation_prompt,
    classify_change,
)
from src.utils.audit import emit_trace_event

MODE_LLM = "llm"
MODE_FALLBACK = "deterministic_fallback"

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class ChangeStatementInterpretNode(FunctionNode):
    """Classify each reconciled signal into the closed taxonomy, with its cited qualifier."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm: Any = None) -> None:
        super().__init__()
        self._llm = llm

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code"):
            emit_trace_event("change_statement_interpret.skipped", {"reason": state.get("error_code")}, state)
            return {}

        packet = json.loads(state.get("user_input", "{}") or "{}")
        delta = json.loads(state.get("delta_record", "{}") or "{}")
        notice = packet.get("change_notice") or {}
        notice_body = str(notice.get("body") or "")
        notice_ref = notice.get("cited_source_ref")

        signals: list[dict[str, Any]] = delta.get("field_changes", [])
        accepted, rejected, mode = self._interpret(signals, notice_body, state)

        statements = []
        for index, signal in enumerate(signals):
            kind, qualifier = accepted.get(index) or classify_change(signal, notice_body)
            statements.append(self._statement(signal, kind, qualifier, notice_ref))

        # ★ SoT §2-4 case 3 — delegation to an annex is not an absent change item.
        delegation = annex_delegation(notice_body, packet.get("annexes") or [])
        if delegation:
            statements.append(delegation)

        emit_trace_event(
            "change_statement_interpret.complete",
            {
                "signal_count": len(signals),
                "statement_count": len(statements),
                "interpretation_mode": mode,
                "model_items_rejected": rejected,
                "annex_delegation": bool(delegation),
                "kind_distribution": {
                    k: sum(1 for s in statements if s["change_kind"] == k)
                    for k in {s["change_kind"] for s in statements}
                },
            },
            state,
        )
        return {
            "stated_changes": json.dumps(statements, ensure_ascii=False),
            "interpretation_mode": mode,
            "change_count": len(statements),
            "status": AgentStatus.SUCCESS.value,
        }

    def _interpret(
        self, signals: list[dict[str, Any]], notice_body: str, state: dict[str, Any]
    ) -> tuple[dict[int, tuple[str, str]], int, str]:
        """One batched call for the whole packet, then validate the answer against the evidence."""
        if not signals or not notice_body.strip() or self._llm is None:
            if self._llm is None:
                emit_trace_event(
                    "change_statement_interpret.llm_unavailable_deterministic_fallback",
                    {"reason": "not_configured"},
                    state,
                )
            return {}, 0, MODE_FALLBACK
        try:
            raw = self._llm.complete(build_interpretation_prompt(signals, notice_body))
            block = _JSON_BLOCK.search(str(raw or ""))
            parsed = json.loads(block.group(0)) if block else None
        except Exception as exc:  # noqa: BLE001 - any client failure degrades, never crashes
            emit_trace_event(
                "change_statement_interpret.llm_unavailable_deterministic_fallback",
                {"reason": type(exc).__name__},
                state,
            )
            return {}, 0, MODE_FALLBACK
        if parsed is None:
            emit_trace_event(
                "change_statement_interpret.llm_unavailable_deterministic_fallback",
                {"reason": "unparseable_response"},
                state,
            )
            return {}, 0, MODE_FALLBACK
        accepted, rejected = accept_interpretation(parsed, signals, notice_body)
        if not accepted:
            emit_trace_event(
                "change_statement_interpret.llm_unavailable_deterministic_fallback",
                {"reason": "no_admissible_item"},
                state,
            )
            return {}, rejected, MODE_FALLBACK
        return accepted, rejected, MODE_LLM

    @staticmethod
    def _statement(signal: dict[str, Any], kind: str, qualifier: str, notice_ref: Any) -> dict[str, Any]:
        """Bind the classification to its evidence.

        The span reference is where the *qualifier* came from: the notice for a vocabulary-driven
        kind, the new extract row for the structural definition comparison. Getting that wrong would
        make S-3's qualifier check pass while pointing the reviewer at the wrong record.

        ★ The notice reference is added **only when the rows themselves resolved**. A field diff is
        evidenced by the rows it was computed from; the notice explains the change but does not
        attribute the row-level delta. Letting the notice stand in for absent row provenance would
        publish a price direction derived from extract rows whose `source` named no authorised
        system — the caller could forge those rows and still get a cited statement out.
        """
        row_refs = [r for r in signal.get("cited_source_refs", []) if r]
        structural = signal.get("change_signal") == "label_unchanged_definition_differs"
        grounded = bool(row_refs)
        span_ref = (row_refs[-1] if structural else notice_ref) if grounded else None
        refs = row_refs + ([notice_ref] if (grounded and notice_ref and not structural and qualifier) else [])
        statement = {
            "offering_ref": signal.get("offering_ref"),
            "change_field": signal.get("change_field"),
            "change_kind": kind,
            "qualifier": qualifier,
            "cited_source_refs": [r for r in dict.fromkeys(refs) if r],
            "evidence_basis": BASIS_CITED_RECORD if grounded else None,
            "evidence_span": {"quote": qualifier, "cited_source_ref": span_ref},
        }
        for field in ("price_direction", "price_band", "price_change_ref"):
            if signal.get(field):
                statement[field] = signal[field]
        return statement
