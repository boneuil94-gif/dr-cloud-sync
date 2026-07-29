"""Local-only inventory domain and SQLite persistence.

This module deliberately has no remote client dependency: inventory can never write to
PrestaShop or ShopCaisse.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .domain import drcloud_key


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InventoryError(ValueError):
    pass


@dataclass(frozen=True)
class StockMovement:
    """Future, local movement contract. No movement is applied in inventory V1."""
    id: str
    prestashop_key: str
    quantity_delta: int
    movement_type: str
    source_id: str
    created_at: str
    validated_at: str | None = None


class InventoryRepository:
    """Repository boundary suitable for replacement by a server database later."""
    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions(
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, started_at TEXT,
          completed_at TEXT, status TEXT NOT NULL CHECK(status IN ('DRAFT','IN_PROGRESS','COMPLETED','VALIDATED')));
        CREATE TABLE IF NOT EXISTS counts(
          session_id TEXT NOT NULL, prestashop_key TEXT NOT NULL, shopcaisse_item_id TEXT NOT NULL,
          physical_quantity INTEGER NOT NULL CHECK(physical_quantity >= 0), counted INTEGER NOT NULL DEFAULT 1,
          counted_at TEXT NOT NULL, updated_at TEXT NOT NULL, source TEXT NOT NULL,
          PRIMARY KEY(session_id,prestashop_key), FOREIGN KEY(session_id) REFERENCES sessions(id));
        CREATE TABLE IF NOT EXISTS history(
          id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, session_id TEXT NOT NULL,
          prestashop_key TEXT NOT NULL, old_quantity INTEGER, new_quantity INTEGER NOT NULL,
          source TEXT NOT NULL, action TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stock_movements(
          id TEXT PRIMARY KEY, prestashop_key TEXT NOT NULL, quantity_delta INTEGER NOT NULL,
          movement_type TEXT NOT NULL, source_id TEXT NOT NULL, created_at TEXT NOT NULL, validated_at TEXT);
        """)
        self.db.commit()

    def active_session(self) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM sessions ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            stamp = now(); identifier = str(uuid.uuid4())
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (identifier, stamp, stamp, None, "IN_PROGRESS"))
            self.db.commit()
            row = self.db.execute("SELECT * FROM sessions WHERE id=?", (identifier,)).fetchone()
        return dict(row)

    def counts(self, session_id: str) -> dict[str, dict[str, Any]]:
        return {r["prestashop_key"]: dict(r) for r in self.db.execute("SELECT * FROM counts WHERE session_id=?", (session_id,))}

    def save(self, session_id: str, item: dict[str, Any], quantity: int, source: str, action: str) -> dict[str, Any]:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise InventoryError("La quantité doit être un entier positif ou nul")
        if source not in {"SCAN", "SEARCH", "MANUAL"} or action not in {"COUNT", "INCREMENT", "DECREMENT", "EDIT", "RESET"}:
            raise InventoryError("Opération de comptage invalide")
        old = self.db.execute("SELECT physical_quantity,counted_at FROM counts WHERE session_id=? AND prestashop_key=?",
                              (session_id, item["prestashop_key"])).fetchone()
        stamp = now(); counted_at = old["counted_at"] if old else stamp
        with self.db:
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
            self.db.execute("UPDATE sessions SET status='COMPLETED',completed_at=? WHERE id=?", (now(), session_id))


class InventoryService:
    def __init__(self, catalogue_path: Path, report_path: Path, repository: InventoryRepository):
        raw = json.loads(catalogue_path.read_text(encoding="utf-8"))
        self.items = raw["mappings"] if isinstance(raw, dict) else raw
        self.items = [{**item, "drcloud_product_key": item.get("drcloud_product_key") or drcloud_key(item["prestashop_key"])} for item in self.items]
        validation = json.loads(report_path.read_text(encoding="utf-8"))
        if validation.get("ready_for_inventory") is not True or len(self.items) != 478:
            raise InventoryError("Le mapping validé de 478 articles est requis")
        self.repo = repository
        self.by_key = {i["prestashop_key"]: i for i in self.items}
        if len(self.by_key) != len(self.items):
            raise InventoryError("prestashop_key dupliquée")
        if len({i["drcloud_product_key"] for i in self.items}) != len(self.items):
            raise InventoryError("drcloud_product_key dupliquée")

    def session(self) -> dict[str, Any]: return self.repo.active_session()

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
        return {"counted": count, "remaining": total-count, "total": total, "percent": round(count*100/total, 1)}

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
        self.repo.complete(self.session()["id"]); return {"completed": True, **progress}

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
