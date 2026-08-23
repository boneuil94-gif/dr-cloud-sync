import json
import sqlite3

from dr_cloud_sync.finance_group_composition_gap import group_composition_gap_funnel


def _db(path):
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE sumup_payouts(
      payout_id TEXT PRIMARY KEY, amount TEXT, currency TEXT, reference TEXT,
      type TEXT, deductions_json TEXT
    );
    CREATE TABLE bank_transactions(
      transaction_id TEXT PRIMARY KEY, provider TEXT, direction TEXT, status TEXT,
      amount TEXT, currency TEXT, reference TEXT
    );
    """)
    return db


def test_classifies_only_remaining_exact_group_gaps_without_emitting_values(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    db = _db(path)
    with db:
        # Remaining group: valid multi-row SumUp group, unique Qonto candidate,
        # but exact group sum does not equal the bank amount.
        db.executemany(
            "INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?)",
            [
                ("p1", "80", "EUR", "ref-a", "PAYOUT", "[]"),
                ("p2", "-5", "EUR", "ref-a", "PAYOUT_DEDUCTION", json.dumps([{"kind": "withheld"}])),
                # Already explained by exact group sum: must be excluded.
                ("p3", "60", "EUR", "ref-b", "PAYOUT", "[]"),
                ("p4", "40", "EUR", "ref-b", "PAYOUT", "[]"),
            ],
        )
        db.executemany(
            "INSERT INTO bank_transactions VALUES(?,?,?,?,?,?,?)",
            [
                ("b1", "qonto", "CREDIT", "COMPLETED", "100", "EUR", "ref-a"),
                ("b2", "qonto", "CREDIT", "COMPLETED", "100", "EUR", "ref-b"),
            ],
        )
    db.close()

    evidence = group_composition_gap_funnel(path)
    counts = evidence["counts"]
    assert evidence["status"] == "MEASURABLE"
    assert counts["remaining_multi_record_groups_total"] == 1
    assert counts["remaining_payout_rows_total"] == 2
    assert counts["remaining_groups_with_payout_type"] == 1
    assert counts["remaining_groups_with_payout_deduction_type"] == 1
    assert counts["remaining_groups_with_nonempty_deductions_json"] == 1
    assert counts["remaining_groups_with_empty_deductions_json"] == 1
    assert counts["remaining_payout_rows_type_payout"] == 1
    assert counts["remaining_payout_rows_type_payout_deduction"] == 1
    assert evidence["provider_exhaustiveness_inferred"] is False
    assert evidence["safety"] == {
        "database_read_only": True,
        "provider_network_calls": False,
        "mutations": False,
        "reference_values_emitted": False,
        "row_level_identifiers_emitted": False,
        "monetary_values_emitted": False,
        "deduction_values_emitted": False,
        "free_form_provider_data_emitted": False,
    }
    rendered = json.dumps(evidence)
    assert "ref-a" not in rendered and "withheld" not in rendered
    assert "100" not in rendered and "80" not in rendered


def test_unknown_types_are_bounded_and_deduction_json_shape_is_not_exposed(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    db = _db(path)
    with db:
        db.executemany(
            "INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?)",
            [
                ("p1", "40", "EUR", "r", "provider-new-kind", "not-json"),
                ("p2", "40", "EUR", "r", None, "{}"),
            ],
        )
        db.execute(
            "INSERT INTO bank_transactions VALUES(?,?,?,?,?,?,?)",
            ("b", "qonto", "CREDIT", "COMPLETED", "90", "EUR", "r"),
        )
    db.close()

    evidence = group_composition_gap_funnel(path)
    counts = evidence["counts"]
    assert counts["remaining_multi_record_groups_total"] == 1
    assert counts["remaining_groups_with_other_type"] == 1
    assert counts["remaining_groups_with_missing_type"] == 1
    assert counts["remaining_groups_with_invalid_deductions_json"] == 1
    assert counts["remaining_payout_rows_type_other"] == 1
    assert counts["remaining_payout_rows_type_missing"] == 1
    rendered = json.dumps(evidence)
    assert "provider-new-kind" not in rendered and "not-json" not in rendered


def test_blank_or_missing_deduction_payload_is_invalid_not_empty(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    db = _db(path)
    with db:
        db.executemany(
            "INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?)",
            [
                ("p1", "30", "EUR", "r", "PAYOUT", None),
                ("p2", "30", "EUR", "r", "PAYOUT_DEDUCTION", "   "),
            ],
        )
        db.execute(
            "INSERT INTO bank_transactions VALUES(?,?,?,?,?,?,?)",
            ("b", "qonto", "CREDIT", "COMPLETED", "70", "EUR", "r"),
        )
    db.close()

    counts = group_composition_gap_funnel(path)["counts"]
    assert counts["remaining_multi_record_groups_total"] == 1
    assert counts["remaining_groups_with_invalid_deductions_json"] == 1
    assert counts["remaining_groups_with_empty_deductions_json"] == 0


def test_fail_closed_for_invalid_amount_or_nonunique_bank_candidate(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    db = _db(path)
    with db:
        db.executemany(
            "INSERT INTO sumup_payouts VALUES(?,?,?,?,?,?)",
            [
                ("p1", "sNaN", "EUR", "bad", "PAYOUT", "[]"),
                ("p2", "10", "EUR", "bad", "PAYOUT", "[]"),
                ("p3", "20", "EUR", "multi", "PAYOUT", "[]"),
                ("p4", "20", "EUR", "multi", "PAYOUT", "[]"),
            ],
        )
        db.executemany(
            "INSERT INTO bank_transactions VALUES(?,?,?,?,?,?,?)",
            [
                ("b1", "qonto", "CREDIT", "COMPLETED", "50", "EUR", "bad"),
                ("b2", "qonto", "CREDIT", "COMPLETED", "50", "EUR", "multi"),
                ("b3", "qonto", "CREDIT", "COMPLETED", "60", "EUR", "multi"),
            ],
        )
    db.close()

    evidence = group_composition_gap_funnel(path)
    assert evidence["counts"]["remaining_multi_record_groups_total"] == 0


def test_missing_ledger_is_unmeasurable(tmp_path):
    evidence = group_composition_gap_funnel(tmp_path / "missing.sqlite3")
    assert evidence["status"] == "UNMEASURABLE"
    assert evidence["reason"] == "REQUIRED_LEDGER_MISSING"
    assert evidence["counts"] is None
