from datetime import datetime, timezone
import sqlite3

from dr_cloud_sync.sales_ingestion import OPERATIONAL_SCHEMA
from dr_cloud_sync.settlements import PaymentSettlementService, match_payment, match_payout
from dr_cloud_sync.sumup import SCHEMA as SUMUP_SCHEMA


def payment(**values):
    return {"payment_id":"p1","external_payment_id":"ref-1","amount":"98","currency":"EUR",
            "occurred_at":"2026-08-03T10:00:00Z", **values}


def transaction(**values):
    return {"sumup_transaction_id":"tx1","transaction_code":"ref-1","client_transaction_id":None,
            "foreign_transaction_id":None,"reference":None,"amount":"98","currency":"EUR",
            "timestamp":"2026-08-03T10:01:00Z","status":"SUCCESSFUL", **values}


def database():
    db=sqlite3.connect(":memory:");db.row_factory=sqlite3.Row
    db.executescript(OPERATIONAL_SCHEMA);db.executescript(SUMUP_SCHEMA)
    return db


def test_exact_reference_and_unique_amount_time_are_deterministic():
    exact=match_payment(payment(),[transaction()])
    assert (exact["status"],exact["match_method"],exact["confidence"]) == ("MATCHED","EXACT_REFERENCE","1")
    unique=match_payment(payment(external_payment_id="other"),[transaction(transaction_code="none")])
    assert (unique["status"],unique["match_method"]) == ("MATCHED","AMOUNT_CURRENCY_TIME_UNIQUE")


def test_ambiguous_amount_and_failed_reference_are_conflicts():
    candidates=[transaction(),transaction(sumup_transaction_id="tx2",transaction_code="ref-2")]
    result=match_payment(payment(external_payment_id="none"),candidates)
    assert result["status"] == "CONFLICT" and result["evidence"]["candidate_count"] == 2
    failed=match_payment(payment(),[transaction(status="FAILED")])
    assert failed["status"] == "CONFLICT" and "NON_FINAL" in failed["match_method"]


def test_mixed_ticket_payments_are_matched_separately_and_idempotently():
    db=database();stamp=datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO sales VALUES(?,?,?,?,?,?,?,?,?,?,?)",("sale","SHOPCAISSE","ticket",stamp,"UTC","STORE","Paris","EUR","PAID",stamp,stamp))
    db.executemany("INSERT INTO sale_payments(payment_id,sale_id,external_payment_id,payment_type,amount,name,description) VALUES(?,?,?,?,?,?,?)",[("card","sale","ref-1","CB","98",None,None),("cash","sale","cash","CASH","20",None,None)])
    row=transaction(); values=(row["sumup_transaction_id"],row["transaction_code"],row["amount"],row["currency"],row["timestamp"],row["status"],row["status"],None,None,None,None,None,"0","0","0","0",None,None,None,None,"1.7","[]","{}",stamp)
    db.execute("INSERT INTO sumup_transactions VALUES("+",".join("?"*24)+")",values)
    service=PaymentSettlementService(db)
    assert service.recompute()["matched"] == 1
    assert service.recompute()["matched"] == 1
    assert len(service.matches()) == 1
    assert service.summary()["revenue_included"] is False


def test_payout_balance_fee_refund_chargeback_adjustment_and_unavailable():
    db=database();stamp=datetime.now(timezone.utc).isoformat();service=PaymentSettlementService(db)
    payout=("pay",None,stamp,"85","EUR","2","PAID",None,None,None,None,"[]","{}",stamp)
    db.execute("INSERT INTO sumup_payouts VALUES("+",".join("?"*14)+")",payout)
    for i,kind,amount in ((1,"TRANSACTION","100"),(2,"REFUND","10"),(3,"CHARGEBACK","5"),(4,"ADJUSTMENT","2")):
        db.execute("INSERT INTO sumup_payout_items VALUES(?,?,?,?,?,?,?,?,?)",(str(i),"pay",None,None,kind,amount,"EUR",stamp,"{}"))
    assert service.payout("pay")["balance_status"] == "BALANCED"
    db.execute("INSERT INTO sumup_payouts VALUES("+",".join("?"*14)+")",("empty",None,stamp,"1","EUR","0","PAID",None,None,None,None,"[]","{}",stamp))
    assert service.payout("empty")["composition"] == "UNAVAILABLE"


def test_payout_to_qonto_requires_reference_or_unique_sumup_counterparty():
    payout={"payout_id":"pay-1","reference":"bank-ref","amount":"96.30","currency":"EUR","payout_date":"2026-08-03T10:00:00Z","paid_date":None}
    credit={"transaction_id":"bank-1","booked_at":"2026-08-04T10:00:00Z","amount":"96.30","currency":"EUR","direction":"CREDIT","counterparty":"SumUp Payments","label":"Settlement","reference":None}
    assert match_payout(payout,[credit])["status"] == "MATCHED"
    assert match_payout(payout,[{**credit,"counterparty":"Other"}])["status"] == "UNMATCHED"
    assert match_payout(payout,[credit,{**credit,"transaction_id":"bank-2"}])["status"] == "CONFLICT"
    exact=match_payout(payout,[{**credit,"counterparty":"Other","reference":"bank-ref"}])
    assert (exact["status"],exact["match_method"]) == ("MATCHED","EXACT_BANK_REFERENCE")


