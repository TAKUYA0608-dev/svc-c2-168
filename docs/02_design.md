# SVC-C2-168 — Template Design Specification (Stage ② Design)

> **Template**: `SVC-C2-168` — Managed-Service Catalogue Change Evidence Handover Summarizer
> **Category**: Cat 2 (multi-step domain workflow) · **Industry**: SVC
> **Agent class**: `CatalogueChangeHandoverSummarizerAgent`
> **Source of Truth**: the internal proposal tracker, JP canonical SoT note #1124140. Where this document and the
> SoT disagree, §9 records the disagreement explicitly — it is never resolved silently.

---

## Position in AgentCore Architecture

- **Agent Class**: `CatalogueChangeHandoverSummarizerAgent`
- **L1 Base**: `AgentBaseGraph` (L1 direct inheritance). `ChatAgent` appears in the SoT §1 `Base`
  field as a **pattern reference only** (Summary-node backbone); nothing inherits from it.
- **Cat 2 composition**: the fixed 5-slot outer backbone, with all domain complexity behind a
  `GraphNode` in the `main` slot wrapping an inner `BaseGraph`.
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)
- **Read-only, advisory-only**: the agent does not update ITSM, publish a catalogue, route a ticket,
  change an SLA, assign staff or notify a customer, and it **infers neither approval outcome nor
  routing rule**. The deliverable is a cited, needs-review handover for a named accountable
  catalogue owner.

---

## 1. Purpose and scope

Given a **completed, approved service-catalogue change packet** (old/new catalogue extracts, owner
acknowledgement records, effective-date statements, change-notice / annex excerpts) and a versioned
handover template, produce a `ManagedServiceCatalogueChangeHandover`: the changes the packet
**states**, each carried with its taxonomy qualifier and cited evidence span, plus contradictions,
evidence gaps and reviewer questions.

**In scope** (SoT §4): source-anchored organisation of stated changes; contradiction and gap
surfacing; reviewer questions for the accountable owner.

**Out of scope** (SoT §4, enforced at S-3 by the determination gate in §5): approval outcome,
routing rules, SLA adequacy, billing consequences, catalogue publication, ticket routing.

---

## 2. Architecture Overview

### Data Flow

```
START → initialize → pre_process → main (GraphNode) → post_process → finalize → END
                        │              │                    │
                     S-1 + S-2      inner workflow        S-3 + S-4
                     Steps 1–2        Steps 3–6            Step 7
```

| SoT §4 step | Where it runs | Layer |
|---|---|---|
| 1 `CatalogueChangePacketIngest` | `PreProcessNode` | S-1 structural validation (required fields, NFKC, size cap) |
| 2 `CommercialAndPersonalDataMinimise` | `PreProcessNode` | S-2 pre-LLM minimisation (+ pre-LLM containment, a separate layer) |
| 3 `CatalogueDeltaReconcile` | `CatalogueDeltaReconcileNode` | deterministic — the Tool-equivalent core |
| 4 `ChangeStatementInterpret` | `ChangeStatementInterpretNode` | **LLM-essential** — bounded interpretation into the closed taxonomy |
| 5 `CitationAnchorRetrieve` | `CitationAnchorRetrieveNode` | deterministic — mints the closed evidence index |
| 6 `HandoverSummaryCompose` | `HandoverSummaryComposeNode` | deterministic composition of the fixed template |
| 7 `OutputSanitise` | `PostProcessNode` | S-3 output gate (four fail-closed checks) + S-4 no-persist |

### Node Configuration

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | platform default | — | — | `InitializeNode` (default) |
| pre_process | S-1 validation + S-2 minimisation + containment | `user_input` | `validated_input`, `input_format` | `FunctionNode` |
| main | drives the inner workflow | `validated_input` | `handover`, `citations`, `evidence_index`, counters | `GraphNode` |
| post_process | S-3 output gate + S-4 no-persist | `handover`, `evidence_index` | `formatted_output`, `disclaimer` | `FunctionNode` |
| finalize | platform default | — | — | `FinalizeNode` (default) |

All three domain slots and the four inner nodes declare
`required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL`.

### Inner workflow nodes

| Node | Responsibility |
|---|---|
| `CatalogueDeltaReconcileNode` | offering-reference join, field-level diff, template-field presence, acknowledgement / effective-date presence, definition-note comparison. Never guesses: a non-match is flagged, not repaired. |
| `ChangeStatementInterpretNode` | assigns `change_kind` + `qualifier` from the **closed** taxonomy, with the cited span. Free text is quoted data, never instructions. |
| `CitationAnchorRetrieveNode` | mints the closed `evidence_index` of `src:<sha8>` anchors and the citation list. Retrieval only — never invents a reference. |
| `HandoverSummaryComposeNode` | fills the fixed handover template. Deterministic: no narrative is model-authored. |

