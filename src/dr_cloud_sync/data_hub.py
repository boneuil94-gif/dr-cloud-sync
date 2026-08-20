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
from .schema import ensure_schema

class SourceStatus(StrEnum):
    CONNECTED="CONNECTED"; PARTIAL="PARTIAL"; NOT_CONFIGURED="NOT_CONFIGURED"; DISABLED="DISABLED"; ERROR="ERROR"; UNSUPPORTED="UNSUPPORTED"; UNAVAILABLE="UNAVAILABLE"
class DataFreshness(StrEnum):
    FRESH="FRESH"; STALE="STALE"; CONNECTED_NO_DATA="CONNECTED_NO_DATA"; ERROR="ERROR"; UNAVAILABLE="UNAVAILABLE"; NOT_CONFIGURED="NOT_CONFIGURED"; DISABLED="DISABLED"
class WorkerState(StrEnum):
    HEALTHY="HEALTHY"; STALE="STALE"; MISSING="MISSING"; DATABASE_MISMATCH="DATABASE_MISMATCH"
class SyncStatus(StrEnum):
    PENDING="PENDING"; RUNNING="RUNNING"; SUCCEEDED="SUCCEEDED"; FAILED="FAILED"; BLOCKED="BLOCKED"; RETRY="RETRY"
class BatchAlreadyRunning(RuntimeError):
    """Raised when the database-wide global synchronization lease is held."""

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
CREATE TABLE IF NOT EXISTS data_sources(source_id TEXT PRIMARY KEY,source_type TEXT NOT NULL,provider TEXT NOT NULL,status TEXT NOT NULL,enabled INTEGER NOT NULL,last_attempt_at TEXT,last_success_at TEXT,last_error TEXT,cursor TEXT,capabilities_json TEXT NOT NULL,stale_after_seconds INTEGER NOT NULL,rows_imported INTEGER NOT NULL DEFAULT 0,last_rows_imported INTEGER,last_run_id INTEGER,data_min_at TEXT,data_max_at TEXT,records_available INTEGER);
CREATE TABLE IF NOT EXISTS sync_jobs(job_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,job_type TEXT NOT NULL,interval_seconds INTEGER NOT NULL,dependencies_json TEXT NOT NULL,max_attempts INTEGER NOT NULL,next_run_at TEXT,last_run_at TEXT,status TEXT NOT NULL DEFAULT 'PENDING',attempts INTEGER NOT NULL DEFAULT 0,duration_ms INTEGER,error TEXT,lock_token TEXT,locked_at TEXT);
CREATE TABLE IF NOT EXISTS data_hub_sync_runs(run_id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,started_at TEXT NOT NULL,completed_at TEXT,status TEXT NOT NULL,attempt INTEGER NOT NULL,result_json TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS automation_worker_heartbeat(worker_id TEXT PRIMARY KEY,seen_at TEXT NOT NULL,database_fingerprint TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS data_hub_sync_batches(batch_id TEXT PRIMARY KEY,started_at TEXT NOT NULL,completed_at TEXT,status TEXT NOT NULL,triggered_by TEXT NOT NULL,jobs_total INTEGER NOT NULL DEFAULT 0,jobs_succeeded INTEGER NOT NULL DEFAULT 0,jobs_failed INTEGER NOT NULL DEFAULT 0,jobs_blocked INTEGER NOT NULL DEFAULT 0,jobs_skipped INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS data_hub_sync_batch_jobs(batch_id TEXT NOT NULL,job_id TEXT NOT NULL,source_id TEXT NOT NULL,provider TEXT NOT NULL,status TEXT NOT NULL,rows_imported INTEGER,duration_ms INTEGER,last_error TEXT,started_at TEXT,completed_at TEXT,PRIMARY KEY(batch_id,job_id));
CREATE INDEX IF NOT EXISTS ix_sync_jobs_due ON sync_jobs(status,next_run_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_data_hub_running_batch ON data_hub_sync_batches(status) WHERE status='RUNNING';
CREATE INDEX IF NOT EXISTS ix_data_hub_batch_jobs ON data_hub_sync_batch_jobs(batch_id,status);
"""

def utcnow(): return datetime.now(timezone.utc)
def iso(value: datetime): return value.astimezone(timezone.utc).isoformat()

class DataHub:
    def __init__(self,path: Path,*,clock: Callable[[],datetime]=utcnow):
        self.path=Path(path); self.clock=clock
        with self.connect() as db: ensure_schema(db,SCHEMA,owner="data_hub")
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
        with self.connect() as db: db.execute("""INSERT INTO data_sources(source_id,source_type,provider,status,enabled,last_attempt_at,last_success_at,last_error,cursor,capabilities_json,stale_after_seconds,rows_imported,last_rows_imported,last_run_id,data_min_at,data_max_at,records_available) VALUES(?,?,?,?,?,NULL,NULL,NULL,NULL,?,?,0,NULL,NULL,NULL,NULL,NULL)
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
                (status.value,diagnostic.occurred_at,int(diagnostic.success),diagnostic.occurred_at,int(diagnostic.success),None if diagnostic.success else diagnostic.message,source_id))
        return diagnostic_id, diagnostic
    def sources(self):
        now=self.clock(); result=[]
        with self.connect() as db:
            rows=db.execute("""SELECT ds.*,MIN(sj.next_run_at) AS next_run_at
                FROM data_sources ds LEFT JOIN sync_jobs sj ON sj.source_id=ds.source_id
                GROUP BY ds.source_id ORDER BY ds.source_id""").fetchall()
        for row in rows:
            item=dict(row); item["enabled"]=bool(item["enabled"]);item["capabilities"]=json.loads(item.pop("capabilities_json"))
            if item["status"]==SourceStatus.NOT_CONFIGURED: fresh=DataFreshness.NOT_CONFIGURED
            elif item["status"] in (SourceStatus.ERROR,): fresh=DataFreshness.ERROR
            elif item["status"]==SourceStatus.DISABLED: fresh=DataFreshness.DISABLED
            elif item["status"] in (SourceStatus.UNSUPPORTED,SourceStatus.UNAVAILABLE): fresh=DataFreshness.UNAVAILABLE
            elif not item["last_run_id"]: fresh=DataFreshness.CONNECTED_NO_DATA
            else: fresh=DataFreshness.FRESH if now-datetime.fromisoformat(item["last_success_at"])<=timedelta(seconds=item["stale_after_seconds"]) else DataFreshness.STALE
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
            runnable_statuses=(SourceStatus.CONNECTED,SourceStatus.UNAVAILABLE,SourceStatus.ERROR) if manual else (SourceStatus.CONNECTED,SourceStatus.UNAVAILABLE)
            if not source or not source["enabled"] or source["status"] not in runnable_statuses: return self._blocked(job_id,"source unavailable")
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
            result=dict(operation(source["cursor"])); end=self.clock(); rows_value=result.get("rows_imported")
            rows_delta=0 if rows_value is None else int(rows_value); last_rows=None if rows_value is None else rows_delta; cursor=result.get("cursor",source["cursor"])
            data_min_at=result.get("data_min_at"); data_max_at=result.get("data_max_at"); records_available=result.get("records_available")
            with self.connect() as db:
                db.execute("UPDATE data_hub_sync_runs SET completed_at=?,status='SUCCEEDED',result_json=? WHERE run_id=?",(iso(end),json.dumps(result),run_id))
                db.execute("UPDATE sync_jobs SET status='SUCCEEDED',next_run_at=?,duration_ms=?,error=NULL,lock_token=NULL WHERE job_id=?",(iso(end+timedelta(seconds=job["interval_seconds"])),int((end-started).total_seconds()*1000),job_id))
                db.execute("UPDATE data_sources SET last_attempt_at=?,last_success_at=?,last_error=NULL,cursor=?,rows_imported=rows_imported+?,last_rows_imported=?,last_run_id=?,data_min_at=CASE WHEN ? IS NULL THEN data_min_at WHEN data_min_at IS NULL OR ? < data_min_at THEN ? ELSE data_min_at END,data_max_at=CASE WHEN ? IS NULL THEN data_max_at WHEN data_max_at IS NULL OR ? > data_max_at THEN ? ELSE data_max_at END,records_available=?,status='CONNECTED' WHERE source_id=?",(iso(end),iso(end),cursor,rows_delta,last_rows,run_id,data_min_at,data_min_at,data_min_at,data_max_at,data_max_at,data_max_at,records_available,job["source_id"]))
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
            else:
                try:
                    results.append(self.run(job["job_id"],operation))
                except Exception:
                    # Contain only failures that ``run`` has already persisted.
                    # Scheduler/SQLite faults raised before that point stay visible
                    # to the worker's outer failure handler instead of disappearing.
                    recorded=self.job(job["job_id"])
                    if not recorded or recorded["status"] not in {SyncStatus.FAILED,SyncStatus.RETRY}:
                        raise
                    results.append(recorded)
        return results
    def run_all(self, operations: Mapping[str, Callable], *, triggered_by="admin", retry_failed=False):
        """Run one isolated, dependency-ordered global batch.

        Every registered job is evidenced. Non-runnable jobs are SKIPPED and an
        exception from one handler is contained so unrelated branches continue.
        """
        batch_id=str(uuid.uuid4()); started=iso(self.clock())
        with self.connect() as db:
            try:
                db.execute("INSERT INTO data_hub_sync_batches(batch_id,started_at,status,triggered_by) VALUES(?,?,'RUNNING',?)",(batch_id,started,str(triggered_by)[:100]))
            except sqlite3.IntegrityError as exc:
                raise BatchAlreadyRunning("BATCH_ALREADY_RUNNING") from exc
            jobs=[dict(row) for row in db.execute("""SELECT sj.*,ds.provider,ds.status AS source_status,ds.enabled
                FROM sync_jobs sj LEFT JOIN data_sources ds ON ds.source_id=sj.source_id ORDER BY sj.job_id""")]
            if retry_failed:
                previous=db.execute("SELECT batch_id FROM data_hub_sync_batches WHERE status!='RUNNING' ORDER BY started_at DESC LIMIT 1").fetchone()
                retry_ids=set() if not previous else {r[0] for r in db.execute("SELECT job_id FROM data_hub_sync_batch_jobs WHERE batch_id=? AND status IN ('FAILED','BLOCKED')",(previous[0],))}
                jobs=[job for job in jobs if job["job_id"] in retry_ids]
            db.execute("UPDATE data_hub_sync_batches SET jobs_total=? WHERE batch_id=?",(len(jobs),batch_id))
        pending={job["job_id"]:job for job in jobs}; completed={}
        while pending:
            progressed=False
            for job_id,job in list(pending.items()):
                dependencies=json.loads(job["dependencies_json"])
                if any(dep in pending for dep in dependencies): continue
                if any(completed.get(dep) in {"FAILED","BLOCKED","SKIPPED"} for dep in dependencies):
                    result=self._record_batch_job(batch_id,job,"BLOCKED",last_error="dependency failed or was skipped")
                elif not job.get("enabled") or job.get("source_status") in {SourceStatus.NOT_CONFIGURED,SourceStatus.DISABLED}:
                    result=self._record_batch_job(batch_id,job,"SKIPPED",last_error=None)
                elif job["job_type"] not in operations:
                    result=self._record_batch_job(batch_id,job,"SKIPPED",last_error="runtime handler missing")
                else:
                    job_started=iso(self.clock())
                    try:
                        state=self.run(job_id,operations[job["job_type"]],manual=True)
                        result=self._record_batch_job(batch_id,job,state["status"],rows_imported=self._last_run_rows(job_id),duration_ms=state.get("duration_ms"),last_error=state.get("error"),started_at=job_started,completed_at=iso(self.clock()))
                    except Exception as exc:
                        from .jobs import sanitize_error
                        state=self.job(job_id) or {}
                        result=self._record_batch_job(batch_id,job,"FAILED",duration_ms=state.get("duration_ms"),last_error=sanitize_error(exc),started_at=job_started,completed_at=iso(self.clock()))
                completed[job_id]=result["status"]; del pending[job_id]; progressed=True
            if not progressed: # dependency cycle or missing dependency
                for job_id,job in list(pending.items()):
                    result=self._record_batch_job(batch_id,job,"BLOCKED",last_error="dependency graph is not satisfiable")
                    completed[job_id]=result["status"]; del pending[job_id]
        return self._complete_batch(batch_id)
    def _last_run_rows(self,job_id):
        with self.connect() as db:
            row=db.execute("SELECT result_json FROM data_hub_sync_runs WHERE job_id=? AND status='SUCCEEDED' ORDER BY run_id DESC LIMIT 1",(job_id,)).fetchone()
        if not row: return None
        value=json.loads(row[0] or "{}").get("rows_imported")
        return None if value is None else int(value)
    def _record_batch_job(self,batch_id,job,status,*,rows_imported=None,duration_ms=None,last_error=None,started_at=None,completed_at=None):
        from .jobs import sanitize_error
        error=sanitize_error(RuntimeError(last_error)) if last_error else None
        now=iso(self.clock()); started_at=started_at or now; completed_at=completed_at or now
        with self.connect() as db:
            db.execute("""INSERT INTO data_hub_sync_batch_jobs(batch_id,job_id,source_id,provider,status,rows_imported,duration_ms,last_error,started_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",(batch_id,job["job_id"],job["source_id"],job.get("provider") or "UNKNOWN",status,rows_imported,duration_ms,error,started_at,completed_at))
        return {"job_id":job["job_id"],"source_id":job["source_id"],"provider":job.get("provider") or "UNKNOWN","status":status,"rows_imported":rows_imported,"duration_ms":duration_ms,"last_error":error,"started_at":started_at,"completed_at":completed_at}
    def _complete_batch(self,batch_id):
        with self.connect() as db:
            counts={row[0]:row[1] for row in db.execute("SELECT status,COUNT(*) FROM data_hub_sync_batch_jobs WHERE batch_id=? GROUP BY status",(batch_id,))}
            succeeded=counts.get("SUCCEEDED",0); failed=counts.get("FAILED",0); blocked=counts.get("BLOCKED",0); skipped=counts.get("SKIPPED",0)
            status="SUCCEEDED" if not failed and not blocked else "FAILED" if not succeeded else "PARTIAL"
            db.execute("""UPDATE data_hub_sync_batches SET completed_at=?,status=?,jobs_succeeded=?,jobs_failed=?,jobs_blocked=?,jobs_skipped=? WHERE batch_id=?""",(iso(self.clock()),status,succeeded,failed,blocked,skipped,batch_id))
        return self.sync_batch(batch_id)
    def sync_batch(self,batch_id):
        with self.connect() as db:
            batch=db.execute("SELECT * FROM data_hub_sync_batches WHERE batch_id=?",(batch_id,)).fetchone()
            if not batch: return None
            jobs=[dict(row) for row in db.execute("SELECT job_id,source_id,provider,status,rows_imported,duration_ms,last_error,started_at,completed_at FROM data_hub_sync_batch_jobs WHERE batch_id=? ORDER BY started_at,job_id",(batch_id,))]
        data=dict(batch); data["jobs"]=jobs; data["summary"]={key:data[key] for key in ("jobs_total","jobs_succeeded","jobs_failed","jobs_blocked","jobs_skipped")}
        return data
    def latest_batch(self):
        with self.connect() as db: row=db.execute("SELECT batch_id FROM data_hub_sync_batches ORDER BY started_at DESC LIMIT 1").fetchone()
        return self.sync_batch(row[0]) if row else None
    def database_fingerprint(self):
        return hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:12]
    def heartbeat(self,worker_id="automation-worker"):
        with self.connect() as db: db.execute("INSERT INTO automation_worker_heartbeat VALUES(?,?,?) ON CONFLICT(worker_id) DO UPDATE SET seen_at=excluded.seen_at,database_fingerprint=excluded.database_fingerprint",(worker_id,iso(self.clock()),self.database_fingerprint()))
    def worker_state(self,*,stale_after_seconds=120):
        fingerprint=self.database_fingerprint(); now=self.clock()
        with self.connect() as db: row=db.execute("SELECT * FROM automation_worker_heartbeat ORDER BY seen_at DESC LIMIT 1").fetchone()
        if not row: return WorkerState.MISSING.value
        if row["database_fingerprint"]!=fingerprint: return WorkerState.DATABASE_MISMATCH.value
        if now-datetime.fromisoformat(row["seen_at"])>timedelta(seconds=stale_after_seconds): return WorkerState.STALE.value
        return WorkerState.HEALTHY.value
    def runtime(self,operations=None):
        with self.connect() as db: heartbeats=[dict(row) for row in db.execute("SELECT * FROM automation_worker_heartbeat ORDER BY worker_id")]
        registered=set((operations or {}).keys())
        return {"database_fingerprint":self.database_fingerprint(),"worker_state":self.worker_state(),"worker_heartbeats":heartbeats,"jobs":[{"job_id":j["job_id"],"handler_registered":j["job_type"] in registered} for j in self.jobs()]}
    def production_evidence(self):
        from .production_evidence import coverage_contract
        keys=("provider","source_id","configuration","connectivity","freshness","last_attempt_at","last_success_at","last_rows_imported","rows_imported","data_min_at","data_max_at","records_available","last_error","next_run_at","last_run_id")
        out=[]
        for s in self.sources():
            item={"provider":s["provider"],"source_id":s["source_id"],"configuration":"CONFIGURED" if s["enabled"] and s["status"]!=SourceStatus.NOT_CONFIGURED else "NOT_CONFIGURED","connectivity":s["status"],"freshness":s["freshness"]}
            item.update({k:s.get(k) for k in keys if k not in item})
            out.append(coverage_contract(item))
        return {"sources":out}
    def health(self):
        sources=self.sources()
        for source in sources:
            source["last_diagnostic"]=(self.diagnostics.recent(source["source_id"],1) or [None])[0]
            source["failure_history"]=self.diagnostics.recent(source["source_id"],5)
        return {"status":"ERROR" if any(s["freshness"]=="ERROR" for s in sources) else "DEGRADED" if any(s["freshness"]!="FRESH" for s in sources) else "OK","sources":sources,"jobs":self.jobs()}
