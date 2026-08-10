"""Read-only bank provider port and durable, idempotent Bank Ledger."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import hashlib, json, sqlite3
from pathlib import Path
from typing import Protocol, Sequence

class BankCategory(StrEnum):
    SALES_SETTLEMENT="SALES_SETTLEMENT"; SUPPLIER_PAYMENT="SUPPLIER_PAYMENT"; BANK_FEE="BANK_FEE"; TAX="TAX"; PAYROLL="PAYROLL"; RENT="RENT"; TRANSFER="TRANSFER"; FINANCING="FINANCING"; REFUND="REFUND"; OTHER="OTHER"; UNKNOWN="UNKNOWN"
@dataclass(frozen=True)
class BankAccount: account_id:str; name:str; currency:str
@dataclass(frozen=True)
class BankBalance: account_id:str; current:Decimal; currency:str; observed_at:str; available:Decimal|None=None
@dataclass(frozen=True)
class BankTransaction:
    account_id:str; booked_at:str; amount:Decimal; currency:str; label:str
    external_transaction_id:str|None=None; value_at:str|None=None; counterparty:str|None=None
    reference:str|None=None; status:str="BOOKED"; category:str=BankCategory.UNKNOWN
    raw_metadata:dict|None=None
@dataclass(frozen=True)
class TransactionPage: transactions:Sequence[BankTransaction]; next_cursor:str|None
class BankProviderPort(Protocol):
    @property
    def configured(self)->bool: ...
    def health(self)->dict: ...
    def accounts(self)->Sequence[BankAccount]: ...
    def balances(self)->Sequence[BankBalance]: ...
    def transactions(self,cursor:str|None=None)->TransactionPage: ...
class DisabledQontoProvider:
    """Honest placeholder: Qonto is unavailable until documented credentials/client exist."""
    configured=False
    def health(self): return {"status":"NOT_CONFIGURED","reason":"Qonto read-only API credentials and validated API contract are missing"}
    def accounts(self): return ()
    def balances(self): return ()
    def transactions(self,cursor=None): return TransactionPage((),cursor)

SCHEMA="""
CREATE TABLE IF NOT EXISTS bank_transactions(transaction_id TEXT PRIMARY KEY,source TEXT NOT NULL,provider TEXT NOT NULL,external_transaction_id TEXT,account_id TEXT NOT NULL,booked_at TEXT NOT NULL,value_at TEXT,amount TEXT NOT NULL,currency TEXT NOT NULL,direction TEXT NOT NULL,label TEXT NOT NULL,counterparty TEXT,reference TEXT,status TEXT NOT NULL,category TEXT NOT NULL,category_state TEXT NOT NULL,raw_metadata_json TEXT NOT NULL,imported_at TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE);
CREATE UNIQUE INDEX IF NOT EXISTS ux_bank_external ON bank_transactions(provider,external_transaction_id) WHERE external_transaction_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS bank_accounts(account_id TEXT NOT NULL,provider TEXT NOT NULL,name TEXT NOT NULL,currency TEXT NOT NULL,imported_at TEXT NOT NULL,PRIMARY KEY(account_id,provider));
CREATE TABLE IF NOT EXISTS bank_balances(account_id TEXT NOT NULL,provider TEXT NOT NULL,current_balance TEXT NOT NULL,available_balance TEXT,currency TEXT NOT NULL,observed_at TEXT NOT NULL,imported_at TEXT NOT NULL,PRIMARY KEY(account_id,provider));
"""
def now(): return datetime.now(timezone.utc).isoformat()
class BankLedger:
    def __init__(self,path:Path):
        self.path=Path(path);self.db=sqlite3.connect(path,check_same_thread=False);self.db.row_factory=sqlite3.Row;self.db.executescript(SCHEMA);self.db.commit()
    @staticmethod
    def fingerprint(provider,t:BankTransaction):
        stable=t.external_transaction_id or "|".join((t.account_id,t.booked_at,str(t.amount),t.currency,t.reference or "",t.label))
        return hashlib.sha256(f"{provider}|{stable}".encode()).hexdigest()
    def import_page(self,provider:str,page:TransactionPage):
        inserted=0
        with self.db:
            for t in page.transactions:
                key=self.fingerprint(provider,t); direction="CREDIT" if t.amount>=0 else "DEBIT"
                metadata={k:v for k,v in (t.raw_metadata or {}).items() if not any(x in k.lower() for x in ("token","secret","password","authorization","api_key"))}
                values=(f"bank:{key}","BANK",provider,t.external_transaction_id,t.account_id,t.booked_at,t.value_at,str(t.amount),t.currency,direction,t.label,t.counterparty,t.reference,t.status,str(t.category),"PROPOSED",json.dumps(metadata),now(),key)
                existing=self.db.execute("SELECT 1 FROM bank_transactions WHERE idempotency_key=?",(key,)).fetchone()
                self.db.execute("""INSERT INTO bank_transactions (transaction_id,source,provider,external_transaction_id,account_id,booked_at,value_at,amount,currency,direction,label,counterparty,reference,status,category,category_state,raw_metadata_json,imported_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(idempotency_key) DO UPDATE SET booked_at=excluded.booked_at,value_at=excluded.value_at,amount=excluded.amount,direction=excluded.direction,label=excluded.label,counterparty=excluded.counterparty,reference=excluded.reference,status=excluded.status,raw_metadata_json=excluded.raw_metadata_json,imported_at=excluded.imported_at""",values)
                inserted+=not bool(existing)
        return {"rows_imported":inserted,"duplicates":len(page.transactions)-inserted,"cursor":page.next_cursor}
    def store_balances(self,provider,balances):
        with self.db:
            for b in balances:self.db.execute("""INSERT INTO bank_balances (account_id,provider,current_balance,available_balance,currency,observed_at,imported_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(account_id,provider) DO UPDATE SET current_balance=excluded.current_balance,available_balance=excluded.available_balance,currency=excluded.currency,observed_at=excluded.observed_at,imported_at=excluded.imported_at""",(b.account_id,provider,str(b.current),str(b.available) if b.available is not None else None,b.currency,b.observed_at,now()))
    def store_accounts(self,provider,accounts):
        with self.db:
            for a in accounts:self.db.execute("""INSERT INTO bank_accounts (account_id,provider,name,currency,imported_at) VALUES(?,?,?,?,?) ON CONFLICT(account_id,provider) DO UPDATE SET name=excluded.name,currency=excluded.currency,imported_at=excluded.imported_at""",(a.account_id,provider,a.name,a.currency,now()))
    def accounts(self): return [dict(r) for r in self.db.execute("SELECT * FROM bank_accounts ORDER BY account_id")]
    def transactions(self): return [dict(r) for r in self.db.execute("SELECT * FROM bank_transactions ORDER BY booked_at DESC")]
    def balances(self): return [dict(r) for r in self.db.execute("SELECT * FROM bank_balances ORDER BY account_id")]
    def classify(self,transaction_id,category,*,confirmed=False):
        category=BankCategory(category).value;state="CONFIRMED" if confirmed else "PROPOSED"
        with self.db:
            result=self.db.execute("UPDATE bank_transactions SET category=?,category_state=? WHERE transaction_id=?",(category,state,transaction_id))
        if not result.rowcount: raise KeyError("bank transaction not found")
        return dict(self.db.execute("SELECT * FROM bank_transactions WHERE transaction_id=?",(transaction_id,)).fetchone())
    def sync(self,provider_name,provider,cursor=None):
        if not provider.configured: raise ValueError("bank provider is not configured")
        total=duplicates=0; observed_dates=[]
        while True:
            page=provider.transactions(cursor); observed_dates.extend(t.booked_at for t in page.transactions)
            result=self.import_page(provider_name,page);total+=result["rows_imported"];duplicates+=result["duplicates"];cursor=page.next_cursor
            if cursor is None: break
        self.store_accounts(provider_name,provider.accounts());self.store_balances(provider_name,provider.balances())
        available=self.db.execute("SELECT count(*) FROM bank_transactions WHERE provider=?",(provider_name,)).fetchone()[0]
        diagnostic=getattr(provider,"last_sync_diagnostic",None)
        if diagnostic is not None:
            diagnostic={**diagnostic,"classification":("TRANSACTIONS_IMPORTED" if total else
                diagnostic.get("classification","CONNECTED_NO_TRANSACTIONS")),"cursor_after":cursor}
        return {"rows_imported":total,"duplicates":duplicates,"cursor":cursor,
            "data_min_at":min(observed_dates) if observed_dates else None,
            "data_max_at":max(observed_dates) if observed_dates else None,
            "records_available":available,"qonto_diagnostic":diagnostic}
