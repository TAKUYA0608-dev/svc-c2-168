# Test Specification — SVC-C2-168

## Test Strategy
- Coverage target: **90%** (achieved: **93%** on `src/`)
- Test types: Unit (`tests/unit/`) / Integration (`tests/integration/`) / Proof-of-boundary
  (`tests/proof_of_boundary/`)
- **Integration tests invoke exactly the way `src/api/server.py` does**:
  `Graph().invoke(user_input_str, ctx=ctx)` — a **string** first argument, `ctx` **keyword-passed**,
  and **no `input_context`**. Passing `ctx` positionally lands it in `session_id`, the caller stays
  `ANONYMOUS`, S-1 refuses every node, and the run returns a bare `status=error` that reads like a
  template bug. Passing `input_context` is worse: it is caller metadata the deployment never sends,
  so a suite that supplies it can stay green while production returns nothing (docs/02 §9-1).
- Several tests assert a **negative through a positive first**. "The marker is absent" is satisfied
  by wholesale rejection as well as by quarantine, so the containment tests assert the packet was
  *processed* before asserting the marker is gone.

### Local vs CI

Three tests fail under a local SDK stub and pass against the real SDK in CI — a known environment
difference, not a defect, and deliberately not "fixed":
`tests/proof_of_boundary/test_pb_invoke_order.py::test_call_order_for_every_node`,
`tests/unit/test_framework_compliance_tc06_tc07.py::test_tc06_…`, `::test_tc07_…`.
**CI is authoritative for the test count.**

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | ✅ `test_state_contract.py` — machine-checked in both directions: declared fields must be primitive/JSON-string, and the effective state may nest only where the *platform* does |
| TC-02 | SecurityViolationError fires on invalid input | Error raised | ✅ framework-owned; the template degrades rather than raising (`SUCCESS + error_code`) |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations | ✅ no credential in `src/`; `redact()` strips `sk-` / `AKIA` / JWT shapes from any rendered string |
| TC-04 | InvocationContext via configurable only | Direct access raises error | ✅ never stored in state; the GraphNode resolves it through the framework |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start` / `node_complete` / `node_error` absent from `execute()` | ✅ 0 duplicates — only domain events are emitted |
| TC-06 | S-2: `_security_gate_input()` not overridden | `TypeError` at class definition if overridden | ✅ 0 overrides (fails locally under a local SDK stub — see above) |
| TC-07 | S-3: `_security_gate_output()` not overridden | `TypeError` at class definition if overridden | ✅ 0 overrides (fails locally under a local SDK stub — see above) |
| TC-08 | `required_trust_level` enforced | Insufficient trust → refused | ✅ all 7 nodes declare `VERIFIED_EXTERNAL`; `scripts/check_trust_level.py` gates it |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial | Domain input checks execute | ✅ the hook returns state and never raises (SDK 1.0.0); the domain work is `_minimise` in `execute` — drop / tokenise / reduce / redact / quarantine |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial | Domain output checks execute | ✅ preservation check on the rendered envelope (`citation_basis` must survive); MAY raise |
| TC-11 | S-4: ≥1 domain `emit_trace_event()` inside each `execute()` | Domain event on every path | ✅ `test_audit_coverage.py` — an AST scan of **every return in every `execute()`**, plus a behavioural half proving the scan describes the real runtime |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures | ✅ `test_audit_coverage.py` (static + behavioural) |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass | ✅ `test_state_safety.py` + `test_state_contract.py` |
| PB-3 | Template → External service | Real external service connection | Data retrieved | ✅ N/A by design — read-only, no external I/O. The LLM client is the only external dependency, injected through `config["llm"]`; its absence is a declared state (`interpretation_mode`) |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations | ✅ `test_import_isolation.py` |
| PB-5 | Checkpoint safety | No JWT/Pydantic in checkpoint | Inspection pass | ✅ every declared field is a primitive or a JSON string |
| PB-6 | Invoke execution order | S-1 → `node_start` → S-2 → `execute()` → S-3 → `node_complete` | Order verified | ✅ `test_pb_invoke_order.py` (CI) — the GraphNode lives in `src/graph/`, never `src/nodes/`, so this gate stays satisfiable |
| PB-7 | HITL interrupt propagation *(conditional)* | Only when `hitl.enabled: true` | — | **Auto-waived — non-HITL.** HITL is not enabled; the handover is resolved out of band, not through an in-graph interrupt |

### PB-8 (template-specific) — the inner graph crosses the framework boundary

`tests/integration/test_inner_node_boundary.py` spies on `BaseNode.__call__` and asserts **every**
inner node passes through it, with the expected set read from the inner graph rather than hardcoded.
A hand-rolled linear driver would bypass the S-1 trust gate, the S-2/S-3 hooks and the lifecycle
events, and the **published envelope would be byte-identical** — so this asserts traversal, not
output, because no outer-path test can catch it.

## Business Logic Tests

The four statements a field-level diff gets wrong (SoT §2-4) are each tested at the invoke path, in
`tests/integration/test_end_to_end.py::TestTheFourSoTCases`.

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | A conditional price change is not reported as unconditional | Notice: 「改定は各契約の次回更新月より適用し、現行契約期間中は従前の金額を据え置く」 | `change_conditional_effective`; the qualifier retains 「次回更新月」; cited span present; `price_band` `+10〜20%` | ✅ |
| BL-02 | A verbal acknowledgement is neither "approved" nor "missing" | Ack note 「口頭で了承済。承認書面は次回 CAB 後に取得予定」, `signed: false` | `owner_acknowledgement_provisional`, with the note quoted | ✅ |
| BL-03 | Delegation to an annex is not a missing change item | Notice: 「変更点は別紙2「カタログ差分表」のとおり」 | `change_indirect_reference`; the qualifier carries the annex version 「第3版」 | ✅ |
| BL-04 | A changed definition under an unchanged label is surfaced | Old 「受付 = 監視アラート検知時刻」 → new 「受付 = サービスデスクでのチケット起票時刻」, label and threshold identical | `definition_changed_label_unchanged`; `no_change_statements` empty | ✅ |
| BL-05 | A cosmetic rewrite is **not** a definition change | 「受付 = 検知時刻」 → 「受付=検知時刻。」 | no finding — the normalised comparison ignores spacing and punctuation | ✅ |
| BL-06 | "No change" is stated only with the comparison behind it | Identical rows, both carrying a definition note | `no_change_statements` populated with `definition_comparison_refs`; with no notes, no claim is made | ✅ |
| BL-07 | Effective dates split by scope; same-scope conflicts raised | Publication date + contract-application date; then two publication dates | two scopes = no conflict; two dates in one scope = `effective_date_conflict` with its qualifier | ✅ |
| BL-08 | 0-hit answers out of scope rather than inventing a finding | Packet with no rows, notice, acknowledgement or date | `status_kind: out_of_scope` | ✅ |
| BL-09 | Commercial amounts never reach the caller | 12,000 → 14,000 and 「割引率 15%」 in the notice | no amount and no discount rate anywhere in the envelope; `+10〜20%` still published | ✅ |
| BL-10 | Customer and contact details never reach the caller | `customer_name`, `contact` in the packet | dropped, not masked | ✅ |
| BL-11 | The declared limit of a citation is fixed as a statement | `annex:invented-v3-does-not-exist` | a citation **is** produced (`test_declared_provenance_is_not_verification`); the day this changes, docs/02 §9-1 must change with it | ✅ |
| BL-12 | Forged surrogate / bare namespace / unauthorised namespace are refused | `src:1a2b3c4d`, `catalogue_extract`, `random_wiki:page-1` | withheld (`needs_review`), no statement published | ✅ |
| BL-13 | Instruction-shaped content in a packet is quarantined, not cause for rejection | Marker inside `change_notice.body` | packet processed, statements present, marker gone | ✅ |
| BL-14 | An unstructured body carrying a marker **is** rejected | Marker within the first 2000 chars of free text | `INJECTION_REJECTED` + `out_of_scope`; a marker past the screened prefix is not | ✅ |
| BL-15 | The model cannot introduce text that was not in the evidence | Model returns a qualifier absent from the notice | refused; the deterministic classification stands; `interpretation_mode: deterministic_fallback` | ✅ |
| BL-16 | Every S-3 gate bites | Mutations of the composed envelope | `CITATION_INCOMPLETE` / `QUALIFIER_MISSING` / `QUALIFIER_UNSOURCED` / `UNVERIFIED_ANCHOR` / `DETERMINATION_ASSERTED` / `UNBACKED_NO_CHANGE` / `AMOUNT_LEAKED` each reproduced | ✅ `test_s3_node_boundary.py::TestTheGatesActuallyBite` |
| BL-17 | Output stays inside the stated boundary | Full packet | no 承認済 / 未承認 / 却下 / approved / rejected / 請求対象 in the envelope; reviewer questions state no outcome | ✅ |

> **On BL-16.** Writing it found a real defect: the amount check ran on the *redacted* string, so
> redaction defused it and the gate was dead code. It now runs on the composed envelope, before
> redaction — an amount reaching that point means the S-2 reduction failed upstream, which is a
> defect to surface rather than one to quietly rewrite.
>
> **On BL-04/BL-05.** The `NO_CHANGE_ASSERTION` pattern needs word boundaries: without them it
> matches the taxonomy key `definition_changed_label_unchanged` inside a summary line, so the finding
> that exists to *report* a hidden definition change was itself read as asserting "no change" and
> every such handover was withheld. `test_service.py::test_the_taxonomy_key_is_not_read_as_a_no_change_claim`
> holds that shut.

## Framework input contract (AgentCore 1.0.1, a platform issue)

> Recorded 2026-08-26 as part of the AgentCore 1.0.1 S-2 migration. This is a transcription of a
> framework contract into this template's spec — not a weakening of any test.

- **Ruling** (platform, 2026-08-24): a high-confidence S-2 injection finding makes the framework
  itself refuse the invoke (`status=error`, no output) inside `InitializeNode`, before any template
  node runs. This is **correct behaviour** — the gate firing as designed is a pass, not a failed
  invoke. Templates cannot override it (`__init_subclass__` rejects it).
- **Residual constraint this template inherits**: a legitimate business packet that merely *quotes*
  a high-confidence phrase (e.g. inside `change_notice.body`) is refused wholesale at invoke time.
  The template's quarantine layer cannot act on what it never receives. This is a framework
  *input-contract* constraint on callers, fixed executably by
  `test_end_to_end.py::TestFrameworkRefusalIsTheContract` via `assert_framework_refused()`
  (status=error + no output + no echo; skipped under a local stub, where the production gate does not
  exist — CI against the real wheel is authoritative).
- **What the template layer still guarantees** (BL-13/BL-14, unchanged in substance): for
  everything the framework passes, instruction-shaped content inside a structured packet is
  quarantined — never obeyed, never grounds for discarding the packet — and an unstructured body
  carrying a marker in its screened prefix is rejected (`INJECTION_REJECTED`). These properties are
  now proven with a **sub-threshold** marker (framework confidence *medium*, still matched by
  `CONTAINMENT_MARKERS`); the premise is pinned by
  `test_the_probe_marker_still_trips_the_template_detector`.
- The degraded-run audit-coverage check (`test_audit_coverage.py::TestSkipPathBehaviour`) now
  triggers with an empty body (`INPUT_REJECTED`) instead of an injection marker: under 1.0.1 a
  refused invoke runs zero template nodes, so it exercised nothing. The degraded=SUCCESS invariant
  and the "every step accounts for itself" property are unchanged.

## Test Execution Summary
- Execution date: 2026-08-05
- Total tests: **169 local** (163 passed, 3 known local stub env failures, 3 skipped).
  **CI is authoritative** and runs the three environment-sensitive ones against the real SDK.
- Coverage: **93%** (`--cov=src`); `src/api/server.py` is the unmodified scaffold adapter.
