"""SVC-C2-168 — agent state (ADR-005: flat TypedDict, msgpack-serializable only).

**Every field this template declares carries its payload as a primitive or a JSON string**, never as
a nested structure: LangGraph checkpoints use msgpack serialization, and a bare ``list[dict]`` in
state has been raised in review on sibling templates.

The one nested field in the effective state is ``enriched_context``, and this template does not
declare it: it is **inherited from** ``AgentState`` and **platform-defined as** ``dict``
(SDK state-schema docs: *"``enriched_context`` | ``dict`` | ``pre_process`` node"*).
Re-declaring it — even narrowed to a JSON string — would put this template out of contract with the
framework that owns the field, so ``pre_process`` simply writes the two short strings the platform
intends it for.

``tests/unit/test_state_contract.py`` enforces both halves: declared fields must be
primitive/JSON-string, and the inherited nested field must be exactly the platform-defined one — so
deleting a declaration is not a way to escape the rule.

**What never reaches state.** No credential, no customer/company name, no staff contact detail, and
**no commercial amount** (unit price, discount rate, contract value). S-2 reduces a price change to a
direction and a coarse band *before* anything is written here — see
``src/services/service.py::derive_price_change`` — so there is no field on this state from which an
amount could be recovered, and the S-4 audit carries counts and rule references only.
"""

from typing import NotRequired

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Managed-service catalogue change evidence handover state.

    Shared fields (``user_input``, ``status``, ``session_id``, ``node_history``, ``error_log``,
    ``input_context``, …) are inherited from ``AgentState``.
    """

    # ── S-1 / S-2 (pre_process — SoT §4 Steps 1–2) ───────────────────────────
    validated_input: NotRequired[str]  # type: ignore[valid-type] # JSON: minimised catalogue change packet
    input_format: NotRequired[str]  # type: ignore[valid-type] # "json" | "empty" | "free_text" | "rejected"
    # `enriched_context` is inherited from AgentState (platform-defined `dict`), not declared here.
    # pre_process writes {source, channel} into it — never the packet body.

    # ── inner workflow (SoT §4 Steps 3–6) ────────────────────────────────────
    delta_record: NotRequired[str]  # type: ignore[valid-type] # JSON: deterministic reconcile output (Step 3)
    stated_changes: NotRequired[str]  # type: ignore[valid-type] # JSON: [{offering_ref, change_field, change_kind, …}]
    acknowledgements: NotRequired[str]  # type: ignore[valid-type] # JSON: [{owner_ref, ack_kind, evidence_span}]
    effective_dates: NotRequired[str]  # type: ignore[valid-type] # JSON: [{date_ref, scope_qualifier, evidence_span}]
    contradictions: NotRequired[str]  # type: ignore[valid-type] # JSON: [{kind, cited_source_refs}]
    evidence_gaps: NotRequired[str]  # type: ignore[valid-type] # JSON: [{gap_kind, cited_source_ref, evidence_basis}]
    reviewer_questions: NotRequired[str]  # type: ignore[valid-type] # JSON: [str]
    citations: NotRequired[str]  # type: ignore[valid-type] # JSON: [{ref_id, source}]
    evidence_index: NotRequired[str]  # type: ignore[valid-type] # JSON: the closed surrogate set this run minted
    handover: NotRequired[str]  # type: ignore[valid-type] # JSON: composed ManagedServiceCatalogueChangeHandover

    # ── counters / flags (primitives only) ───────────────────────────────────
    change_count: NotRequired[int]  # type: ignore[valid-type]
    gap_count: NotRequired[int]  # type: ignore[valid-type]
    human_review_required: NotRequired[bool]  # type: ignore[valid-type]
    interpretation_mode: NotRequired[str]  # type: ignore[valid-type] # "llm" | "deterministic_fallback"
    citation_complete: NotRequired[bool]  # type: ignore[valid-type]
    qualifier_complete: NotRequired[bool]  # type: ignore[valid-type]
    error_code: NotRequired[str]  # type: ignore[valid-type]

    # ── output (post_process — SoT §4 Step 7) ────────────────────────────────
    formatted_output: NotRequired[str]  # type: ignore[valid-type]
    disclaimer: NotRequired[str]  # type: ignore[valid-type]
    audit_logged: NotRequired[bool]  # type: ignore[valid-type]