**The `GraphNode` lives in `src/graph/graph.py`, never under `src/nodes/`.** PB-6 instantiates every
class it discovers under `src/nodes/` and calls it with a bare state; a `GraphNode` resolves an
`InvocationContext` from that state, so one placed there fails the boundary proof.

**The inner graph is driven by the framework, not by hand.** `GraphNode.execute()` is not overridden
and there is no `run_linear()`-style helper: calling `execute()` directly would bypass
`BaseNode.__call__`, and with it the S-1 trust gate, the S-2/S-3 hooks and the lifecycle events — so
the inner nodes' declared trust level would not be enforced on the path production takes.
`tests/integration/test_inner_node_boundary.py` asserts the traversal, not the output, because the
published envelope is identical either way.

### Topology: linear with per-node skip guards

`add_conditional_edges` does not branch reliably when a graph is driven through a `GraphNode`, so the
inner chain is linear and each node carries `if state.get("error_code"): … return {}`. Each skip
still emits its S-4 event — a degraded run has no unaudited step.

### State Definition

`src/schemas/state.py`. Flat `TypedDict`; every declared field is a primitive or a JSON string.

| Field group | Fields | Type |
|---|---|---|
| S-1 / S-2 output | `validated_input`, `input_format` | JSON string / str |
| inner workflow | `delta_record`, `stated_changes`, `acknowledgements`, `effective_dates`, `contradictions`, `evidence_gaps`, `reviewer_questions`, `citations`, `evidence_index`, `handover` | JSON strings |
| counters / flags | `change_count`, `gap_count`, `human_review_required`, `interpretation_mode`, `citation_complete`, `qualifier_complete`, `error_code` | primitives |
| output | `formatted_output`, `disclaimer`, `audit_logged` | primitives |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON strings). No Pydantic, no dataclass, no `datetime`.
- No JWT, API key or credential in state (checkpoint DB leakage).
- **No commercial amount and no customer/staff identifier in state** — S-2 removes both before
  anything is written (§3).
- `enriched_context` is **not** re-declared: it is the platform's `dict` field, and narrowing an
  inherited platform field would put this template out of contract with the framework that owns it.

### LLM wiring (Step 4)

The client arrives as `config["llm"]`, is handed to the `GraphNode` by `Graph.register_nodes()`, and
is forwarded into the inner workflow through `_parent_config()`. Only `ChangeStatementInterpretNode`
uses it, in **one batched call** for the whole packet. With no client configured — or on any client
failure or unusable response — the step degrades to the seeded rule-based path and **declares it**:
`interpretation_mode = "deterministic_fallback"` travels into the published envelope, so "it quietly
ran deterministic in production" is not a reachable state.

---

## 3. Commercial-data model (SoT §11 risks 1 and 9)

The packet can carry unit prices, discount rates and contract values. This template's rule is
stronger than "do not send them to the model":

1. **Amounts are reduced at S-2, before anything is written to state.** `derive_price_change` pairs
   the old and new extract rows by offering reference, computes a **direction**
   (`increase` / `decrease` / `unchanged`) and a **coarse band** (`+10〜20%` …), and then the raw
   fields are dropped. The band is a ratio, not a value: it cannot be inverted to an amount.
2. **Monetary literals are redacted from every free-text field** at the same point, so a change
   notice reading 「月額を 12,000円 → 14,000円 に改定」 reaches the model, and the output, with the
   amounts already replaced.
3. **S-3 re-checks the rendered envelope** for monetary literals and withholds if any is found
   (`AMOUNT_LEAKED`). This is a real gate, not a comment: removing (1) or (2) makes it fire.

Consequence: **no amount reaches the LLM, state, the audit trail or the output.** The default
handover carries `price_change_ref` (a cited span anchor) plus direction and band, exactly as SoT
addendum A ② specifies. The privileged disclosure path of SoT addendum A ③ is **not implemented** —
see §9-2.

---

## 4. Grounding model — what a citation asserts

`CITATION_BASIS = "caller_declared_authorized_source"`, carried in **every** envelope, on both the
published and the withheld path.

A `source` is citable when, and only when, it is `<namespace>:<reference>` where the namespace names
an **authorised system of record** (`src/services/service.py::AUTHORIZED_CATALOGUE_SYSTEMS`) and the
reference part is non-empty. It is then hashed to `src:<sha8>`.

- **No format passthrough.** A caller-supplied `src:1a2b3c4d` has namespace `src`, which is not an
  authorised system, so it resolves to `None`. Provenance is resolved **exactly once**, at S-1, so
  the surrogates seen downstream are always internally minted.
