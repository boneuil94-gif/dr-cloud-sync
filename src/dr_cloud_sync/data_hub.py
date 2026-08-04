"""Provider-neutral, SQLite-backed synchronization control plane.

Connectors remain read-only adapters.  This module owns scheduling and state, not
the Sales, Stock or Bank ledgers which remain separate authorities.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib, json, sqlite3, time, uuid
from pathlib import Path
from typing import Callable, Mapping, Any
from .connector_diagnostics import ConnectorDiagnostic, DiagnosticRepository, from_exception
from .sqlite import connection

class SourceStatus(StrEnum):
    CONNECTED="CONNECTED"; PARTIAL="PARTIAL"; NOT_CONFIGURED="NOT_CONFIGURED"; DISABLED="DISABLED"; ERROR="ERROR"; UNSUPPORTED="UNSUPPORTED"; UNAVAILABLE="UNAVAILABLE"
class DataFreshness(StrEnum):
    FRESH="FRESH"; STALE="STALE"; ERROR="ERROR"; UNAVAILABLE="UNAVAILABLE"; NOT_CONFIGURED="NOT_CONFIGURED"; DISABLED="DISABLED"
class SyncStatus(StrEnum):
    PENDING="PENDING"; RUNNING="RUNNING"; SUCCEEDED="SUCCEEDED"; FAILED="FAILED"; BLOCKED="BLOCKED"; RETRY="RETRY"

@dataclass(frozen=True)
class JobDefinition:
    job_id: str; source_id: str; job_type: str; interval_seconds: int
    dependencies: tuple[str,...]=(); max_attempts: int=3

DEFAULT_SOURCES=(
 ("shopcaisse_sales","SHOPCAISSE_SALES","ShopCaisse"),
 ("prestashop_sales","PRESTASHOP_SALES","PrestaShop"),
 ("prestashop_catalog","PRESTASHOP_CATALOG","PrestaShop"),
 ("bank","BANK","Qonto"), ("purchases","PURCHASES","LOCAL"), ("stock","STOCK","LOCAL"))

SCHEMA="""
CREATE TABLE IF NOT EXISTS data_sources(source_id TEXT PRIMARY KEY,source_type TEXT NOT NULL,provider TEXT NOT NULL,status TEXT NOT NULL,enabled INTEGER NOT NULL,last_attempt_at TEXT,last_success_at TEXT,last_error TEXT,cursor TEXT,capabilities_json TEXT NOT NULL,stale_after_seconds INTEGER NOT NULL,rows_imported INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sync_jobs(job_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,job_type TEXT NOT NULL,interval_seconds INTEGER NOT NULL,dependencies_json TEXT NOT NULL,max_attempts INTEGER NOT NULL,next_run_at TEXT,last_run_at TEXT,status TEXT NOT NULL DEFAULT 'PENDING',attempts INTEGER NOT NULL DEFAULT 0,duration_ms INTEGER,error TEXT,lock_token TEXT,locked_at TEXT);
CREATE TABLE IF NOT EXISTS data_hub_sync_runs(run_id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,started_at TEXT NOT NULL,completed_at TEXT,status TEXT NOT NULL,attempt INTEGER NOT NULL,result_json TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS automation_worker_heartbeat(worker_id TEXT PRIMARY KEY,seen_at TEXT NOT NULL,database_fingerprint TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sync_jobs_due ON sync_jobs(status,next_run_at);
"""

def utcnow(): return datetime.now(timezone.utc)
def iso(value: datetime): return value.astimezone(timezone.utc).isoformat()

class DataHub:
    def __init__(self,path: Path,*,clock: Callable[[],datetime]=utcnow):
        self.path=Path(path); self.clock=clock
        with self.connect() as db: db.executescript(SCHEMA)
        self.diagnostics=DiagnosticRepository(self.path)
    def connect(self):
        """Yield a transactional connection and always release its file handle.

        ``sqlite3.Connection.__exit__`` only commits or rolls back; it does not
        close the connection.  DataHub performs many short operations, so relying
        on that protocol used to leak one descriptor per operation.
        """
        return connection(self.path, timeout=10, row_factory=sqlite3.Row)
    def register_source(self,source_id,source_type,provider,*,configured=False,enabled=True,capabilities=(),stale_after_seconds=3600,status=None):
        """Register observable source state without equating a port with a connection.

        ``configured=True`` is reserved for adapters whose runtime prerequisites (and,
        where applicable, authenticated health check) succeeded.  Audited partial and
        unsupported capabilities can be stated explicitly without producing a false
        green status.
        """
        status=SourceStatus(status) if status else SourceStatus.DISABLED if not enabled else SourceStatus.CONNECTED if configured else SourceStatus.NOT_CONFIGURED
        with self.connect() as db: db.execute("""INSERT INTO data_sources VALUES(?,?,?,?,?,NULL,NULL,NULL,NULL,?,?,0)
          ON CONFLICT(source_id) DO UPDATE SET source_type=excluded.source_type,provider=excluded.provider,enabled=excluded.enabled,capabilities_json=excluded.capabilities_json,stale_after_seconds=excluded.stale_after_seconds,status=CASE WHEN data_sources.status='ERROR' AND excluded.status='CONNECTED' THEN data_sources.status WHEN data_sources.last_success_at IS NOT NULL AND excluded.status='UNAVAILABLE' THEN data_sources.status ELSE excluded.status END""",
          (source_id,source_type,provider,status.value,int(enabled),json.dumps(list(capabilities)),stale_after_seconds))
    def register_job(self,job: JobDefinition):
        if job.interval_seconds<1: raise ValueError("interval_seconds must be positive")
        with self.connect() as db: db.execute("""INSERT INTO sync_jobs(job_id,source_id,job_type,interval_seconds,dependencies_json,max_attempts,next_run_at)
          VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET interval_seconds=excluded.interval_seconds,dependencies_json=excluded.dependencies_json,max_attempts=excluded.max_attempts""",
          (job.job_id,job.source_id,job.job_type,job.interval_seconds,json.dumps(job.dependencies),job.max_attempts,iso(self.clock())))
    def connector_health_check(self, source_id, provider, check, *, operation, stage,
                               endpoint_path, request_id=None):
        """Run and persist a connector health check, shared by startup and the UI."""
        request_id=request_id or str(uuid.uuid4()); started=time.monotonic()
        try:
            check(); duration=int((time.monotonic()-started)*1000)
            diagnostic=ConnectorDiagnostic(source_id,provider,operation,stage,"UNKNOWN",
                "Test de lecture réussi",iso(self.clock()),endpoint_path=endpoint_path,
                duration_ms=duration,request_id=request_id,success=True)
            status=SourceStatus.CONNECTED
        except Exception as exc:
            duration=int((time.monotonic()-started)*1000)
            diagnostic=from_exception(source_id=source_id,provider=provider,operation=operation,
                stage=stage,exc=exc,duration_ms=duration,request_id=request_id)
            diagnostic=replace(diagnostic,operation=operation,stage=stage,endpoint_path=endpoint_path)
            status=SourceStatus.ERROR
        diagnostic_id=self.diagnostics.add(diagnostic)
        with self.connect() as db:
            db.execute("UPDATE data_sources SET status=?,last_attempt_at=?,last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END,last_error=CASE WHEN ? THEN NULL ELSE ? END WHERE source_id=?",
                (status.value,diagnostic.occurred_at,int(diagnostic.success),diagnostic.occurred_at,
                 int(diagnostic.success),None if diagnostic.success else diagnostic.message,source_id))
        return diagnostic_id, diagnostic
    def sources(self):
        now=self.clock(); result=[]
        with self.connect() as db: rows=db.execute("SELECT * FROM data_sources ORDER BY source_id").fetchall()
        for row in rows:
            item=dict(row); item["enabled"]=bool(item["enabled"]);item["capabilities"]=json.loads(item.pop("capabilities_json"))
            if item["status"]==SourceStatus.NOT_CONFIGURED: fresh=DataFreshness.NOT_CONFIGURED
            elif item["status"] in (SourceStatus.ERROR,): fresh=DataFreshness.ERROR
            elif item["status"]==SourceStatus.DISABLED: fresh=DataFreshness.DISABLED
            elif item["status"] in (SourceStatus.UNSUPPORTED,SourceStatus.UNAVAILABLE) or not item["last_success_at"]: fresh=DataFreshness.UNAVAILABLE
            else: fresh=DataFreshness.FRESH if now-datetime.fromisoformat(item["last_success_at"])<=timedelta(seconds=item["stale_after_seconds"]) else DataFreshness.STALE
            with self.connect() as db:
                schedule=db.execute("SELECT MIN(next_run_at) FROM sync_jobs WHERE source_id=?",(item["source_id"],)).fetchone()[0]
            item["next_run_at"]=schedule
            item["freshness"]=fresh.value;result.append(item)
        return result
    def jobs(self):
        with self.connect() as db: rows=db.execute("SELECT * FROM sync_jobs ORDER BY job_id").fetchall()
        return [{**dict(r),"dependencies":json.loads(r["dependencies_json"])} for r in rows]
    def run(self,job_id:str,operation:Callable[[str|None],Mapping[str,Any]],*,manual=False):
        now=self.clock(); token=f"{job_id}:{iso(now)}"
        with self.connect() as db:
            job=db.execute("SELECT * FROM sync_jobs WHERE job_id=?",(job_id,)).fetchone()
            if not job: raise KeyError(job_id)
            source=db.execute("SELECT * FROM data_sources WHERE source_id=?",(job["source_id"],)).fetchone()
            if not source or not source["enabled"] or source["status"] not in (SourceStatus.CONNECTED,SourceStatus.UNAVAILABLE): return self._blocked(job_id,"source unavailable")
            if not manual and job["next_run_at"] and datetime.fromisoformat(job["next_run_at"])>now: return dict(job)
            dependencies=json.loads(job["dependencies_json"])
            for dependency in dependencies:
                state=db.execute("SELECT status FROM sync_jobs WHERE job_id=?",(dependency,)).fetchone()
                if not state or state[0]!=SyncStatus.SUCCEEDED: return self._blocked(job_id,f"dependency not ready: {dependency}")
            lock_expiry=iso(now-timedelta(seconds=max(300,job["interval_seconds"]*2)))
            claimed=db.execute("UPDATE sync_jobs SET status='RUNNING',lock_token=?,locked_at=?,attempts=attempts+1,last_run_at=? WHERE job_id=? AND (status!='RUNNING' OR locked_at<?)",(token,iso(now),iso(now),job_id,lock_expiry)).rowcount
            if not claimed: raise RuntimeError("job already running")
            if job["status"]==SyncStatus.RUNNING:
                db.execute("UPDATE data_hub_sync_runs SET completed_at=?,status='FAILED',error='interrupted worker lease expired' WHERE job_id=? AND status='RUNNING'",(iso(now),job_id))
            attempt=job["attempts"]+1; cur=db.execute("INSERT INTO data_hub_sync_runs(job_id,started_at,status,attempt) VALUES(?,?,'RUNNING',?)",(job_id,iso(now),attempt));run_id=cur.lastrowid
        started=self.clock()
        try:
            result=dict(operation(source["cursor"])); end=self.clock(); rows=int(result.get("rows_imported",0)); cursor=result.get("cursor",source["cursor"])
            with self.connect() as db:
                db.execute("UPDATE data_hub_sync_runs SET completed_at=?,status='SUCCEEDED',result_json=? WHERE run_id=?",(iso(end),json.dumps(result),run_id))
                db.execute("UPDATE sync_jobs SET status='SUCCEEDED',next_run_at=?,duration_ms=?,error=NULL,lock_token=NULL WHERE job_id=?",(iso(end+timedelta(seconds=job["interval_seconds"])),int((end-started).total_seconds()*1000),job_id))
                db.execute("UPDATE data_sources SET last_attempt_at=?,last_success_at=?,last_error=NULL,cursor=?,rows_imported=rows_imported+?,status='CONNECTED' WHERE source_id=?",(iso(end),iso(end),cursor,rows,job["source_id"]))
            return self.job(job_id)
        except Exception as exc:
            from .jobs import sanitize_error
            end=self.clock(); error=sanitize_error(exc); retry=attempt<job["max_attempts"] and bool(getattr(exc,"retryable",False)); delay=min(3600,30*2**(attempt-1))
            with self.connect() as db:
                db.execute("UPDATE data_hub_sync_runs SET completed_at=?,status='FAILED',error=? WHERE run_id=?",(iso(end),error,run_id))
                db.execute("UPDATE sync_jobs SET status=?,next_run_at=?,duration_ms=?,error=?,lock_token=NULL WHERE job_id=?",(SyncStatus.RETRY if retry else SyncStatus.FAILED,iso(end+timedelta(seconds=delay)),int((end-started).total_seconds()*1000),error,job_id))
                db.execute("UPDATE data_sources SET last_attempt_at=?,last_error=?,status='ERROR' WHERE source_id=?",(iso(end),error,job["source_id"]))
            self.diagnostics.add(from_exception(source_id=job["source_id"],provider=source["provider"],operation=job["job_type"],stage=getattr(exc,"diagnostic",{}).get("stage","execution"),exc=exc,job_id=job_id,run_id=run_id,attempt=attempt,duration_ms=int((end-started).total_seconds()*1000),cursor=source["cursor"],next_retry_at=iso(end+timedelta(seconds=delay)) if retry else None))
            raise
    def _blocked(self,job_id,error):
        with self.connect() as db: db.execute("UPDATE sync_jobs SET status='BLOCKED',error=? WHERE job_id=?",(error,job_id))
        return self.job(job_id)
    def job(self,job_id):
        with self.connect() as db: row=db.execute("SELECT * FROM sync_jobs WHERE job_id=?",(job_id,)).fetchone()
        return dict(row) if row else None
    def run_due(self,operations):
        now=iso(self.clock()); results=[]
        for job in self.jobs():
            if not job["next_run_at"] or job["next_run_at"]>now: continue
            operation=operations.get(job["job_type"])
            if operation is None: results.append(self._blocked(job["job_id"],f"runtime handler missing: {job['job_type']}"))
            else: results.append(self.run(job["job_id"],operation))
        return results
    def database_fingerprint(self):
        return hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:12]
    def heartbeat(self,worker_id="automation-worker"):
        with self.connect() as db: db.execute("INSERT INTO automation_worker_heartbeat VALUES(?,?,?) ON CONFLICT(worker_id) DO UPDATE SET seen_at=excluded.seen_at,database_fingerprint=excluded.database_fingerprint",(worker_id,iso(self.clock()),self.database_fingerprint()))
    def runtime(self,operations=None):
        with self.connect() as db: heartbeats=[dict(row) for row in db.execute("SELECT * FROM automation_worker_heartbeat ORDER BY worker_id")]
        registered=set((operations or {}).keys())
        return {"database_fingerprint":self.database_fingerprint(),"worker_heartbeats":heartbeats,"jobs":[{"job_id":j["job_id"],"handler_registered":j["job_type"] in registered} for j in self.jobs()]}
    def health(self):
        sources=self.sources()
        for source in sources:
            source["last_diagnostic"]=(self.diagnostics.recent(source["source_id"],1) or [None])[0]
            source["failure_history"]=self.diagnostics.recent(source["source_id"],5)
        return {"status":"ERROR" if any(s["freshness"]=="ERROR" for s in sources) else "DEGRADED" if any(s["freshness"]!="FRESH" for s in sources) else "OK","sources":sources,"jobs":self.jobs()}