def test_empty_summary_never_returns_nan_and_marks_unknown_bank():
    result = PaymentSettlementService(database()).summary()
    assert result["coverage_percent"] is None
    assert result["qonto_credits"] is None
    assert "NaN" not in str(result)


def test_cash_cockpit_keeps_unconfigured_qonto_as_one_global_fact():
    db=database(); stamp="2026-08-03T10:00:00+00:00"
    db.execute("INSERT INTO sales VALUES(?,?,?,?,?,?,?,?,?,?,?)",("sale","SHOPCAISSE","ticket",stamp,"UTC","STORE","Paris","EUR","PAID",stamp,stamp))
    db.execute("INSERT INTO sale_payments(payment_id,sale_id,external_payment_id,payment_type,amount,name,description) VALUES(?,?,?,?,?,?,?)",("card","sale","ref-1","CB","98",None,None))
    row=transaction(); values=(row["sumup_transaction_id"],row["transaction_code"],row["amount"],row["currency"],row["timestamp"],row["status"],row["status"],None,None,None,None,None,"0","0","0","0",None,None,None,None,"1.70","[]","{}",stamp)
    db.execute("INSERT INTO sumup_transactions VALUES("+",".join("?"*24)+")",values)
    db.execute("INSERT INTO sumup_payouts VALUES("+",".join("?"*14)+")",("pay",None,stamp,"96.30","EUR","1.70","PAID",None,None,None,None,"[]","{}",stamp))
    service=PaymentSettlementService(db); service.recompute(); result=service.summary()
    assert result["cash_summary"]["card_declared"] == "98"
    assert result["cash_summary"]["fees"] == "1.7"
    assert result["cash_summary"]["paid"] is None
    assert result["configuration_alert"]["count"] == 1
    assert result["active_anomalies"] == 0
    payout_link=[x for x in service.matches() if x["source_type"]=="SUMUP_PAYOUT"][0]
    assert (payout_link["status"],payout_link["match_method"]) == ("NOT_EVALUATED","WAITING_FOR_BANK_SOURCE")


def test_naive_payment_time_is_rejected_and_backfill_preview_has_common_period():
    assert match_payment(payment(occurred_at="2026-08-03T10:00:00"),[transaction()])["match_method"]=="INVALID_PAYMENT_TIME"
    preview=PaymentSettlementService(database()).backfill_preview()
    assert preview["card_candidates"]==0 and preview["intersection_start"] is None


def test_settlement_ui_contract_has_central_formatters_and_responsive_views():
    from pathlib import Path
    root = Path(__file__).parents[1] / "src" / "dr_cloud_sync" / "static"
    script = (root / "settlements.js").read_text(encoding="utf-8")
    markup = (root / "settlements.html").read_text(encoding="utf-8")
    styles = (root / "inventory.css").read_text(encoding="utf-8")
    for helper in ("formatCount", "formatMoney", "formatPercent", "formatFreshness", "formatStatus"):
        assert f"function {helper}" in script
    assert "Number.isFinite" in script
    assert 'aria-live="polite"' in markup
    assert 'data-panel="payouts"' in markup and 'data-panel="transactions"' in markup
    assert "@media(max-width:360px)" in styles
    assert ".settlement-mobile" in styles and ".settlement-drawer" in styles


def test_explorer_server_search_pagination_evidence_timeline_and_secret_redaction():
    db=database(); service=PaymentSettlementService(db); stamp=datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO payment_settlement_links VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("settlement:explorer","SHOPCAISSE_PAYMENT","ticket 98","SUMUP_TRANSACTION","TX-CODE","POSSIBLE","0.82","AMOUNT_TIME_UNIQUE","98","98","0","EUR",74,'{"reference_equal":false,"token":"never"}',stamp,stamp,None,None,"key-explorer"))
    db.execute("INSERT INTO payment_settlement_evidence VALUES(?,?,?,?,?)",("e1","settlement:explorer","MATCH_SIGNALS",'{"candidate_count":1,"authorization":"never"}',stamp))
    service.note("settlement:explorer","Appeler le magasin", "manager")
    assert service.explorer({"q":" TICKET   98 ","limit":"1"})["pagination"]["total"] == 1
    assert service.explorer({"q":"appeler le magasin"})["items"][0]["settlement_id"] == "settlement:explorer"
    evidence=service.evidence("settlement:explorer")
    assert evidence["time_difference_seconds"] == 74 and "authorization" not in str(evidence).lower()
    assert any(x["type"]=="INTERNAL_NOTE" for x in service.timeline("settlement:explorer")["items"])


def test_explorer_static_mobile_and_safe_display_contracts():
    from pathlib import Path
    root=Path(__file__).parents[1]/"src"/"dr_cloud_sync"/"static"
    markup=(root/"settlement-explorer.html").read_text(); script=(root/"settlement-explorer.js").read_text(); styles=(root/"inventory.css").read_text()
    assert 'aria-live="polite"' in markup and "Settlement Explorer" in markup
    assert "AbortController" in script and "history.replaceState" in script and "Number.isFinite" in script
    assert "@media(max-width:375px)" in styles and "@media(max-width:768px)" in styles
