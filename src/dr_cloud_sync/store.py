"""Transactional SQLite snapshot of the master PrestaShop catalogue."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sync_runs (
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
  counts_json TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS prestashop_entities (
  resource TEXT NOT NULL, source_id INTEGER NOT NULL, payload_json TEXT NOT NULL,
  synced_at TEXT NOT NULL, PRIMARY KEY (resource, source_id)
);
CREATE INDEX IF NOT EXISTS idx_entities_resource ON prestashop_entities(resource);
CREATE TABLE IF NOT EXISTS external_stock_observations (
  job_id TEXT NOT NULL, source TEXT NOT NULL,
  source_product_id TEXT NOT NULL, source_combination_id TEXT NOT NULL,
  quantity INTEGER NOT NULL, observed_at TEXT NOT NULL,
  PRIMARY KEY(job_id, source, source_product_id, source_combination_id)
);
CREATE INDEX IF NOT EXISTS idx_stock_observation_job ON external_stock_observations(source, job_id);
"""


class SnapshotStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(SCHEMA)
            yield connection
        finally:
            connection.close()

    def begin_run(self, connection: sqlite3.Connection) -> int:
        cursor = connection.execute(
            "INSERT INTO sync_runs(started_at, status) VALUES (?, 'running')", (_now(),)
        )
        connection.commit()
        return int(cursor.lastrowid)

    def replace_snapshot(
        self, connection: sqlite3.Connection, run_id: int | None, resources: dict[str, list[dict[str, Any]]],
        *, job_id: str | None = None
    ) -> dict[str, int]:
        counts = {name: len(rows) for name, rows in resources.items()}
        now = _now()
        try:
            connection.execute("BEGIN")
            for resource, rows in resources.items():
                connection.execute("DELETE FROM prestashop_entities WHERE resource = ?", (resource,))
                connection.executemany(
                    "INSERT INTO prestashop_entities(resource, source_id, payload_json, synced_at) VALUES (?, ?, ?, ?)",
                    [(resource, _source_id(row), json.dumps(row, ensure_ascii=False), now) for row in rows],
                )
            if job_id is not None:
                stocks = resources.get("stock_availables")
                if stocks is None:
                    raise ValueError("Snapshot PrestaShop incomplet: stock_availables absent")
                observations = [_stock_row(row, job_id, now) for row in stocks]
                connection.executemany("""INSERT INTO external_stock_observations
                    (job_id,source,source_product_id,source_combination_id,quantity,observed_at)
                    VALUES (?,?,?,?,?,?)""", observations)
            if run_id is not None:
                connection.execute(
                    "UPDATE sync_runs SET completed_at=?, status='completed', counts_json=? WHERE id=?",
                    (now, json.dumps(counts, sort_keys=True), run_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return counts

    def fail_run(self, connection: sqlite3.Connection, run_id: int, error: str) -> None:
        connection.execute(
            "UPDATE sync_runs SET completed_at=?, status='failed', error=? WHERE id=?",
            (_now(), error[:500], run_id),
        )
        connection.commit()


def _source_id(row: dict[str, Any]) -> int:
    try:
        return int(row["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Entité PrestaShop sans identifiant valide") from exc


def _stock_row(row: dict[str, Any], job_id: str, observed_at: str) -> tuple[Any, ...]:
    try:
        product_id = str(int(row["id_product"]))
        combination_id = str(int(row.get("id_product_attribute") or 0))
        quantity = int(row["quantity"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Quantité PrestaShop non exploitable dans le snapshot complet") from exc
    return job_id, "PRESTASHOP", product_id, combination_id, quantity, observed_at


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
