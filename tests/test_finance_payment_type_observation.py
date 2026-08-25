import sqlite3

from dr_cloud_sync.finance_payment_type_observation import upstream_payment_type_observation


def _db(path):
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE sales(sale_id TEXT PRIMARY KEY, source TEXT NOT NULL);
    CREATE TABLE sale_payments(
      payment_id TEXT PRIMARY KEY, sale_id TEXT NOT NULL,
      payment_type TEXT, name TEXT, description TEXT,
      canonical_payment_type TEXT, mapping_rule TEXT, mapping_version TEXT
    );
    """)
    return db


def test_observation_separates_missing_unrecognized_and_recognized_without_raw_values(tmp_path):
    path = tmp_path / "ledger.db"
    db = _db(path)
    db.executemany("INSERT INTO sales VALUES(?,?)", [("s1", "SHOPCAISSE"), ("s2", "PRESTASHOP")])
    rows = [
        ("p1", "s1", "", None, None, "UNKNOWN", "missing", "shopcaisse-payment-types-v2"),
        ("p2", "s1", "Terminal Secret Label", None, None, "UNKNOWN", "unknown-label", "shopcaisse-payment-types-v2"),
        ("p3", "s1", "", "Cash Secret Label", None, "CASH", "exact-normalized:name:cash-secret-label", "shopcaisse-payment-types-v2"),
        ("p4", "s1", "", None, "Card Secret Label", "CARD", "exact-normalized:description:card-secret-label", "shopcaisse-payment-types-v2"),
        ("p5", "s1", "legacy", None, None, "UNKNOWN", "legacy-unmapped", "shopcaisse-payment-types-v1"),
        ("p6", "s2", "visa", None, None, "CARD", "exact-normalized:payment_type:visa", "shopcaisse-payment-types-v2"),
    ]
    db.executemany("INSERT INTO sale_payments VALUES(?,?,?,?,?,?,?,?)", rows)
    db.commit(); db.close()

    result = upstream_payment_type_observation(path)
    assert result["evidence_status"] == "MEASURABLE"
    c = result["counts"]
    assert c["shopcaisse_payments"] == 5
    assert c["canonical_card"] == 1
    assert c["canonical_known_non_card"] == 1
    assert c["canonical_unknown_or_other"] == 3
    assert c["raw_signal_none"] == 1
    assert c["raw_signal_any"] == 4
    assert c["unknown_with_no_raw_signal"] == 1
    assert c["unknown_with_raw_signal"] == 2
    assert c["mapping_rule_missing"] == 1
    assert c["mapping_rule_unknown_label"] == 1
    assert c["mapping_rule_recognized_name"] == 1
    assert c["mapping_rule_recognized_description"] == 1
    assert c["mapping_rule_other_or_legacy"] == 1
    assert c["mapping_version_current"] == 4
    assert c["mapping_version_other_or_legacy"] == 1
    assert c["unknown_current_mapping_version"] == 2
    assert c["unknown_other_or_legacy_mapping_version"] == 1
    text = str(result)
    for secret in ("Terminal Secret Label", "Cash Secret Label", "Card Secret Label", "legacy", "visa"):
        assert secret not in text
    assert result["provider_exhaustiveness_inferred"] is False
    assert result["safety"]["raw_payment_values_emitted"] is False


def test_observation_classifies_recognition_source_without_emitting_token(tmp_path):
    path = tmp_path / "ledger.db"
    db = _db(path)
    db.execute("INSERT INTO sales VALUES('s','SHOPCAISSE')")
    db.execute(
        "INSERT INTO sale_payments VALUES(?,?,?,?,?,?,?,?)",
        ("p", "s", "Very Secret Tender", None, None, "CARD", "exact-normalized:payment_type:very-secret-tender", "shopcaisse-payment-types-v2"),
    )
    db.commit(); db.close()
    result = upstream_payment_type_observation(path)
    assert result["counts"]["mapping_rule_recognized_payment_type"] == 1
    assert "very-secret-tender" not in str(result)


def test_observation_fails_closed_for_missing_ledger_or_schema(tmp_path):
    missing = upstream_payment_type_observation(tmp_path / "missing.db")
    assert missing["evidence_status"] == "UNMEASURABLE"
    assert missing["reason"] == "REQUIRED_LEDGER_MISSING"

    path = tmp_path / "partial.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sales(sale_id TEXT PRIMARY KEY, source TEXT NOT NULL)")
    db.execute("CREATE TABLE sale_payments(sale_id TEXT, canonical_payment_type TEXT)")
    db.commit(); db.close()
    partial = upstream_payment_type_observation(path)
    assert partial["evidence_status"] == "UNMEASURABLE"
    assert partial["reason"] == "REQUIRED_SCHEMA_INCOMPLETE"
