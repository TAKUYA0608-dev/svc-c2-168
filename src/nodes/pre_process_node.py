"""SVC-C2-168 — pre_process: SoT §4 Step 1 (CatalogueChangePacketIngest, S-1) + Step 2 (S-2).

Three layers run here, deliberately kept distinct — the SoT is explicit that they are not the same
thing, and conflating them is what lets a change-notice body act as an instruction:

1. **S-1 — structural validation.** Required fields, NFKC normalisation, size cap. Injection
   detection is *not* attributed to S-1.
2. **S-2 — commercial and personal data minimisation (pre-LLM).** Customer/company names and staff
   contact details are dropped, join keys are tokenised, and **commercial amounts are reduced to a
   direction and a coarse band and then discarded** — all before any LLM call, so no downstream node
   ever sees them. This is minimisation, not containment.
3. **pre-LLM containment (a separate layer).** Change-notice bodies, acknowledgement notes and annex
   remarks are treated as *quoted data*: an instruction-shaped value is quarantined and never acted
   on. Independent of S-2.

**Provenance is resolved exactly once, here.** Downstream nodes receive citations, never raw labels,
so a caller value shaped like an internal surrogate can never re-enter the pipeline.

Degraded paths return ``SUCCESS + error_code`` and discard the offending body — never ``ERROR``,
which would skip ``post_process`` (S-3/S-4) in the production framework.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import (
    ID_TOKENISE_FIELDS,
    OUTPUT_ONLY_FIELDS,
    PII_DROP_FIELDS,
    PRICE_FIELDS,
    PROVENANCE_FIELDS,
    contains_injection_marker,
    derive_price_change,
    nfkc,
    opaque_id,
    redact,
    resolve_provenance,
)
from src.utils.audit import emit_trace_event

_MAX_INPUT = 400_000
_QUARANTINED = "[QUARANTINED — instruction-shaped content, not acted on]"


def _minimise(obj: Any) -> Any:
    """Recursively drop identity and commercial values, tokenise join keys, resolve provenance.

    Values of unknown fields are kept (an MSP packet legitimately carries site-specific fields this
    template cannot enumerate) but are still redacted. What *is* enumerated is what must never
    survive (``PII_DROP_FIELDS``, ``PRICE_FIELDS``, ``OUTPUT_ONLY_FIELDS``) and what must always be
    tokenised (``ID_TOKENISE_FIELDS``).
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            low = key.lower()
            if low in PII_DROP_FIELDS or low in PRICE_FIELDS or low in OUTPUT_ONLY_FIELDS:
                # Dropped, not masked — the value never enters state. Amounts have already been
                # reduced to direction+band by `_price_changes` before this runs, and the
                # output-only keys are dropped so a caller cannot pre-fill a field that the S-3
                # determination check exempts as quoted evidence.
                continue
            if low in ID_TOKENISE_FIELDS:
                text = str(value).strip() if value is not None else ""
                out[key] = opaque_id(text, ID_TOKENISE_FIELDS[low]) if text else None
                continue
            if low in PROVENANCE_FIELDS:
                # Single resolution point. Unauthorised / bare / forged surrogate → None → S-3 blocks.
                out[PROVENANCE_FIELDS[low]] = resolve_provenance(value)
                continue
            out[key] = _minimise(value)
        return out
    if isinstance(obj, list):
        return [_minimise(v) for v in obj]
    if isinstance(obj, str):
        text = redact(nfkc(obj))
        # pre-LLM containment: quote, never obey. The fact that something instruction-shaped was
        # recorded is retained; its content is not carried forward.
        return _QUARANTINED if contains_injection_marker(text) else text
    return obj


