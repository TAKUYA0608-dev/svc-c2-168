"""The LLM the Agent classification rests on is reached **through the graph**, not just constructible.

Sibling a sibling template was failed on 2026-08-05 for shipping this step deterministic-only: SoT §2 makes
the Agent-vs-Tool verdict conditional on the bounded interpretation being LLM-backed, so a client
that is never reached is the same defect wearing a different shape.

The existing node tests construct `ChangeStatementInterpretNode(client)` directly, which proves the
node and not the wiring — measured: removing `llm` from the inner graph's construction of that node
failed **no** test. These drive the real `Graph(config={"llm": ...}).invoke()`, so the forwarding
chain `Graph → GraphNode._parent_config() → inner graph → node` is what is under test.
"""

import json

import pytest

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph

from tests.integration import test_end_to_end as e2e


class _RecordingClient:
    """Records what it was shown; replies with whatever the test scripted."""

    def __init__(self, reply=None):
        self.reply = reply if reply is not None else []
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def _ctx():
    return InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, caller_id="t")


def _payload(**over):
    packet = e2e._packet(**over)
    return packet if isinstance(packet, str) else json.dumps(packet, ensure_ascii=False)


def _run(client=None, payload=None):
    config = {"llm": client} if client is not None else None
    graph = Graph(config=config) if config else Graph()
    return graph.invoke(payload or _payload(), ctx=_ctx(), session_id="s")


def _env(out):
    return json.loads(out["output"])


class TestTheSeamIsReached:
    def test_the_configured_client_is_actually_called(self):
        client = _RecordingClient()
        _run(client)
        assert client.prompts, (
            "the configured client was never called — the forwarding chain is broken, and every "
            "run would silently take the deterministic path while claiming to be LLM-backed")

    def test_without_a_client_the_fallback_is_declared(self):
        assert _env(_run())["interpretation_mode"] == "deterministic_fallback"


class TestTheModelNeverSeesCommercialValues:
    """SoT addendum A ① — real amounts, discount rates and customer names never enter the prompt."""

    def test_no_real_amount_reaches_the_prompt(self):
        client = _RecordingClient()
        _run(client)
        assert client.prompts, "client not reached — the assertion below would be vacuous"
        shown = "\n".join(client.prompts)
        for amount in ("12,000", "14,000", "12000", "14000"):
            assert amount not in shown, f"a real amount ({amount}) reached the model"


class TestTheReplyIsConstrainedNotTrusted:
    @pytest.mark.parametrize("reply", [
        "not json at all",
        [{"index": 0, "kind": "price_approved_by_the_model", "qualifier": "unconditional"}],
    ])
    def test_an_unusable_reply_degrades_rather_than_publishing_it(self, reply):
        out = _run(_RecordingClient(reply))
        body = json.dumps(_env(out), ensure_ascii=False)
        assert "price_approved_by_the_model" not in body
        assert out["status"] != "error"

    def test_a_failing_client_degrades_rather_than_crashing(self):
        class _Broken:
            def complete(self, prompt):
                raise RuntimeError("upstream unavailable")

        out = _run(_Broken())
        assert out["status"] != "error"
        assert _env(out)["interpretation_mode"] == "deterministic_fallback"


class TestTheDefinitionRowQuotesItsOwnRecord:
    """Marketplace re-test, 2026-09-28: the definition row carried the price sentence as its quote.

    The change notice is about the price only; the definition change is found by comparing the
    catalogue rows, and the row's span cites the new extract. A model reply that quotes the (masked)
    price sentence for that row is verbatim in the notice, so only this binding keeps the quote in the
    record it cites. Through the real graph, the same way the Marketplace runs it.
    """

    PRICE = "監視オプション (OFR-0412) の月額を [REDACTED-AMOUNT] → [REDACTED-AMOUNT] に改定する。"
    DEFERRED = "改定は各契約の次回更新月より適用し、現行契約期間中は従前の金額を据え置く。"

    def _rows(self, definition_quote):
        client = _RecordingClient({"items": [
            {"id": 0, "change_kind": "change_conditional_effective", "qualifier": self.DEFERRED},
            {"id": 1, "change_kind": "definition_changed_label_unchanged", "qualifier": definition_quote},
        ]})
        env = _env(_run(client))
        assert env["interpretation_mode"] == "llm", "the scripted reply was not used - the test would be vacuous"
        return {r["change_field"]: r for r in env["stated_changes"]}

    def test_a_notice_sentence_is_not_the_definition_rows_quote(self):
        rows = self._rows(self.PRICE)
        definition = rows["definition_note"]
        assert "[REDACTED-AMOUNT]" not in definition["qualifier"]
        assert definition["evidence_span"]["quote"] == definition["qualifier"]
        assert "受付 = 監視アラート検知時刻" in definition["qualifier"]
        assert "受付 = サービスデスクでのチケット起票時刻" in definition["qualifier"]
        # the price row still carries the model's (verbatim, notice-cited) qualifier
        assert rows["price"]["qualifier"] == self.DEFERRED

    def test_an_empty_quote_does_not_withhold_the_handover(self):
        rows = self._rows("")
        assert "受付 = サービスデスクでのチケット起票時刻" in rows["definition_note"]["qualifier"]