- **A bare namespace is rejected.** `catalogue_extract` names a system but no record.
- **What it does not assert**: that the record exists. The check is on the *label*. A fabricated
  reference under an authorised namespace (`annex:invented-v3`) **will** produce a citation. That
  limit is asserted by `test_declared_provenance_is_not_verification` and declared in the envelope
  rather than papered over. Authority and entailment remain the catalogue owner's judgement; S-3
  machine-checks **presence** only.

---

## 5. Framework Utilization / Security gates

| Layer | Where | What |
|---|---|---|
| **S-1** | `PreProcessNode.execute` | Required-field validation, NFKC normalisation, 400 000-char cap. Injection detection is deliberately **not** attributed to S-1. |
| **S-2** | `PreProcessNode._minimise` (before any LLM call) | Customer/company names and staff contact details **dropped**; join keys **tokenised** to `<kind>:<sha8>`; commercial amounts **reduced to direction+band and dropped**; credentials and monetary literals redacted. |
| **pre-LLM containment** | `PreProcessNode._minimise` — *a separate layer from S-2* | Free text is quoted data. An instruction-shaped **value** is quarantined and the rest of the packet is still processed. Whole-request rejection applies only to an unstructured body, where the request itself is the instruction. |
| **S-3** | `PostProcessNode.execute` | Four fail-closed checks, all re-derived from the envelope rather than trusted from upstream — see below. |
| **S-4** | `src/utils/audit.py` | `emit_trace_event` on every `execute()` path, skip guards included. Counts, rule versions and error codes only — never a packet body, an identifier or a rejected value. |
| **S-5** | CI `gate-credential-scan` | No credential in `src/`. |

**S-3, the four checks** (any one fails → `status_kind = needs_review`, body withheld, `SUCCESS` +
`error_code` — never `ERROR`, which would skip `post_process` itself):

1. **Citation completeness** — every statement's `cited_source_refs` ⊆ the citation set. Uncited is
   admissible only where the absence *is* the claim and the entry declares
   `evidence_basis = absent_from_supplied_record`.
2. **Qualifier completeness** — a change whose kind is anything other than
   `change_stated_confirmed` must carry a non-empty qualifier **and** a cited span; a "no change"
   assertion must carry a definition-comparison reference. This is the SoT's over-statement gate.
3. **No fabricated surrogate** — every `<kind>:<sha8>` in the rendered envelope must be in the
   closed index this run minted.
4. **No determination language and no amount leak** — the output may not state that a change is
   approved / rejected / compliant / correctly routed, and may not contain a monetary literal.

The determination check scans **agent-authored** text only. Quoted caller evidence lives in
`evidence_span.quote` and is exempt, because quoting a claim is not asserting it and suppressing it
would hide from the reviewer the very thing they must validate. That exemption is only safe because
**the caller cannot choose that key**: `OUTPUT_ONLY_FIELDS` drops any caller-supplied
`quote` / `qualifier` / `change_kind` / `ack_kind` / `evidence_span` / `status_kind` at S-2, and
`tests/unit/test_service.py` fixes that property.

---

## 6. Degradation model

| Condition | Result |
|---|---|
| Empty body | `SUCCESS` + `INPUT_REJECTED` → `out_of_scope` envelope |
| Body over the size cap | `SUCCESS` + `INPUT_TOO_LONG` → `out_of_scope` |
| Unstructured body carrying an instruction marker | `SUCCESS` + `INJECTION_REJECTED` → `out_of_scope` |
| Free-text question | `input_format = "free_text"` → `out_of_scope` safe answer |
| Structured packet, no stated change and no gap (0-hit) | `out_of_scope` safe answer (SoT §4 note) |
| Any S-3 check fails | `needs_review`, body withheld, cited-statement content not published |

`ERROR` is never returned from a domain path.

---

## 7. Import Isolation Confirmation

`src/` imports `framework.*` only — no `agenticstar`, no `mediator`, no sibling template.
`emit_trace_event` comes from `shared.utils.audit_logger` through the shim in `src/utils/audit.py`,
which falls back to a structured stderr line under a local SDK stub and **never raises** (an exception in
an audit call would become `node_error` and fail PB-6).

---

## 8. Design Decision Record