def _price_changes(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Reduce every old→new price pair to direction + band **before** minimisation drops the values.

    Running on the raw packet is the point: the amounts exist only inside this function's frame. The
    offering reference is tokenised with the same rule the rows use, so the result joins downstream
    without any raw identifier surviving either.
    """
    old = {r.get("offering_ref"): r for r in raw.get("old_extract") or [] if isinstance(r, dict)}
    changes = []
    for row in raw.get("new_extract") or []:
        if not isinstance(row, dict):
            continue
        counterpart = old.get(row.get("offering_ref"))
        if counterpart is None:
            continue
        derived = derive_price_change(counterpart, row)
        if derived:
            changes.append({"offering_ref": opaque_id(row.get("offering_ref"), "ofr"), **derived})
    return changes


class PreProcessNode(FunctionNode):
    """S-1 structural validation + S-2 minimisation + pre-LLM containment."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """S-2 hook. **Must return state and must never raise** (SDK 1.0.0).

        Raising here, or returning ``None``, sets state to ``None`` in the production framework and
        crashes every downstream node. Rejection is surfaced through ``error_code`` in ``execute``.
        """
        return dict(state)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw = state.get("user_input", "") or ""
        input_context = state.get("input_context", {}) or {}  # read-only caller metadata
        enriched = {
            "source": "CatalogueChangeHandoverSummarizerAgent",
            "channel": input_context.get("channel", "unknown"),
        }

        if len(raw) > _MAX_INPUT:
            emit_trace_event("catalogue_change_packet_ingest.rejected", {"reason": "INPUT_TOO_LONG"}, state)
            return self._rejected("rejected", "INPUT_TOO_LONG", enriched)

        if not raw.strip():
            emit_trace_event("catalogue_change_packet_ingest.rejected", {"reason": "INPUT_REJECTED"}, state)
            return self._rejected("empty", "INPUT_REJECTED", enriched)

        packet, fmt = self._parse(nfkc(raw))

        # Whole-request rejection applies to an *instruction surface* only — a body that is not a
        # structured packet, so the request itself is the instruction. It must NOT apply to a
        # structured packet: a legitimate change notice can carry an instruction-shaped string in a
        # body or a remark, and rejecting the packet for that discards real evidence.
        # Instruction-shaped content inside a packet is handled one layer down by `_minimise`, which
        # quarantines the offending value and keeps the rest.
        if fmt == "free_text" and contains_injection_marker(raw[:2000]):
            emit_trace_event("catalogue_change_packet_ingest.rejected", {"reason": "INJECTION_REJECTED"}, state)
            return self._rejected("rejected", "INJECTION_REJECTED", enriched)

        body = json.dumps(packet, ensure_ascii=False)
        emit_trace_event(
            "catalogue_change_packet_ingest.validated",
            {
                "input_format": fmt,
                "old_row_count": len(packet.get("old_extract", [])),
                "new_row_count": len(packet.get("new_extract", [])),
                "acknowledgement_count": len(packet.get("acknowledgements", [])),
                "price_change_count": len(packet.get("price_changes", [])),
                # How many values were quarantined as instruction-shaped. Recording the count keeps the
                # fact auditable without carrying the content forward.
                "quarantined_values": body.count(_QUARANTINED),
            },
            state,
        )
        return {
            "validated_input": body,
            "input_format": fmt,
            "enriched_context": enriched,
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _rejected(fmt: str, error_code: str, enriched: dict[str, Any]) -> dict[str, Any]:
        out = {
            "validated_input": "{}",
            "input_format": fmt,
            "enriched_context": enriched,
            "error_code": error_code,
            "status": AgentStatus.SUCCESS.value,
        }
        if fmt == "rejected":
            out["user_input"] = ""  # the offending body is discarded, not carried forward
        return out

    @staticmethod
    def _parse(text: str) -> tuple[dict[str, Any], str]:
        empty: dict[str, Any] = {
            "old_extract": [],
            "new_extract": [],
            "acknowledgements": [],
            "effective_date_statements": [],
            "annexes": [],
            "change_notice": {},
            "price_changes": [],
            "required_template_fields": [],
        }
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return empty, "free_text"  # natural-language question → out-of-scope downstream
        if not isinstance(obj, dict):
            return empty, "free_text"

        # ★ Order matters: the price reduction reads the raw amounts, `_minimise` then removes them.
        prices = _price_changes(obj)
        minimised = _minimise(obj)
        notice = minimised.get("change_notice") or {}
        return {
            "packet_id": minimised.get("packet_id"),
            "packet_version_ref": minimised.get("packet_version_ref"),
            "handover_template_version": minimised.get("handover_template_version"),
            "catalogue_baseline_refs": minimised.get("catalogue_baseline_refs") or {},
            "old_extract": minimised.get("old_extract") or [],
            "new_extract": minimised.get("new_extract") or [],
            "change_notice": notice if isinstance(notice, dict) else {},
            "acknowledgements": minimised.get("acknowledgements") or [],
            "effective_date_statements": minimised.get("effective_date_statements") or [],
            "annexes": minimised.get("annexes") or [],
            "required_template_fields": [str(f) for f in (minimised.get("required_template_fields") or [])],
            "price_changes": prices,
        }, "json"
