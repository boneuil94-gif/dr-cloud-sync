"""Durable administration workflow around the catalogue rehydration engine."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import sqlite3
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from uuid import uuid4

from .jobs import JobRunner, JobStatus, SqliteJobRepository
from .os_admin import backup
from .rehydration import CatalogueRehydrationService, historical_observations
from .repositories import SQLiteOSRepository


class RehydrationConflict(RuntimeError):
    """The requested administrative operation is unsafe or already running."""


class AdminCatalogueRehydration:
    """Persist reports and schedule PREVIEW/APPLY_SAFE without blocking HTTP."""

    def __init__(self, database: Path, snapshot: Path, backup_root: Path, *,
                 environment: str, safe_mode: bool):
        self.database, self.snapshot, self.backup_root = database, snapshot, backup_root
        self.environment, self.safe_mode = environment, safe_mode
        self.jobs = SqliteJobRepository(database)
        self._lock = Lock()
        with sqlite3.connect(database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS catalogue_rehydration_reports(
              report_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
              fingerprint TEXT NOT NULL, report_json TEXT NOT NULL, applied_job_id TEXT UNIQUE)""")
            db.execute("""UPDATE sync_runs SET status='FAILED', completed_at=datetime('now'),
              error_code='ApplicationRestarted', error='Job invalidé par le redémarrage de l’application'
              WHERE job_type='CATALOGUE_REHYDRATION' AND status='RUNNING'""")

    def _repository(self):
        return SQLiteOSRepository(self.database, [])

    def _observations(self, repository):
        if not self.snapshot.is_file():
            raise RuntimeError("Snapshot historique de catalogue indisponible")
        return historical_observations(self.snapshot, repository.all())

    @staticmethod
    def _fingerprint(repository, observations) -> str:
        products = [{"key": p.drcloud_product_key, "product_id": p.product_id,
                     "combination_id": p.combination_id, "base_name": p.base_name,
                     "variant_name": p.variant_name, "attributes": p.attributes,
                     "reference": p.reference, "ean": p.ean, "sources": [p.name_source,
                     p.variant_source, p.reference_source, p.ean_source]}
                    for p in repository.all()]
        raw = {"products": products, "observations": [asdict(row) for row in observations]}
        return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False,
                                         default=str).encode()).hexdigest()

    def _running(self):
        return next((j for j in self.jobs.list_recent(100)
                     if j.job_type == "CATALOGUE_REHYDRATION" and j.status == JobStatus.RUNNING), None)

    def request_preview(self, actor: str) -> dict[str, Any]:
        with self._lock:
            running = self._running()
            if running:
                return {"job": self.public_job(running), "reused": True}
            job = self.jobs.create(job_type="CATALOGUE_REHYDRATION", connector="CATALOGUE",
                                   operation="PREVIEW", max_attempts=1)
            Thread(target=self._preview, args=(job.job_id,), daemon=True).start()
        return {"job": self.public_job(self.jobs.get(job.job_id)), "reused": False}

    def _preview(self, job_id: str):
        job = self.jobs.get(job_id)
        def operation():
            repository = self._repository(); observations = self._observations(repository)
            report = CatalogueRehydrationService(repository).preview(observations)
            fingerprint, report_id = self._fingerprint(repository, observations), str(uuid4())
            with sqlite3.connect(self.database) as db:
                db.execute("""INSERT INTO catalogue_rehydration_reports
                  (report_id,job_id,created_at,fingerprint,report_json)
                  VALUES(?,?,datetime('now'),?,?)""",
                  (report_id, job_id, fingerprint, json.dumps(report, ensure_ascii=False)))
            return {**report["summary"], "report_id": report_id,
                    "fingerprint": fingerprint, "before": report["before"],
                    "after_projected": report["after_projected"]}
        try:
            JobRunner(self.jobs).run(job, operation)
        except Exception:
            pass

    def request_apply(self, report_id: str, actor: str) -> dict[str, Any]:
        with self._lock:
            row = self._report_row(report_id)
            if not row: raise RehydrationConflict("Analyse réussie introuvable; relancez une analyse")
            if row["applied_job_id"]:
                return {"job": self.public_job(self.jobs.get(row["applied_job_id"])), "reused": True}
            if self._running(): raise RehydrationConflict("Une réhydratation est déjà en cours")
            repository = self._repository(); observations = self._observations(repository)
            if self._fingerprint(repository, observations) != row["fingerprint"]:
                raise RehydrationConflict("Analyse obsolète; le catalogue a changé, relancez une analyse")
            job = self.jobs.create(job_type="CATALOGUE_REHYDRATION", connector="CATALOGUE",
                operation="APPLY_SAFE", idempotency_key=f"report:{report_id}", max_attempts=1)
            with sqlite3.connect(self.database) as db:
                db.execute("UPDATE catalogue_rehydration_reports SET applied_job_id=? WHERE report_id=? AND applied_job_id IS NULL",
                           (job.job_id, report_id))
            Thread(target=self._apply, args=(job.job_id, actor), daemon=True).start()
        return {"job": self.public_job(self.jobs.get(job.job_id)), "reused": False}

    def _apply(self, job_id: str, actor: str):
        job = self.jobs.get(job_id)
        def operation():
            repository = self._repository(); observations = self._observations(repository)
            with sqlite3.connect(self.database) as db:
                db.row_factory = sqlite3.Row
                report = db.execute("SELECT fingerprint FROM catalogue_rehydration_reports WHERE applied_job_id=?",
                                    (job_id,)).fetchone()
            if not report or self._fingerprint(repository, observations) != report["fingerprint"]:
                raise RehydrationConflict("Analyse obsolète; le catalogue a changé, relancez une analyse")
            service = CatalogueRehydrationService(repository, backup=lambda: backup(
                self.database, self.backup_root, environment=self.environment,
                safe_mode=self.safe_mode))
            result = service.apply_safe(observations, actor=actor)
            result["backup"] = Path(result["backup"]).name
            result["invariants"] = {"product_count": True, "identities": True,
                "stock_movements": True, "inventory": True, "purchases_receipts": True}
            return result
        try:
            JobRunner(self.jobs).run(job, operation)
        except Exception:
            pass

    def _report_row(self, report_id):
        with sqlite3.connect(self.database) as db:
            db.row_factory = sqlite3.Row
            return db.execute("SELECT * FROM catalogue_rehydration_reports WHERE report_id=?", (report_id,)).fetchone()

    def status(self):
        recent = [j for j in self.jobs.list_recent(100) if j.job_type == "CATALOGUE_REHYDRATION"]
        preview = next((j for j in recent if j.operation == "PREVIEW"), None)
        apply = next((j for j in recent if j.operation == "APPLY_SAFE"), None)
        current = next((j for j in recent if j.status == JobStatus.RUNNING), recent[0] if recent else None)
        return {"state": "NEVER" if not current else current.status.value,
                "current_job": self.public_job(current),
                "last_preview": self.public_job(preview), "last_apply": self.public_job(apply)}

    def report(self, report_id: str | None, *, page=1, per_page=25,
               classification="ALL", search=""):
        if not report_id:
            with sqlite3.connect(self.database) as db:
                row = db.execute("SELECT report_id FROM catalogue_rehydration_reports ORDER BY rowid DESC LIMIT 1").fetchone()
            report_id = row[0] if row else None
        row = self._report_row(report_id) if report_id else None
        if not row: raise KeyError("report")
        report = json.loads(row["report_json"]); items = report["items"]
        if classification != "ALL": items = [x for x in items if x["classification"] == classification]
        needle = search.casefold().strip()
        if needle:
            items = [x for x in items if needle in " ".join(str(x.get(k) or "") for k in
                ("product_key", "product_id", "combination_id") ).casefold() or
                needle in " ".join(str(v) for v in x["current"].values()).casefold() or
                needle in " ".join(str(v) for v in x["candidates"].values()).casefold()]
        total = len(items); start = (page - 1) * per_page
        return {"report_id": report_id, "created_at": row["created_at"],
                "summary": report["summary"], "before": report["before"],
                "after_projected": report["after_projected"], "items": items[start:start+per_page],
                "pagination": {"page": page, "per_page": per_page, "total": total},
                "applied": bool(row["applied_job_id"])}

    @staticmethod
    def public_job(job):
        if not job: return None
        return {"job_id": job.job_id, "operation": job.operation, "status": job.status.value,
                "started_at": job.started_at, "completed_at": job.completed_at,
                "metrics": dict(job.summary), "error": job.error_message}
