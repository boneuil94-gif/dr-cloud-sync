"""Local-only inventory domain and SQLite persistence.

This module deliberately has no remote client dependency: inventory can never write to
PrestaShop or ShopCaisse.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .domain import MovementStatus, MovementType, StockMovement, drcloud_key
from .repositories import DuplicateStockMovement, SQLiteStockMovementRepository, ensure_stock_movements_schema
from .services import StockService


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InventoryError(ValueError):
    pass


class InventoryRepository:
    """Repository boundary suitable for replacement by a server database later."""
    def __init__(self, path: Path):
        self.path = path
        # Waitress dispatches requests on worker threads while the repository is
        # created once during application startup.  Allow that shared connection
        # to be used by whichever worker handles the request.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("""CREATE TABLE IF NOT EXISTS sessions(
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, started_at TEXT,
          completed_at TEXT, status TEXT NOT NULL)""")
        # Older databases constrained the unused VALIDATED state. Rebuilding the
        # table preserves every row while allowing the explicit PR D lifecycle.
        sql=(self.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()[0] or "")
        if "CHECK(status" in sql:
            self.db.executescript("""PRAGMA foreign_keys=OFF;
            ALTER TABLE sessions RENAME TO sessions_legacy;
            CREATE TABLE sessions(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, started_at TEXT,
              completed_at TEXT, status TEXT NOT NULL);
            INSERT INTO sessions SELECT * FROM sessions_legacy;
            DROP TABLE sessions_legacy; PRAGMA foreign_keys=ON;""")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS counts(
          session_id TEXT NOT NULL, prestashop_key TEXT NOT NULL, shopcaisse_item_id TEXT NOT NULL,
          physical_quantity INTEGER NOT NULL CHECK(physical_quantity >= 0), counted INTEGER NOT NULL DEFAULT 1,
          counted_at TEXT NOT NULL, updated_at TEXT NOT NULL, source TEXT NOT NULL,
          PRIMARY KEY(session_id,prestashop_key), FOREIGN KEY(session_id) REFERENCES sessions(id));
        CREATE TABLE IF NOT EXISTS history(
          id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, session_id TEXT NOT NULL,
          prestashop_key TEXT NOT NULL, old_quantity INTEGER, new_quantity INTEGER NOT NULL,
          source TEXT NOT NULL, action TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inventory_stock_proposals(
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
          status TEXT NOT NULL, source_version INTEGER NOT NULL, source_checksum TEXT NOT NULL,
          actor TEXT, validated_at TEXT, applied_at TEXT, movement_count INTEGER NOT NULL DEFAULT 0,
          idempotent_count INTEGER NOT NULL DEFAULT 0, error TEXT,
          FOREIGN KEY(session_id) REFERENCES sessions(id));
        CREATE TABLE IF NOT EXISTS inventory_stock_proposal_lines(
          proposal_id TEXT NOT NULL, drcloud_product_key TEXT NOT NULL,
          physical_quantity INTEGER NOT NULL, reference_quantity INTEGER NOT NULL,
          quantity_delta INTEGER NOT NULL, movement_type TEXT, idempotency_key TEXT,
          movement_id TEXT, PRIMARY KEY(proposal_id,drcloud_product_key),
          FOREIGN KEY(proposal_id) REFERENCES inventory_stock_proposals(id));
        """)
        ensure_stock_movements_schema(self.db)
        self.db.commit()

    def active_session(self) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM sessions WHERE status IN ('DRAFT','IN_PROGRESS') ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            stamp = now(); identifier = str(uuid.uuid4())
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (identifier, stamp, stamp, None, "IN_PROGRESS"))
            self.db.commit()
            row = self.db.execute("SELECT * FROM sessions WHERE id=?", (identifier,)).fetchone()
        return dict(row)

    def latest_session(self) -> dict[str, Any]:
        row=self.db.execute("SELECT * FROM sessions ORDER BY created_at DESC LIMIT 1").fetchone()
        return dict(row) if row else self.active_session()

    def new_session(self) -> dict[str, Any]:
        active=self.db.execute("SELECT * FROM sessions WHERE status IN ('DRAFT','IN_PROGRESS') ORDER BY created_at DESC LIMIT 1").fetchone()
        if active: return dict(active)
        stamp=now(); identifier=str(uuid.uuid4())
        with self.db: self.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",(identifier,stamp,stamp,None,"IN_PROGRESS"))
        return dict(self.db.execute("SELECT * FROM sessions WHERE id=?",(identifier,)).fetchone())

    def counts(self, session_id: str) -> dict[str, dict[str, Any]]:
        return {r["prestashop_key"]: dict(r) for r in self.db.execute("SELECT * FROM counts WHERE session_id=?", (session_id,))}

    def save(self, session_id: str, item: dict[str, Any], quantity: int, source: str, action: str) -> dict[str, Any]:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise InventoryError("La quantité doit être un entier positif ou nul")
        if source not in {"SCAN", "SEARCH", "MANUAL"} or action not in {"COUNT", "INCREMENT", "DECREMENT", "EDIT", "RESET"}:
            raise InventoryError("Opération de comptage invalide")
        status=self.db.execute("SELECT status FROM sessions WHERE id=?",(session_id,)).fetchone()
        if not status or status[0] not in {"DRAFT","IN_PROGRESS"}:
            raise InventoryError("Cette session clôturée est gelée; créez une nouvelle session")
        old = self.db.execute("SELECT physical_quantity,counted_at FROM counts WHERE session_id=? AND prestashop_key=?",
                              (session_id, item["prestashop_key"])).fetchone()
        stamp = now(); counted_at = old["counted_at"] if old else stamp
        with self.db:
            self.db.execute("UPDATE sessions SET status='IN_PROGRESS',started_at=COALESCE(started_at,?) WHERE id=? AND status='DRAFT'",(stamp,session_id))
            self.db.execute("""INSERT INTO counts VALUES(?,?,?,?,?,?,?,?)
              ON CONFLICT(session_id,prestashop_key) DO UPDATE SET physical_quantity=excluded.physical_quantity,
              counted=1,updated_at=excluded.updated_at,source=excluded.source""",
              (session_id, item["prestashop_key"], item["shopcaisse_item_id"], quantity, 1, counted_at, stamp, source))
            self.db.execute("INSERT INTO history(timestamp,session_id,prestashop_key,old_quantity,new_quantity,source,action) VALUES(?,?,?,?,?,?,?)",
                            (stamp, session_id, item["prestashop_key"], old["physical_quantity"] if old else None, quantity, source, action))
        return dict(self.db.execute("SELECT * FROM counts WHERE session_id=? AND prestashop_key=?", (session_id, item["prestashop_key"])).fetchone())

    def history(self, session_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM history WHERE session_id=? ORDER BY id", (session_id,))]

    def complete(self, session_id: str) -> None:
        with self.db:
            changed=self.db.execute("UPDATE sessions SET status='COMPLETED',completed_at=? WHERE id=? AND status='IN_PROGRESS'", (now(), session_id))
            if changed.rowcount != 1: raise InventoryError("Transition de clôture interdite")

    def append(self, movement: StockMovement) -> None:
        try:
            self.db.execute("""INSERT INTO stock_movements(id,drcloud_product_key,quantity_delta,movement_type,
              source_type,source_id,idempotency_key,status,created_at,validated_at,applied_at,actor,result_message)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(movement.id,movement.drcloud_product_key,movement.quantity_delta,
              movement.movement_type.value,movement.source_type,movement.source_id,movement.idempotency_key,
              movement.status.value,movement.created_at,movement.validated_at,movement.applied_at,movement.actor,movement.result_message))
        except sqlite3.IntegrityError as exc: raise DuplicateStockMovement from exc

    def get(self, identifier: str) -> StockMovement | None:
        return SQLiteStockMovementRepository._movement(self.db.execute("SELECT * FROM stock_movements WHERE id=?",(identifier,)).fetchone())
    def by_idempotency_key(self, source_type: str, key: str) -> StockMovement | None:
        return SQLiteStockMovementRepository._movement(self.db.execute("SELECT * FROM stock_movements WHERE source_type=? AND idempotency_key=?",(source_type,key)).fetchone())
    def list(self) -> list[StockMovement]:
        return [SQLiteStockMovementRepository._movement(r) for r in self.db.execute("SELECT * FROM stock_movements ORDER BY created_at,id")]  # type: ignore[misc]
    def current_quantity(self, product_id: str) -> int:
        return int(self.db.execute("SELECT COALESCE(SUM(quantity_delta),0) FROM stock_movements WHERE drcloud_product_key=? AND status='APPLIED'",(product_id,)).fetchone()[0])
    def current_positions(self) -> list[dict]:
        rows=self.db.execute("""WITH ranked AS (SELECT drcloud_product_key,source_type,ROW_NUMBER() OVER (PARTITION BY drcloud_product_key ORDER BY COALESCE(applied_at,created_at) DESC,created_at DESC,id DESC) rank FROM stock_movements WHERE status='APPLIED'), totals AS (SELECT drcloud_product_key,SUM(quantity_delta) quantity,MAX(COALESCE(applied_at,created_at)) last_movement_at FROM stock_movements WHERE status='APPLIED' GROUP BY drcloud_product_key) SELECT totals.*,ranked.source_type last_source_type FROM totals JOIN ranked USING(drcloud_product_key) WHERE ranked.rank=1 ORDER BY totals.drcloud_product_key""").fetchall()
        return [dict(row) for row in rows]
    def recent_movements(self, limit: int = 50) -> list[StockMovement]:
        return [SQLiteStockMovementRepository._movement(r) for r in self.db.execute("SELECT * FROM stock_movements WHERE status='APPLIED' ORDER BY COALESCE(applied_at,created_at) DESC,id DESC LIMIT ?",(max(0,min(int(limit),200)),))]  # type: ignore[misc]
    def movements_for_product(self, product_id: str) -> list[StockMovement]:
        return [SQLiteStockMovementRepository._movement(r) for r in self.db.execute("SELECT * FROM stock_movements WHERE drcloud_product_key=? AND status='APPLIED' ORDER BY COALESCE(applied_at,created_at) DESC,id DESC",(product_id,))]  # type: ignore[misc]
    def aggregate_statistics(self) -> dict[str,int]:
        row=self.db.execute("SELECT COUNT(DISTINCT drcloud_product_key),COALESCE(SUM(quantity_delta),0),COUNT(*),COALESCE(SUM(CASE WHEN date(COALESCE(applied_at,created_at))=date('now') THEN 1 ELSE 0 END),0) FROM stock_movements WHERE status='APPLIED'").fetchone()
        negative=self.db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM stock_movements WHERE status='APPLIED' GROUP BY drcloud_product_key HAVING SUM(quantity_delta)<0)").fetchone()[0]
        return {"products_tracked":row[0],"total_units":row[1],"movements":row[2],"movements_today":row[3],"negative_positions":negative}

    def proposal(self, session_id: str) -> dict[str, Any] | None:
        row=self.db.execute("SELECT * FROM inventory_stock_proposals WHERE session_id=?",(session_id,)).fetchone()
        if not row: return None
        value=dict(row); value["lines"]=[dict(r) for r in self.db.execute("SELECT * FROM inventory_stock_proposal_lines WHERE proposal_id=? ORDER BY drcloud_product_key",(row["id"],))]
        value["summary"]={"lines":len(value["lines"]),"increases":sum(x["quantity_delta"]>0 for x in value["lines"]),"decreases":sum(x["quantity_delta"]<0 for x in value["lines"]),"unchanged":sum(x["quantity_delta"]==0 for x in value["lines"])}
        return value


class InventoryService:
    def __init__(self, catalogue_path: Path, report_path: Path, repository: InventoryRepository):
        raw = json.loads(catalogue_path.read_text(encoding="utf-8"))
        self.items = raw["mappings"] if isinstance(raw, dict) else raw
        self.items = [{**item, "drcloud_product_key": item.get("drcloud_product_key") or drcloud_key(item["prestashop_key"])} for item in self.items]
        validation = json.loads(report_path.read_text(encoding="utf-8"))
        if validation.get("ready_for_inventory") is not True or not self.items:
            raise InventoryError("Un mapping validé et non vide est requis")
        self.repo = repository
        self.by_key = {i["prestashop_key"]: i for i in self.items}
        if len(self.by_key) != len(self.items):
            raise InventoryError("prestashop_key dupliquée")
        if len({i["drcloud_product_key"] for i in self.items}) != len(self.items):
            raise InventoryError("drcloud_product_key dupliquée")
        required=("prestashop_key","drcloud_product_key","shopcaisse_item_id")
        if any(not str(i.get(field) or "").strip() for i in self.items for field in required):
            raise InventoryError("Le catalogue contient une identité ou référence requise invalide")

    def session(self) -> dict[str, Any]: return self.repo.latest_session()
    def new_session(self) -> dict[str, Any]: return self.repo.new_session()

    @staticmethod
    def _ean(item: dict[str, Any]) -> str: return str(item.get("ean") or item.get("EAN") or "").strip()
    @staticmethod
    def _name(item: dict[str, Any]) -> str: return str(item.get("name") or item.get("nom complet") or "")

    def scan(self, ean: str) -> dict[str, Any]:
        matches = [i for i in self.items if self._ean(i) == ean.strip() and ean.strip()]
        return {"status": "UNIQUE" if len(matches)==1 else "UNKNOWN" if not matches else "AMBIGUOUS", "items": matches}

    def search(self, query: str = "", view: str = "ALL", without_ean: bool = False) -> list[dict[str, Any]]:
        q = query.casefold().strip(); session = self.session(); counts = self.repo.counts(session["id"])
        result = []
        for item in self.items:
            counted = item["prestashop_key"] in counts
            if view == "COUNTED" and not counted or view == "REMAINING" and counted: continue
            if without_ean and self._ean(item): continue
            haystack = " ".join(str(item.get(k) or "") for k in ("name","nom complet","ean","EAN","attributes","reference","référence","product_id","combination_id","prestashop_key","drcloud_product_key")).casefold()
            if q and q not in haystack: continue
            row = dict(item); row["count"] = counts.get(item["prestashop_key"]); result.append(row)
        return result

    def progress(self) -> dict[str, Any]:
        count = len(self.repo.counts(self.session()["id"])); total = len(self.items)
        return {"counted": count, "remaining": total-count, "total": total, "percent": round(count*100/total, 1) if total else 0.0}

    def count(self, key: str, quantity: int | None, source: str, action: str = "COUNT") -> dict[str, Any]:
        if key not in self.by_key: raise InventoryError("Article inconnu")
        session = self.session(); current = self.repo.counts(session["id"]).get(key)
        if action == "INCREMENT": quantity = (current["physical_quantity"] if current else 0) + 1
        elif action == "DECREMENT": quantity = max(0, (current["physical_quantity"] if current else 0) - 1)
        elif action == "RESET": quantity = 0
        return self.repo.save(session["id"], self.by_key[key], quantity, source, action)  # type: ignore[arg-type]

    def results(self) -> list[dict[str, Any]]:
        counts = self.repo.counts(self.session()["id"]); rows=[]
        for item in self.items:
            count=counts.get(item["prestashop_key"]); physical=count["physical_quantity"] if count else None
            ps=item.get("stock_prestashop"); sc=item.get("stock_shopcaisse")
            row=dict(item); row.update(physical_quantity=physical, counted=count is not None,
              counted_at=count["counted_at"] if count else None,
              difference_prestashop=physical-int(ps) if physical is not None and ps is not None else None,
              difference_shopcaisse=physical-int(sc) if physical is not None and sc is not None else None)
            rows.append(row)
        return rows

    def complete(self) -> dict[str, Any]:
        progress=self.progress()
        if progress["remaining"]: return {"completed": False, **progress}
        session_id=self.session()["id"]; self.repo.complete(session_id)
        proposal=self.create_proposal(session_id)
        return {"completed": True, "proposal_id":proposal["id"], **progress}

    def proposal(self, session_id: str | None = None) -> dict[str, Any] | None:
        return self.repo.proposal(session_id or self.session()["id"])

    def create_proposal(self, session_id: str | None = None) -> dict[str, Any]:
        session_id=session_id or self.session()["id"]
        existing=self.repo.proposal(session_id)
        if existing: return existing
        session=self.repo.db.execute("SELECT status FROM sessions WHERE id=?",(session_id,)).fetchone()
        if not session or session[0] != "COMPLETED": raise InventoryError("La proposition exige un inventaire terminé")
        counts=self.repo.counts(session_id)
        if len(counts) != len(self.items): raise InventoryError("L'inventaire doit être complet")
        if any(i.get("stock_prestashop") is None for i in self.items):
            raise InventoryError("Le stock de référence validé est indisponible")
        snapshot=[(i["drcloud_product_key"],counts[i["prestashop_key"]]["physical_quantity"],int(i["stock_prestashop"])) for i in self.items]
        checksum=hashlib.sha256(json.dumps(snapshot,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
        identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,f"drcloud:inventory:{session_id}:v1")); stamp=now()
        with self.repo.db:
            self.repo.db.execute("INSERT INTO inventory_stock_proposals(id,session_id,created_at,status,source_version,source_checksum) VALUES(?,?,?,?,?,?)",(identifier,session_id,stamp,"PROPOSED",1,checksum))
            for key,physical,reference in snapshot:
                delta=physical-reference
                self.repo.db.execute("INSERT INTO inventory_stock_proposal_lines VALUES(?,?,?,?,?,?,?,?)",(identifier,key,physical,reference,delta,MovementType.INVENTORY_CORRECTION.value if delta else None,f"inventory:{session_id}:{key}:v1" if delta else None,None))
            self.repo.db.execute("UPDATE sessions SET status='PROPOSED' WHERE id=? AND status='COMPLETED'",(session_id,))
        return self.repo.proposal(session_id)  # type: ignore[return-value]

    def validate(self, actor: str) -> dict[str, Any]:
        proposal=self.proposal()
        if not proposal: raise InventoryError("Aucune proposition à valider")
        if proposal["status"] in {"VALIDATED","APPLIED"}: return proposal
        if proposal["status"] != "PROPOSED": raise InventoryError("État incompatible avec la validation")
        stamp=now()
        with self.repo.db:
            self.repo.db.execute("UPDATE inventory_stock_proposals SET status='VALIDATED',actor=?,validated_at=?,error=NULL WHERE id=?",(actor,stamp,proposal["id"]))
            self.repo.db.execute("UPDATE sessions SET status='VALIDATED' WHERE id=? AND status='PROPOSED'",(proposal["session_id"],))
        return self.repo.proposal(proposal["session_id"])  # type: ignore[return-value]

    def apply(self, actor: str) -> dict[str, Any]:
        proposal=self.proposal()
        if not proposal: raise InventoryError("Aucune proposition à appliquer")
        if proposal["status"] == "APPLIED": return proposal
        if proposal["status"] != "VALIDATED": raise InventoryError("La proposition doit être validée")
        stamp=now(); created=0; replayed=0; movement_ids=[]
        try:
            self.repo.db.execute("BEGIN IMMEDIATE")
            for line in proposal["lines"]:
                if not line["quantity_delta"]: continue
                movement=StockMovement(drcloud_product_key=line["drcloud_product_key"],quantity_delta=line["quantity_delta"],movement_type=MovementType.INVENTORY_CORRECTION,source_type="INVENTORY",source_id=proposal["session_id"],idempotency_key=line["idempotency_key"],status=MovementStatus.APPLIED,validated_at=proposal["validated_at"],applied_at=stamp,actor=actor,result_message="Inventaire validé et appliqué localement")
                result=StockService(self.repo).apply(movement); created+=result.created; replayed+=not result.created; movement_ids.append(result.movement.id)
                self.repo.db.execute("UPDATE inventory_stock_proposal_lines SET movement_id=? WHERE proposal_id=? AND drcloud_product_key=?",(result.movement.id,proposal["id"],line["drcloud_product_key"]))
            self.repo.db.execute("UPDATE inventory_stock_proposals SET status='APPLIED',applied_at=?,movement_count=?,idempotent_count=?,error=NULL WHERE id=?",(stamp,len(movement_ids),replayed,proposal["id"]))
            self.repo.db.execute("UPDATE sessions SET status='APPLIED' WHERE id=? AND status='VALIDATED'",(proposal["session_id"],))
            self.repo.db.commit()
        except Exception as exc:
            self.repo.db.rollback()
            clean="L'application transactionnelle a échoué; vous pouvez réessayer."
            with self.repo.db: self.repo.db.execute("UPDATE inventory_stock_proposals SET error=? WHERE id=?",(clean,proposal["id"]))
            raise InventoryError(clean) from exc
        return self.repo.proposal(proposal["session_id"])  # type: ignore[return-value]

    def report(self, output_path: Path | None = None) -> dict[str, Any]:
        rows=self.results(); progress=self.progress()
        report={"session_id": self.session()["id"], "total_articles": len(rows), "counted": progress["counted"],
          "physical_total_units": sum(r["physical_quantity"] or 0 for r in rows if r["counted"]),
          "prestashop_differences": sum(r["difference_prestashop"] not in (None,0) for r in rows),
          "shopcaisse_differences": sum(r["difference_shopcaisse"] not in (None,0) for r in rows),
          "generated_at": now(), "ready_for_stock_correction": progress["remaining"] == 0, "results": rows}
        report["difference_summary"]={
          "correct_everywhere":sum(r["difference_prestashop"]==0 and r["difference_shopcaisse"]==0 for r in rows),
          "prestashop_only":sum(r["difference_prestashop"] not in (None,0) and r["difference_shopcaisse"]==0 for r in rows),
          "shopcaisse_only":sum(r["difference_shopcaisse"] not in (None,0) and r["difference_prestashop"]==0 for r in rows),
          "both":sum(r["difference_prestashop"] not in (None,0) and r["difference_shopcaisse"] not in (None,0) for r in rows)}
        if output_path:
            output_path.parent.mkdir(parents=True,exist_ok=True); temporary=output_path.with_suffix(output_path.suffix+".tmp")
            temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");temporary.replace(output_path)
        return report

    def csv(self) -> str:
        fields=("prestashop_key","product_id","combination_id","name","ean","reference","shopcaisse_item_id","physical_quantity","stock_prestashop","stock_shopcaisse","difference_prestashop","difference_shopcaisse","counted_at")
        out=io.StringIO(); writer=csv.DictWriter(out, fields, extrasaction="ignore"); writer.writeheader()
        for row in self.results(): writer.writerow(row)
        return out.getvalue()
