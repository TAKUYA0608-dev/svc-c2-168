"""SVC-C2-168 — post_process: SoT §4 Step 7 (OutputSanitise — S-3 output gate + S-4 no-persist).

**S-3 re-derives; it does not trust the composing step.** A handover assembled by some other path
must not be able to publish, so every check here works from the envelope, the closed evidence index
and the citation set rather than from a flag set upstream.

Four checks, all fail-closed:

1. **Citation completeness** — every statement's references ⊆ the citation set. Uncited is
   admissible only where the absence *is* the claim and the entry declares ``BASIS_ABSENT``.
2. **Qualifier completeness** — the over-statement gate. A conditional / delegated / definition-
   changed / provisional / narrowed / ambiguous finding must carry its qualifier **and** a cited
   span, and a "no change" assertion must carry the definition comparison behind it.
3. **No fabricated surrogate** — every ``<kind>:<sha8>`` in the rendered envelope must be in the
   closed index this run minted.
4. **No determination and no amount** — the output may not state an approval, compliance, routing or
   billing outcome, and may not contain a commercial amount.

Failure is **degraded, not ERROR**: ``SUCCESS + error_code`` with the body withheld, so the
disclaimer and the terminal S-4 audit still run. Withholding never damages what it is protecting —
the cited statements are dropped as a set, not partially rewritten.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.handover_summary_compose_node import PUBLISHED_KIND
from src.services.service import (
    CITATION_BASIS,
    CONTAINMENT_MARKERS,
    RULE_VERSION,
    agent_authored_text,
    asserts_determination,
    citation_binding_failure,
    leaks_amount,
    qualifier_binding_failure,
    redact,
    surrogates_in,
)
from src.utils.audit import emit_trace_event

# ── S-3: the authoritative injection / output-policy boundary ────────────────
#
# Upstream containment is required and is defence in depth, but it must not be *labelled* the
# injection security control and must not *substitute* for the S-3 proof. The caller-visible boundary
# is here: whatever reaches generated output is neutralised at S-3, whichever path produced it —
# including a path that bypassed pre_process entirely.
_NEUTRALISED = "[NEUTRALISED]"
_MARKER_RE = re.compile("|".join(re.escape(m) for m in CONTAINMENT_MARKERS), re.IGNORECASE)

_DISCLAIMER = (
    "本 handover は、提供された完了済みカタログ変更 packet に対する needs-review のドラフトです。"
    "ITSM の更新・カタログの公開・ticket の振替・SLA の変更・人員の割当・顧客への通知は行わず、"
    "**変更の可否・routing rule・料金請求の帰結もいっさい判断しません**。"
    "**S-3 が機械的に保証するのは citation の completeness（各記述に認可済み system of record を"
    "名指しした出典参照が付いていること）と qualifier の completeness だけです。名指しされた"
    "レコードが当該システムに実在するか、記述を裏付けるか（entailment）は本エージェントでは"
    "検証できません**（citation_basis を参照）。最終判断は指名された accountable catalogue owner が"
    "行ってください。"
    "**商用金額は S-2 で方向とレンジに縮約済みで、本 handover には実額を含みません。**"
)

_WITHHELD_MESSAGE = (
    "出典参照または qualifier の完全性が確認できなかったため、根拠不十分な handover の提示を"
    "差し控えました。`<認可済みシステム>:<レコード参照>` 形式の source を各記録に付与のうえ"
    "再実行してください（レコードの実在性は本エージェントでは検証しません）。"
)
_WITHHELD_HUMAN_MESSAGE = (
    "Not every statement carries both a source reference naming an authorized system of record and "
    "the qualifier its classification requires; the handover is withheld pending those declared "
    "references and accountable-owner review. Record existence is not verified by this agent "
    "(see citation_basis)."
)
_OUT_OF_SCOPE_MESSAGE = (
    "カタログ変更 packet として解釈できる入力が確認できませんでした。旧／新カタログ抽出・"
    "変更通知・owner acknowledgement・施行日参照を含む JSON を送信してください。"
)


def _neutralise_injection(text: str) -> tuple[str, int]:
    """Neutralise instruction-shaped artifacts in the rendered envelope. Returns (text, count)."""
    hits = len(_MARKER_RE.findall(text))
    return (_MARKER_RE.sub(_NEUTRALISED, text), hits) if hits else (text, 0)


class PostProcessNode(FunctionNode):
    """S-3 output gate + S-4 no-persist audit."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """S-3 preservation check. Receives the **delta from execute()**; MAY raise (SDK 1.0.0)."""
        out = result.get("formatted_output", "")
        if out and "citation_basis" not in out:
            raise ValueError("S-3: mandatory citation basis missing from output")
        return dict(result)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        handover: dict[str, Any] = json.loads(state.get("handover", "{}") or "{}")

        if handover.get("status_kind") != PUBLISHED_KIND:
            # The path an upstream rejection (injection / oversize / empty / 0-hit) lands on. It
            # publishes an envelope, so it needs its own S-4 event: reporting `audit_logged: True`
            # from a path that emitted nothing is the gap a sibling template was pulled up on.
            emit_trace_event(
                "output_sanitise.out_of_scope",
                {"reason": state.get("error_code") or "not_composed", "rule_version": RULE_VERSION},
                state,
            )
            return self._envelope(
                {
                    "status_kind": "out_of_scope",
                    "stated_changes": [],
                    "citations": [],
                    "citation_basis": CITATION_BASIS,
                    "message": _OUT_OF_SCOPE_MESSAGE,
                },
                state,
                state.get("error_code"),
            )

        cited = {c["source"] for c in handover.get("citations", []) if c.get("source")}
        index = set(json.loads(state.get("evidence_index", "[]") or "[]"))

        # ★ The amount check runs on the **composed** envelope, before redaction — and the ordering
        # is the whole point. Redaction rewrites `14,000円` to a placeholder, so a leak check placed
        # after it can never fire; the gate looked present and was dead code until a mutation test
        # removed the reduction at S-2 and the envelope still published. An amount reaching this
        # point means the S-2 reduction (docs/02 §3) failed upstream, which is a defect to surface,
        # not something to paper over by quietly redacting it.
        composed = json.dumps(handover, ensure_ascii=False)

        # Redact, then neutralise instruction-shaped artifacts in whatever reached generated output —
        # including via a path that never passed pre_process.
        rendered, injected = _neutralise_injection(redact(composed))
        checked = json.loads(rendered)

        reason = citation_binding_failure(checked, cited) or qualifier_binding_failure(checked)
        if reason is None and not surrogates_in(rendered) <= index:
            reason = "UNVERIFIED_ANCHOR"
        if reason is None and any(asserts_determination(t) for t in agent_authored_text(checked)):
            # A determination would exceed the stated boundary — withhold rather than publish it.
            reason = "DETERMINATION_ASSERTED"
        if reason is None and leaks_amount(composed):
            reason = "AMOUNT_LEAKED"

        if reason is not None:
            # The rejected value is deliberately NOT echoed: only the violation kind is reported.
            emit_trace_event(
                "output_sanitise.withheld",
                {
                    "reason": reason,
                    "statement_count": len(handover.get("stated_changes", [])),
                    "injection_artifacts_neutralised": injected,
                    "rule_version": RULE_VERSION,
                },
                state,
            )
            return self._envelope(
                {
                    "status_kind": "needs_review",
                    "packet_id": handover.get("packet_id"),
                    "stated_changes": [],
                    "owner_acknowledgements": [],
                    "effective_date_statements": [],
                    "citations": [],
                    "citation_basis": CITATION_BASIS,
                    "citation_complete": False,
                    "human_review": {
                        "required": True,
                        "status": "pending_owner_review",
                        "message": _WITHHELD_HUMAN_MESSAGE,
                    },
                    "message": _WITHHELD_MESSAGE,
                },
                state,
                reason,
            )

        checked["citation_complete"] = True
        checked["qualifier_complete"] = True
        emit_trace_event(
            "output_sanitise.complete",
            {
                "status_kind": checked["status_kind"],
                "statement_count": len(checked.get("stated_changes", [])),
                "gap_count": len(checked.get("evidence_gaps", [])),
                "contradiction_count": len(checked.get("contradictions", [])),
                "reviewer_question_count": len(checked.get("reviewer_questions", [])),
                "interpretation_mode": checked.get("interpretation_mode"),
                "injection_artifacts_neutralised": injected,
                "rule_version": RULE_VERSION,
            },
            state,
        )
        return self._envelope(checked, state, state.get("error_code"))

    @staticmethod
    def _envelope(body: dict[str, Any], state: dict[str, Any], error_code: str | None) -> dict[str, Any]:
        body["disclaimer"] = _DISCLAIMER
        # Neutralise here as well as on the grounded body, so "no caller-controlled string reaches
        # the caller un-neutralised" is structural rather than a coincidence. The withheld envelope
        # echoes a caller-supplied identifier, and today that identifier cannot carry text only
        # because `packet_id` appears in ID_TOKENISE_FIELDS — a CoE-calibratable constant. Removing
        # it from that list would open the path silently. Idempotent on the grounded path.
        if error_code:
            # Surfaced in the body, not only in the audit event: `invoke()` returns
            # {output, status, trace_id, correlation_id, node_history} and drops every other state
            # field, so an operator who only sees the envelope would otherwise have no machine
            # -readable reason for a withheld or out-of-scope answer. The violation *kind* is
            # reported; the rejected value never is.
            body["error_code"] = error_code
        rendered, _ = _neutralise_injection(json.dumps(body, ensure_ascii=False))
        out = {
            "formatted_output": rendered,
            "disclaimer": _DISCLAIMER,
            "audit_logged": True,
            "citation_complete": bool(body.get("citation_complete")),
            "qualifier_complete": bool(body.get("qualifier_complete")),
            "status": AgentStatus.SUCCESS.value,
        }
        if error_code:
            out["error_code"] = error_code
        return out
