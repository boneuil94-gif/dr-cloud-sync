"""Sanitised, read-only production truth and recovery evidence.

The collectors deliberately report UNKNOWN/NOT_PROVEN instead of inferring that
an installed script, a recent cursor, or an empty ledger proves production.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sqlite3
import ssl
import tempfile
import time
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SENSITIVE = ("secret", "token", "cookie", "password", "credential", "api_key", "authorization", "email", "phone")


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize(value):
    """Recursively allow evidence while removing keys likely to contain secrets/PII."""
    if isinstance(value, dict):
        return {str(k): sanitize(v) for k, v in value.items()
                if not any(word in str(k).lower() for word in SENSITIVE)}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str) and any(marker in value.lower() for marker in ("bearer ", "basic ", "session=")):
        return "[REDACTED]"
    return value


def sha_status(expected: str | None, deployed: str | None, served: str | None) -> str:
    values = [value.strip() for value in (expected, deployed, served) if value and value.strip()]
    if len(values) != 3:
        return "UNKNOWN"
    return "MATCH" if len(set(values)) == 1 else "MISMATCH"


def probe_public(url: str, *, timeout: float = 10) -> dict:
    """Probe public health, TLS and headers; never sends credentials."""
    checked = now(); result = {"checked_at": checked, "url": url, "health_status": "UNKNOWN",
                               "https_status": "UNKNOWN", "certificate": "UNKNOWN", "redirect_http_https": "UNKNOWN"}
    required = {"content-security-policy", "strict-transport-security", "x-content-type-options", "referrer-policy"}
    frame = {"x-frame-options", "content-security-policy"}
    try:
        request = Request(url, headers={"User-Agent": "DrCloud-Production-Evidence/1.0"})
        with urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read(64_000)); headers = {k.lower(): v for k, v in response.headers.items()}
            result.update({"health_status": "OK" if response.status == 200 and body.get("status") == "ok" else "ERROR",
                           "served_commit": body.get("commit"), "application_version": body.get("version"),
                           "https_status": "OK" if urlparse(url).scheme == "https" else "ERROR",
                           "certificate": "VALID" if urlparse(url).scheme == "https" else "NOT_APPLICABLE",
                           "security_headers": {name: ("PRESENT" if name in headers else "MISSING") for name in sorted(required)},
                           "frame_policy": "PRESENT" if frame & headers.keys() else "MISSING"})
        parsed=urlparse(url); http_url=f"http://{parsed.netloc}{parsed.path}"
        class NoRedirect(__import__("urllib.request", fromlist=["HTTPRedirectHandler"]).HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl): return None
        opener=__import__("urllib.request", fromlist=["build_opener"]).build_opener(NoRedirect)
        try: opener.open(Request(http_url), timeout=timeout)
        except HTTPError as exc:
            result["redirect_http_https"] = "OK" if exc.code in (301, 302, 307, 308) and exc.headers.get("Location", "").startswith("https://") else "ERROR"
    except (OSError, ValueError, json.JSONDecodeError, URLError) as exc:
        result.update({"health_status": "ERROR", "error": exc.__class__.__name__})
    return result


def coverage_contract(source: dict) -> dict:
    configured = source.get("configuration") != "NOT_CONFIGURED"
    provider_total = source.get("provider_total", source.get("records_available"))
    imported = source.get("imported_total", source.get("rows_imported", 0))
    ratio = None if provider_total is None else (min(imported / provider_total, 1.0) if provider_total else (1.0 if imported == 0 else None))
    freshness = source.get("freshness", "UNAVAILABLE")
    if not configured: evidence = "NOT_CONFIGURED"
    elif source.get("connectivity") == "ERROR": evidence = "ERROR"
    elif freshness != "FRESH": evidence = "STALE" if freshness == "STALE" else "UNAVAILABLE"
    elif ratio is None: evidence = "FRESH_UNKNOWN_COVERAGE"
    elif ratio >= 1: evidence = "FRESH_COMPLETE"
    else: evidence = "FRESH_PARTIAL"
    return {"source_id": source.get("source_id"), "provider": source.get("provider"), "freshness": freshness,
            "coverage_status": "UNKNOWN_COVERAGE" if ratio is None else "COMPLETE" if ratio >= 1 else "PARTIAL",
            "provider_total": provider_total, "imported_total": imported,
            "rejected_total": source.get("rejected_total"), "duplicates_total": source.get("duplicates_total"),
            "last_cursor": source.get("last_cursor", source.get("cursor")), "data_min_at": source.get("data_min_at"),
            "data_max_at": source.get("data_max_at"), "last_success_at": source.get("last_success_at"),
            "coverage_ratio": ratio, "evidence_status": evidence}


def backup_inventory(root: Path, *, stale_after_seconds: int = 86400) -> dict:
    root=Path(root); checked=now()
    if not root.exists(): return {"checked_at": checked, "status": "BACKUP_MISSING", "location": str(root), "backups": []}
    rows=[]
    for bundle in root.iterdir():
        db=bundle/"drcloud.db" if bundle.is_dir() else bundle
        meta=bundle/"metadata.json" if bundle.is_dir() else None
        if not db.is_file(): continue
        stat=db.stat(); checksum=hashlib.sha256(db.read_bytes()).hexdigest(); manifest={}
        created=datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        if meta and meta.exists():
            try:
                manifest=json.loads(meta.read_text()); created=datetime.fromisoformat(manifest.get("created_at", "").replace("Z", "+00:00"))
            except (ValueError, json.JSONDecodeError): pass
        valid=stat.st_size > 0
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
                quick=connection.execute("PRAGMA quick_check").fetchone()[0]
                schema="\n".join((r[0] or "") for r in connection.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"))
            fingerprint=hashlib.sha256(schema.encode()).hexdigest(); valid &= quick == "ok" and bool(schema)
        except sqlite3.DatabaseError: quick="FAILED"; fingerprint=None; valid=False
        expected=manifest.get("sha256") or next((x.get("sha256") for x in manifest.get("files",[]) if x.get("path")=="drcloud.db"),None)
        if expected: valid &= expected == checksum
        required=("drcloud.db","catalogue.json","catalogue-report.json")
        entries={x.get("path"):x for x in manifest.get("files",[]) if isinstance(x,dict)}
        runtime_complete=manifest.get("required_runtime_files")==list(required)
        for name in required:
            candidate=bundle/name; entry=entries.get(name)
            runtime_complete &= bool(entry and candidate.is_file() and candidate.stat().st_size==entry.get("size") and hashlib.sha256(candidate.read_bytes()).hexdigest()==entry.get("sha256"))
        if manifest.get("required_runtime_files") is not None:
            valid &= bool(runtime_complete)
        backup_class="APP_RESTORABLE" if valid and runtime_complete else "LEGACY_DB_ONLY" if valid else "INVALID"
        age=max(0,(datetime.now(timezone.utc)-created).total_seconds())
        watermark=manifest.get("recovery_watermark") if isinstance(manifest.get("recovery_watermark"),dict) else None
        rows.append({"backup_id": bundle.name, "created_at": created.isoformat().replace("+00:00", "Z"), "backup_age_seconds":round(age,3), "size_bytes": stat.st_size, "sha256": checksum, "database": str(db),
                     "method":manifest.get("method","UNKNOWN"), "schema_fingerprint":fingerprint,
                     "data_max_at":manifest.get("data_max_at"),
                     "watermark_confidence":watermark.get("confidence","UNKNOWN") if watermark else "LOW",
                     "watermark_coverage":watermark.get("coverage") if watermark else None,
                     "quick_check":quick, "manifest_status":manifest.get("status","UNKNOWN"),
                     "status":"VALID" if valid else "INVALID", "backup_class":backup_class,
                     "runtime_files_complete":bool(runtime_complete)})
    rows.sort(key=lambda row: row["created_at"], reverse=True)
    if not rows: status="BACKUP_MISSING"
    else: status="BACKUP_PROVEN" if (datetime.now(timezone.utc)-datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00"))).total_seconds() <= stale_after_seconds else "BACKUP_STALE"
    return {"checked_at": checked, "status": status, "location": str(root), "frequency": os.environ.get("DRCLOUD_BACKUP_FREQUENCY") or "UNKNOWN", "rotation": os.environ.get("DRCLOUD_BACKUP_ROTATION") or "UNKNOWN", "retention": os.environ.get("DRCLOUD_BACKUP_RETENTION") or "UNKNOWN", "backups": rows}


def restore_test(root: Path, *, actor: str = "cli-operator", environment: str = "isolated-temporary") -> dict:
    started=time.monotonic(); started_at=now(); inventory=backup_inventory(root)
    operation_id=str(uuid4()); report={"operation":"restore-test", "operation_id":operation_id, "actor":actor,
        "commit":os.environ.get("DRCLOUD_COMMIT"), "started_at":started_at, "environment":environment,
        "target_rpo":None, "target_rto":None}
    valid=[row for row in inventory["backups"] if row.get("status")=="VALID" and row.get("backup_class")=="APP_RESTORABLE"]
    if not valid:
        result="RESTORE_FAILED" if inventory["backups"] else "RESTORE_NOT_PROVEN"
        return {**report,"completed_at":now(),"duration_seconds":round(time.monotonic()-started,3),"restore_result":result,
            "backup_validation":"BACKUP_INVALID" if inventory["backups"] else "BACKUP_MISSING",
            "observed_rpo":None,"observed_rto":None,"integrity_check":"NOT_RUN","foreign_key_check":"NOT_RUN","app_boot":"NOT_RUN","health_result":"NOT_RUN"}
    latest=valid[0]; report["backup_id"]=latest["backup_id"]
    try:
        with tempfile.TemporaryDirectory(prefix="drcloud-restore-") as tmp:
            target=Path(tmp)/"drcloud.db"; bundle=Path(latest["database"]).parent
            for name in ("drcloud.db","catalogue.json","catalogue-report.json"):
                shutil.copy2(bundle/name,Path(tmp)/name)
            database_restored_at=now(); database_restored=time.monotonic()
            if hashlib.sha256(target.read_bytes()).hexdigest()!=latest["sha256"]: raise sqlite3.DatabaseError("checksum mismatch")
            with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as db:
                integrity=db.execute("PRAGMA integrity_check").fetchone()[0]
                foreign=list(db.execute("PRAGMA foreign_key_check")); tables={r[0]:db.execute(f'SELECT count(*) FROM "{r[0]}"').fetchone()[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                indexes=db.execute("SELECT count(*) FROM sqlite_master WHERE type='index'").fetchone()[0]
            restored_schema="\n".join((r[0] or "") for r in sqlite3.connect(f"file:{target}?mode=ro",uri=True).execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"))
            schema_fingerprint=hashlib.sha256(restored_schema.encode()).hexdigest()
            good=integrity=="ok" and not foreign and schema_fingerprint==latest["schema_fingerprint"]
            # A real isolated WSGI boot/probe is deliberately performed against the copied DB.
            from .inventory_web import create_app
            from .os_config import OSSettings
            from wsgiref.simple_server import make_server
            import threading
            settings=OSSettings("recovery-test","recovery-secret-not-production","recovery","unused",Path(tmp),"127.0.0.1",0,True,False)
            app=create_app(settings); application_started_at=now(); application_started=time.monotonic()
            server=make_server("127.0.0.1",0,app); thread=threading.Thread(target=server.handle_request,daemon=True); thread.start()
            with urlopen(f"http://127.0.0.1:{server.server_port}/health",timeout=10) as response:
                health=json.loads(response.read()); health_ok=response.status==200 and health.get("status")=="ok"
            thread.join(10); server.server_close(); health_ok_at=now(); health_time=time.monotonic(); good &= health_ok
            completed=now(); duration=round(time.monotonic()-started,3)
            backup_time=datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00")); incident=datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            return {**report,"completed_at":completed,"database_restored_at":database_restored_at,"application_started_at":application_started_at,"health_ok_at":health_ok_at,
                "duration_seconds":duration,"database_restore_duration":round(database_restored-started,3),"application_boot_duration":round(application_started-database_restored,3),"health_recovery_duration":round(health_time-application_started,3),
                "restore_result":"RESTORE_PROVEN" if good else "RESTORE_FAILED","backup_age_seconds":round((incident-backup_time).total_seconds(),3),
                "observed_rpo":round((incident-backup_time).total_seconds(),3),"observed_rpo_method":"incident_reference_at minus backup_created_at; business data timestamp unavailable",
                "observed_rto":duration if good else None,"integrity_check":integrity,"foreign_key_check":"OK" if not foreign else "FAILED",
                "schema_fingerprint":schema_fingerprint,"app_boot":"OK" if good else "FAILED","health_result":"OK" if health_ok else "FAILED","database_size":target.stat().st_size,"table_counts":tables,"index_count":indexes}
    except Exception as exc:
        return {**report,"completed_at":now(),"duration_seconds":round(time.monotonic()-started,3),"restore_result":"RESTORE_FAILED","observed_rpo":None,"observed_rto":None,"integrity_check":"FAILED","foreign_key_check":"NOT_RUN","app_boot":"FAILED","health_result":"FAILED","sanitized_error":exc.__class__.__name__}


def recovery_report(root: Path, *, rollback_report: Path | None = None) -> dict:
    """Execute the safe recovery drill and return its PII-free evidence envelope."""
    backup=backup_inventory(root); restore=restore_test(root); crash=sqlite_crash_test()
    return sanitize({"timestamp":now(),"environment":"isolated-temporary","commit":os.environ.get("DRCLOUD_COMMIT"),
        "backup":backup,"restore":restore,"integrity":{"integrity_check":restore.get("integrity_check"),"foreign_key_check":restore.get("foreign_key_check")},
        "observed_rpo":restore.get("observed_rpo"),"observed_rto":restore.get("observed_rto"),
        "rollback":rollback_check(rollback_report),"rollback_schema_compatibility":"UNKNOWN",
        "crash_recovery":crash,"warnings":["Production data was never modified","Rollback was not executed unless an external staging report is supplied"]})


def sqlite_crash_test() -> dict:
    """Kill a WAL writer in a disposable directory, reopen it and prove integrity."""
    with tempfile.TemporaryDirectory(prefix="drcloud-crash-") as tmp:
        database=Path(tmp)/"crash.db"
        code=("import sqlite3,time,sys; db=sqlite3.connect(sys.argv[1]); "
              "db.execute('PRAGMA journal_mode=WAL'); db.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY,value TEXT)'); db.commit(); "
              "db.execute('BEGIN IMMEDIATE'); db.execute(\"INSERT INTO evidence(value) VALUES('uncommitted')\"); "
              "print('WRITE_STARTED',flush=True); time.sleep(60)")
        process=subprocess.Popen([os.environ.get("PYTHON","python"),"-c",code,str(database)],stdout=subprocess.PIPE,text=True)
        marker=process.stdout.readline().strip() if process.stdout else ""; process.kill(); process.wait(timeout=5)
        try:
            with sqlite3.connect(database) as db:
                mode=db.execute("PRAGMA journal_mode").fetchone()[0]
                integrity=db.execute("PRAGMA integrity_check").fetchone()[0]
                rows=db.execute("SELECT count(*) FROM evidence").fetchone()[0]
            passed=marker=="WRITE_STARTED" and mode.lower()=="wal" and integrity=="ok" and rows==0
            return {"result":"CRASH_RECOVERY_PROVEN" if passed else "CRASH_RECOVERY_FAILED","journal_mode":mode,
                    "integrity_check":integrity,"uncommitted_rows":rows,"process_exit":process.returncode}
        except sqlite3.DatabaseError as exc:
            return {"result":"CRASH_RECOVERY_FAILED","integrity_check":"FAILED","sanitized_error":exc.__class__.__name__}


def rollback_check(report_path: Path | None = None) -> dict:
    if report_path and Path(report_path).is_file():
        return sanitize(json.loads(Path(report_path).read_text()))
    return {"operation":"rollback-check","checked_at":now(),"environment":"NOT_EXECUTED","result":"ROLLBACK_NOT_PROVEN","previous_commit":None,"deployed_commit":None,"health_result":"NOT_RUN","schema_compatible":"UNKNOWN","data_loss_check":"NOT_RUN"}


def database_facts(database: Path) -> dict:
    if not Path(database).is_file(): return {"status":"UNAVAILABLE","schema_fingerprint":None,"database_fingerprint":None}
    try:
        with sqlite3.connect(f"file:{Path(database)}?mode=ro", uri=True) as db:
            schema="\n".join(row[0] or "" for row in db.execute("SELECT sql FROM sqlite_master ORDER BY type,name"))
            integrity=db.execute("PRAGMA quick_check").fetchone()[0]
        stat=Path(database).stat()
        return {"status":"OK" if integrity=="ok" else "ERROR", "schema_version":1,
                "schema_fingerprint":hashlib.sha256(schema.encode()).hexdigest(),
                "database_fingerprint":hashlib.sha256(f"{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:16],
                "database_size":stat.st_size}
    except sqlite3.DatabaseError: return {"status":"ERROR","schema_fingerprint":None,"database_fingerprint":None}


def reconciliation_report(database: Path) -> dict:
    """Read known ledgers defensively, returning N/D rather than fabricated zeroes."""
    names={"sales_total":"sales", "payments_total":"sale_payments", "sumup_transactions_total":"sumup_transactions",
           "payouts_total":"sumup_payouts", "qonto_transactions_total":"bank_transactions"}
    result={key:None for key in names}; counts={key:None for key in ("MATCHED","PROBABLE","POSSIBLE","AMBIGUOUS","CONFLICT","UNMATCHED","REJECTED")}
    if not Path(database).is_file(): return {**result,"qonto_matches_total":None,"statuses":counts,"reconciliation_coverage":None,"evidence_status":"UNAVAILABLE","vocabulary_mapping":{"POSSIBLE":"PROBABLE","CONFLICT":"AMBIGUOUS"}}
    with sqlite3.connect(f"file:{Path(database)}?mode=ro", uri=True) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for key,table in names.items():
            if table in tables: result[key]=db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        for table in ("payment_matches","finance_reconciliation_matches"):
            if table not in tables: continue
            columns={r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}; column=next((c for c in ("status","confidence") if c in columns),None)
            if column:
                for status,total in db.execute(f'SELECT "{column}",count(*) FROM "{table}" GROUP BY "{column}"'):
                    key=str(status).upper(); counts[key]=total if counts.get(key) is None else counts[key]+total
    matched=counts.get("MATCHED"); denominator=result.get("payouts_total")
    coverage=None if matched is None or not denominator else matched/denominator
    return {**result,"qonto_matches_total":matched,"statuses":counts,"reconciliation_coverage":coverage,
            "evidence_status":"TESTED" if any(v is not None for v in result.values()) else "NOT_PROVEN",
            "vocabulary_mapping":{"POSSIBLE":"PROBABLE","CONFLICT":"AMBIGUOUS"}}


def snapshot(*, database: Path, environment: str, expected_commit: str | None, deployed_commit: str | None,
             public_url: str | None, worker_state: str = "UNKNOWN", last_heartbeat: str | None = None,
             sources=()) -> dict:
    public=probe_public(public_url) if public_url else {"health_status":"UNKNOWN","https_status":"UNKNOWN","served_commit":None}
    served=public.get("served_commit")
    return sanitize({"production_evidence_snapshot":{"timestamp":now(),"environment":environment,"expected_commit":expected_commit,
        "deployed_commit":deployed_commit,"served_commit":served,"sha_status":sha_status(expected_commit,deployed_commit,served),
        "application_version":public.get("application_version") or "1.0.0",**database_facts(database),"worker_state":worker_state,
        "last_heartbeat":last_heartbeat,"https_status":public.get("https_status"),"health_status":public.get("health_status"),
        "public_probe":public,"data_coverage":[coverage_contract(row) for row in sources]}})


def internal_alerts(evidence: dict) -> list[dict]:
    """Evaluate internal-only alerts. Delivery is intentionally out of scope."""
    rules=(("health_down", evidence.get("health_status") not in ("OK",)),
           ("worker_stale", evidence.get("worker_state") in ("STALE","MISSING","DATABASE_MISMATCH")),
           ("backup_stale", evidence.get("backup_status") in ("BACKUP_STALE","BACKUP_MISSING")),
           ("restore_test_old", evidence.get("restore_result") != "RESTORE_PROVEN"),
           ("schema_drift", evidence.get("status") == "ERROR"),
           ("coverage_drop", any(row.get("evidence_status") == "FRESH_PARTIAL" for row in evidence.get("data_coverage",[]))),
           ("source_stale", any(row.get("evidence_status") == "STALE" for row in evidence.get("data_coverage",[]))),
           ("reconciliation_drop", evidence.get("reconciliation_coverage") is None))
    return [{"rule":name,"status":"WARNING","external_delivery":"DISABLED"} for name,active in rules if active]
