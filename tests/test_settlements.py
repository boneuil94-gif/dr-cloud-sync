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
    db.executemany("INSERT INTO sale_payments VALUES(?,?,?,?,?,?,?)",[("card","sale","ref-1","CB","98",None,None),("cash","sale","cash","CASH","20",None,None)])
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