| # | Decision | Reason |
|---|---|---|
| D-1 | Steps 1–2 in `pre_process`, 3–6 inner, 7 in `post_process` | S-2 must precede every LLM call, and S-3 must be the last thing before the caller sees the envelope. |
| D-2 | Only Step 4 uses the LLM | The composition step must not author the narrative — the SoT requires qualifier and citation to be carried structurally, and prices to be transcribed deterministically. |
| D-3 | `status_kind = "catalogue_change_handover"` for the published envelope | The SoT writes `status_kind=needs-review` to mean *the deliverable is a human-review packet*. Reusing that string for the published envelope would make it indistinguishable from an S-3 withholding. Published envelopes therefore carry the explicit kind plus `human_review: {required: true}`; `needs_review` is reserved for withheld. |
| D-4 | Amounts reduced at S-2 rather than carried in a deterministic channel | See §3 and §9-2. It removes the egress surface the SoT's own risk 9 describes. |
| D-5 | `_subgraph` cached on the class, keyed on the identity of the injected client, with a capacity ceiling | Compiling the inner graph is expensive; a plain cache would let an agent configured *with* a client be served a subgraph built without one. |

---

## 9. ★ Deviations from the SoT (declared, not silent)

### 9-1. SoT §12-B-4 — trusted ingress attestation is **not implemented**

SoT addendum B-1 requires a citation to be `authorized ∧ attested`, with attestation read from
`input_context`. **This template implements the `authorized` half only**, deliberately.

- `input_context` is **caller metadata** (the SDK state-schemas reference:
  `input_context | dict | invoke() | Caller metadata`), and the shipped `src/api/server.py` calls
  `agent.invoke(req.input, ctx=ctx)` — it never passes it.
- Two sibling templates gated citations on that field. Because nothing
  populates it on the deployed path, **every valid payload degraded to `needs_review` with the body
  withheld** — the agents produced no usable output. Their tests were green only because they
  invoked with a kwarg the deployment never sends.
- Trading a weak claim for no output is not an improvement. So this template **does not claim to
  verify what it cannot verify**: the envelope always carries
  `citation_basis = "caller_declared_authorized_source"`, and `CITATION_BASIS` in
  `src/services/service.py` states what that does and does not assert — including *why* forwarding a
  caller-supplied attestation would be meaningless (the caller also controls the `source` under
  test).

What is still enforced: the forged-surrogate defence (`src:<hex>` → `None`), the bare-namespace
rejection, and single-point resolution at S-1.

The limit is **fixed by a test**: `test_declared_provenance_is_not_verification` asserts that a
fabricated reference under an authorised namespace *does* produce a citation. If that ever ceases to
be true the test fails and this section must be updated. Real verification needs a server-side
lookup or gateway-signed references delivered outside the caller's body — **a platform dependency,
tracked at SoT §12-B, not something to simulate here.**

`tests/integration/test_end_to_end.py::TestDeployedPath` invokes exactly the way `server.py` does and
asserts that a valid packet returns a body, so the failure mode above cannot reappear unnoticed.

### 9-2. SoT addendum A ③ — the privileged amount-disclosure block is **not implemented**

Addendum A ③ would disclose real amounts to an *authorised recipient*, with the authorisation read
from platform-supplied `input_context`. That gate has the same defect as 9-1: the metadata does not
reach the agent, so the block would be withheld on every real invocation while the code implied a
recipient check existed.

Applying the same rule — *if it cannot be verified, do not claim to verify it* — this template never
discloses amounts at all (§3). Addendum A ④'s fail-closed behaviour is therefore the **only**
behaviour, and the body is never damaged by it.

**Why this is behaviour-preserving rather than a scope reduction.** The authorisation the block
depends on arrives only through `input_context`, which the deployed `src/api/server.py` never passes
(§9-1, measured on two sibling TEL templates). Implementing addendum A ③ exactly as written would
therefore produce a block that is withheld on **every** real invocation — observationally identical
to not implementing it, but shipping a dead path that implies a recipient check exists. The default
handover this template does emit — `price_change_ref` + direction + band — is what addendum A ②
specifies, unchanged.

**This is nonetheless a capability the SoT promised to authorised recipients, and it is not the
engineer's to drop.** The decision belongs with PM/CoE and is recorded as such, not settled here:

| Option | What it requires |
|---|---|
| (a) Restore the capability | The platform supplies a recipient-authorisation signal a caller cannot set from the request body — **SoT §12-B-5**, owner: PM/CoE. Amounts would also have to be retained past S-2, which this design deliberately does not do |
| (b) Drop the capability | PM/CoE record that direction + band is the final contract, and addendum A ③ is struck from the SoT |

Until one is chosen, the implemented behaviour is (b) **in effect but not in record**. Flagged rather
than presented as settled — an approval on the proposal is not approval to change what the proposal said.

---

## 10. Open items (Stage ③ / Stage ④)

- Seeded conditional / provisional / annex vocabularies and the band boundaries are CoE-calibratable
  defaults (SoT §12-A); they are not authoritative lists, and the judgement always resolves to a
  cited span plus a reviewer question.
- The G1 (internal hold-out) comparison harness of SoT addendum D is a Stage ③ deliverable and does
  not gate this design.
- SoT §12-B-1 remains with PM/CoE.
