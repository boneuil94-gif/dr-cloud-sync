"""Secret-safe, structured diagnostics shared by web and worker runtimes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from contextlib import closing
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class ErrorCategory(StrEnum):
    AUTH="AUTH"; HTTP="HTTP"; TIMEOUT="TIMEOUT"; NETWORK="NETWORK"; PARSING="PARSING"
    VALIDATION="VALIDATION"; CONFIGURATION="CONFIGURATION"; PERSISTENCE="PERSISTENCE"; UNKNOWN="UNKNOWN"


SENSITIVE=re.compile(r"(?i)(authorization|proxy-authorization|cookie|set-cookie|api[-_]?key|(?:access[-_]?)?token|refresh[-_]?token|password|passwd|secret|credential)")
TOKEN_PATTERNS=(
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),"Bearer [REDACTED]"),
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]+"),"Basic [REDACTED]"),
    (re.compile(r'(?i)(["\']?(?:api[-_]?key|token|password|secret|cookie)["\']?\s*[:=]\s*)["\']?[^\s,;}"\']+'),r'\1[REDACTED]'),
)


def sanitize(value: Any, *, limit: int=1000) -> str | None:
    """Redact recursively before limiting; never serialize request headers."""
    if value is None: return None
    if isinstance(value,(dict,list,tuple)):
        def clean(item):
            if isinstance(item,dict): return {str(k):("[REDACTED]" if SENSITIVE.search(str(k)) else clean(v)) for k,v in item.items()}
            if isinstance(item,(list,tuple)): return [clean(v) for v in item]
            return item
        text=json.dumps(clean(value),ensure_ascii=False,separators=(",",":"))
    else: text=str(value)
    for pattern,replacement in TOKEN_PATTERNS: text=pattern.sub(replacement,text)
    return text[:limit]


def safe_path(url_or_path: str | None) -> str | None:
    if not url_or_path: return None
    parts=urlsplit(str(url_or_path)); path=parts.path or "/"
    safe_query=urlencode([(k,"[REDACTED]" if SENSITIVE.search(k) else v) for k,v in parse_qsl(parts.query,keep_blank_values=True)])
    return urlunsplit(("","",path,safe_query,""))[:500]


@dataclass(frozen=True)
class ConnectorDiagnostic:
    source_id: str; provider: str; operation: str; stage: str
    category: str; message: str; occurred_at: str
    job_id: str|None=None; run_id: int|None=None; endpoint_path: str|None=None
    http_status: int|None=None; response_excerpt: str|None=None; exception_type: str|None=None
    attempt: int=1; duration_ms: int|None=None; cursor: str|None=None
    request_id: str|None=None; next_retry_at: str|None=None; success: bool=False


SCHEMA="""
CREATE TABLE IF NOT EXISTS connector_diagnostics(
 diagnostic_id INTEGER PRIMARY KEY AUTOINCREMENT,source_id TEXT NOT NULL,provider TEXT NOT NULL,
 job_id TEXT,run_id INTEGER,operation TEXT NOT NULL,stage TEXT NOT NULL,endpoint_path TEXT,http_status INTEGER,
 category TEXT NOT NULL,message TEXT NOT NULL,response_excerpt TEXT,exception_type TEXT,attempt INTEGER NOT NULL,
 occurred_at TEXT NOT NULL,duration_ms INTEGER,cursor TEXT,request_id TEXT,next_retry_at TEXT,success INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_connector_diagnostics_source ON connector_diagnostics(source_id,occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_connector_diagnostics_run ON connector_diagnostics(run_id);
"""


class DiagnosticRepository:
    def __init__(self,path: Path):
        self.path=Path(path)
        with closing(self.connect()) as db:
            db.executescript(SCHEMA); db.commit()
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;return db
    def add(self,item: ConnectorDiagnostic) -> int:
        values=asdict(item); values["success"]=int(item.success)
        with closing(self.connect()) as db:
            cur=db.execute(f"INSERT INTO connector_diagnostics({','.join(values)}) VALUES({','.join('?' for _ in values)})",tuple(values.values()))
            db.commit()
            return int(cur.lastrowid)
    def recent(self,source_id: str|None=None,limit: int=10,*,failures_only=True):
        limit=max(1,min(int(limit),50)); where=[];args=[]
        if source_id: where.append("source_id=?");args.append(source_id)
        if failures_only: where.append("success=0")
        sql="SELECT * FROM connector_diagnostics"+(" WHERE "+" AND ".join(where) if where else "")+" ORDER BY diagnostic_id DESC LIMIT ?";args.append(limit)
        with closing(self.connect()) as db: rows=db.execute(sql,args).fetchall()
        return [{**dict(r),"success":bool(r["success"])} for r in rows]


def from_exception(*,source_id,provider,operation,stage,exc,job_id=None,run_id=None,
                   attempt=1,duration_ms=None,cursor=None,request_id=None,next_retry_at=None):
    context=getattr(exc,"diagnostic",{}) or {}; cause=exc.__cause__ or exc
    status=context.get("http_status") or (cause.code if isinstance(cause,HTTPError) else None)
    response=context.get("response_excerpt")
    if response is None and isinstance(cause,HTTPError):
        try: response=cause.read(4096).decode("utf-8",errors="replace")
        except Exception: response=None
    category=context.get("category")
    if not category:
        category="AUTH" if status in (401,403) else "HTTP" if status else "TIMEOUT" if isinstance(cause,TimeoutError) else "NETWORK" if isinstance(cause,(URLError,OSError)) else "PARSING" if isinstance(cause,(json.JSONDecodeError,UnicodeDecodeError)) else "UNKNOWN"
    return ConnectorDiagnostic(source_id,provider,context.get("operation",operation),context.get("stage",stage),category,
        sanitize(exc,limit=500) or "Erreur connecteur",datetime.now(timezone.utc).isoformat(),job_id,run_id,
        safe_path(context.get("endpoint_path")),status,sanitize(response),type(cause).__name__,attempt,duration_ms,
        sanitize(cursor,limit=300),sanitize(request_id,limit=200),next_retry_at,False)
