"""A model-proposed change_kind must agree with the deterministic signal it labels."""

from src.services.service import accept_interpretation

NOTICE = (
    "10月より監視オプションの月額を改定します。改定は各契約の次回更新月より適用し、現行契約期間中は従前の金額を据え置く。"
    "受付の定義をサービスデスクでのチケット起票時刻に変更します。"
)
ITEMS = [
    {"offering_ref": "OFR-0412", "change_field": "price", "change_signal": "value_differs"},
    {
        "offering_ref": "OFR-0412",
        "change_field": "definition_note",
        "change_signal": "label_unchanged_definition_differs",
        "old_definition": "受付 = 監視アラート検知時刻",
        "new_definition": "受付 = サービスデスクでのチケット起票時刻",
    },
]
ROWS_QUOTE = "指標名・閾値は不変、定義文が変更: 「受付 = 監視アラート検知時刻」 → 「受付 = サービスデスクでのチケット起票時刻」"


def test_transposed_kinds_are_rejected():
    raw = {
        "items": [
            {"id": 0, "change_kind": "definition_changed_label_unchanged", "qualifier": ""},
            {
                "id": 1,
                "change_kind": "change_conditional_effective",
                "qualifier": "改定は各契約の次回更新月より適用し、現行契約期間中は従前の金額を据え置く。",
            },
        ]
    }
    accepted, rejected = accept_interpretation(raw, ITEMS, NOTICE)
    assert accepted == {} and rejected == 2


def test_consistent_kinds_are_accepted():
    raw = {
        "items": [
            {
                "id": 0,
                "change_kind": "change_conditional_effective",
                "qualifier": "改定は各契約の次回更新月より適用し、現行契約期間中は従前の金額を据え置く。",
            },
            {"id": 1, "change_kind": "definition_changed_label_unchanged", "qualifier": ""},
        ]
    }
    accepted, rejected = accept_interpretation(raw, ITEMS, NOTICE)
    assert (
        rejected == 0
        and accepted[0][0] == "change_conditional_effective"
        and accepted[1] == ("definition_changed_label_unchanged", ROWS_QUOTE)
    )


def test_the_definition_row_never_quotes_a_notice_sentence():
    """Found on the Marketplace, 2026-09-28: the model gave the definition row the price sentence.

    That sentence is verbatim in the notice, so the verbatim check alone let it through — and the
    row's span cites the new catalogue extract, which does not contain it. The row's quote is the
    rows' own old -> new definition whatever the model quoted, including an empty quote.
    """
    price = "10月より監視オプションの月額を改定します。"
    for quoted in (price, ""):
        raw = {"items": [{"id": 1, "change_kind": "definition_changed_label_unchanged", "qualifier": quoted}]}
        accepted, rejected = accept_interpretation(raw, ITEMS, NOTICE)
        assert rejected == 0 and accepted[1] == ("definition_changed_label_unchanged", ROWS_QUOTE), quoted
