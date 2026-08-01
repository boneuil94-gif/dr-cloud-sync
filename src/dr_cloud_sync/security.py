"""Security persistence, password hashing, RBAC and secret-safe auditing.

The schema is deliberately additive: the historic ``local_credentials`` row is
kept and attached to the bootstrap administrator.  Secret *values* never enter
these tables.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, hmac, json, re, secrets, sqlite3, uuid
from pathlib import Path
from typing import Any

ADMIN_ACCOUNT_ID = "local-admin"
PBKDF2_ITERATIONS = 600_000
USER_STATUSES = {"ACTIVE", "DISABLED", "LOCKED"}
ROLES = ("ADMIN", "MANAGER", "STAFF", "READ_ONLY")

PERMISSIONS = {
    "catalogue.read", "catalogue.write", "stock.read", "stock.write", "stock.validate",
    "sales.read", "sales.sync", "sales.map", "finance.read", "bank.read", "bank.sync",
    "purchasing.read", "purchasing.write", "marketing.read", "marketing.generate",
    "marketing.approve", "marketing.schedule", "admin.read", "admin.write",
    "security.read", "security.manage_users", "security.manage_roles",
    "security.manage_secrets", "settings.read", "settings.write", "backup.manage",
}
ROLE_PERMISSIONS = {
    "ADMIN": PERMISSIONS,
    "MANAGER": PERMISSIONS - {"security.manage_users", "security.manage_roles", "security.manage_secrets", "settings.write", "backup.manage", "bank.sync"},
    "STAFF": {"catalogue.read", "stock.read", "stock.write", "sales.read", "purchasing.read"},
    "READ_ONLY": {"catalogue.read", "stock.read", "sales.read", "purchasing.read", "marketing.read"},
}
SENSITIVE = re.compile(r"(?i)(password|passwd|secret|token|api.?key|authorization|cookie|credential)")

@dataclass(frozen=True)
class SettingDefinition:
    key: str; value_type: str; default: Any; category: str; description: str
    runtime_editable: bool = True; choices: tuple[Any, ...] = ()

SETTING_REGISTRY = {
    item.key: item for item in (
        SettingDefinition("safe_mode", "bool", True, "security", "Bloque les opérations externes destructrices."),
        SettingDefinition("data_hub.freshness_minutes", "int", 60, "data_hub", "Seuil de fraîcheur du Data Hub."),
        SettingDefinition("stock.low_threshold", "int", 5, "stock", "Seuil d’alerte de stock faible."),
        SettingDefinition("alerts.cooldown_minutes", "int", 30, "alerts", "Délai minimal entre deux alertes."),
        SettingDefinition("dashboard.compact", "bool", False, "dashboard", "Active l’affichage compact du tableau de bord."),
    )
}

def _now() -> str: return datetime.now(timezone.utc).isoformat()

def sanitise(value: Any) -> Any:
    """Recursively redact credential-shaped fields before logging/auditing."""
    if isinstance(value, dict): return {str(k): "[REDACTED]" if SENSITIVE.search(str(k)) else sanitise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [sanitise(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"(?i)(password|secret|token|api.?key|authorization|cookie)(\s*[:=]\s*)[^\s,;}]+", r"\1\2[REDACTED]", value)
    return value

def validate_password(password: str, username: str = "") -> None:
    common={"password1234", "motdepasse123", "administrateur", "drcloud123456", "azerty123456", "123456789012"}
    if len(password) < 14: raise ValueError("Le mot de passe doit contenir au moins 14 caractères.")
    if password.casefold() in common or (username and username.casefold() in password.casefold()): raise ValueError("Le mot de passe est trop facile à deviner.")
    if len(set(password)) < 6: raise ValueError("Le mot de passe manque de diversité.")

def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt=salt or secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"

def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm,rounds,salt,expected=encoded.split("$",3)
        if algorithm!="pbkdf2_sha256": return False
        actual=hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),int(rounds))
        return hmac.compare_digest(actual,bytes.fromhex(expected))
    except (ValueError,TypeError): return False

@dataclass(frozen=True)
class Credential:
    account_id: str; password_hash: str; password_changed_at: str; session_version: int

class SecurityStore:
    """Single durable authority for identities, grants, sessions and audit."""
    def __init__(self,database: Path,bootstrap_username: str,bootstrap_password: str):
        self.database=database; self.db=sqlite3.connect(database,check_same_thread=False,timeout=10)
        self.db.row_factory=sqlite3.Row; self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS local_credentials(account_id TEXT PRIMARY KEY,password_hash TEXT NOT NULL,password_changed_at TEXT NOT NULL,session_version INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS security_users(user_id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE COLLATE NOCASE,display_name TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('ACTIVE','DISABLED','LOCKED')),credential_ref TEXT NOT NULL UNIQUE REFERENCES local_credentials(account_id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_login_at TEXT,locked_until TEXT);
        CREATE TABLE IF NOT EXISTS security_roles(role_id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,description TEXT NOT NULL,system INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS security_permissions(permission_id TEXT PRIMARY KEY,description TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS security_role_permissions(role_id TEXT NOT NULL REFERENCES security_roles(role_id),permission_id TEXT NOT NULL REFERENCES security_permissions(permission_id),PRIMARY KEY(role_id,permission_id));
        CREATE TABLE IF NOT EXISTS security_user_roles(user_id TEXT NOT NULL REFERENCES security_users(user_id),role_id TEXT NOT NULL REFERENCES security_roles(role_id),assigned_at TEXT NOT NULL,assigned_by TEXT NOT NULL,PRIMARY KEY(user_id,role_id));
        CREATE TABLE IF NOT EXISTS security_sessions(session_id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES security_users(user_id),session_version INTEGER NOT NULL,created_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,expires_at TEXT NOT NULL,revoked_at TEXT,remote_address TEXT);
        CREATE INDEX IF NOT EXISTS idx_security_sessions_user ON security_sessions(user_id,revoked_at,expires_at);
        CREATE TABLE IF NOT EXISTS audit_logs(audit_id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,actor_id TEXT,action TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT,request_id TEXT,source TEXT,metadata_json TEXT NOT NULL,success INTEGER NOT NULL CHECK(success IN (0,1)));
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id,timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action,timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type,entity_id,timestamp DESC);
        CREATE TABLE IF NOT EXISTS secret_references(secret_ref TEXT PRIMARY KEY,provider TEXT NOT NULL,purpose TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('ACTIVE','INVALID','REVOKED')),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_rotated_at TEXT);
        CREATE TABLE IF NOT EXISTS system_settings(setting_key TEXT PRIMARY KEY,value_json TEXT NOT NULL,description TEXT NOT NULL,updated_at TEXT NOT NULL,updated_by TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS activity_logs(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,data TEXT NOT NULL);
        """)
        # Defense in depth: even accidental/raw application SQL cannot rewrite history.
        self.db.executescript("""
        CREATE TRIGGER IF NOT EXISTS audit_logs_no_update BEFORE UPDATE ON audit_logs BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete BEFORE DELETE ON audit_logs BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;
        """)
        stamp=_now()
        with self.db:
            for role in ROLES: self.db.execute("INSERT OR IGNORE INTO security_roles VALUES(?,?,?,1)",(role,role,role.replace("_"," ").title()))
            for permission in sorted(PERMISSIONS): self.db.execute("INSERT OR IGNORE INTO security_permissions VALUES(?,?)",(permission,permission))
            for role,permissions in ROLE_PERMISSIONS.items():
                for permission in permissions: self.db.execute("INSERT OR IGNORE INTO security_role_permissions VALUES(?,?)",(role,permission))
            row=self.db.execute("SELECT 1 FROM local_credentials WHERE account_id=?",(ADMIN_ACCOUNT_ID,)).fetchone()
            if not row:
                if not bootstrap_password: raise ValueError("Un mot de passe administrateur initial est requis")
                self.db.execute("INSERT INTO local_credentials VALUES(?,?,?,1)",(ADMIN_ACCOUNT_ID,hash_password(bootstrap_password),stamp))
            self.db.execute("INSERT OR IGNORE INTO security_users VALUES(?,?,?,?,?,?,?,?,NULL)",(ADMIN_ACCOUNT_ID,bootstrap_username,"Administrateur","ACTIVE",ADMIN_ACCOUNT_ID,stamp,stamp,None))
            self.db.execute("INSERT OR IGNORE INTO security_user_roles VALUES(?,?,?,?)",(ADMIN_ACCOUNT_ID,"ADMIN",stamp,"bootstrap"))

    def user_by_username(self,username):
        row=self.db.execute("SELECT * FROM security_users WHERE username=?",(username,)).fetchone(); return dict(row) if row else None
    def user(self,user_id):
        row=self.db.execute("SELECT * FROM security_users WHERE user_id=?",(user_id,)).fetchone(); return dict(row) if row else None
    def users(self):
        rows=self.db.execute("SELECT * FROM security_users ORDER BY username").fetchall()
        return [{**dict(r),"roles":self.roles_for(r["user_id"])} for r in rows]
    def roles_for(self,user_id): return [r[0] for r in self.db.execute("SELECT role_id FROM security_user_roles WHERE user_id=? ORDER BY role_id",(user_id,))]
    def permissions_for_role(self,role): return {r[0] for r in self.db.execute("SELECT permission_id FROM security_role_permissions WHERE role_id=?",(role,))}
    def permissions_for(self,user_id): return {r[0] for r in self.db.execute("SELECT DISTINCT rp.permission_id FROM security_user_roles ur JOIN security_role_permissions rp ON rp.role_id=ur.role_id WHERE ur.user_id=?",(user_id,))}
    def credential(self,user_id):
        row=self.db.execute("SELECT c.* FROM security_users u JOIN local_credentials c ON c.account_id=u.credential_ref WHERE u.user_id=?",(user_id,)).fetchone(); return Credential(**dict(row)) if row else None
    def authenticate(self,username,password):
        user=self.user_by_username(username)
        if not user or user["status"]!="ACTIVE": return None
        credential=self.credential(user["user_id"])
        return user if credential and verify_password(password,credential.password_hash) else None
    def mark_login(self,user_id):
        with self.db: self.db.execute("UPDATE security_users SET last_login_at=?,updated_at=? WHERE user_id=?",(_now(),_now(),user_id))
    def create_user(self,username,display_name,password,roles,actor):
        username=username.strip(); display_name=display_name.strip(); validate_password(password,username)
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,64}",username): raise ValueError("Nom utilisateur invalide")
        if not roles or not set(roles)<=set(ROLES): raise ValueError("Rôle invalide")
        uid=str(uuid.uuid4()); credential_ref=f"user:{uid}"; stamp=_now()
        with self.db:
            self.db.execute("INSERT INTO local_credentials VALUES(?,?,?,1)",(credential_ref,hash_password(password),stamp))
            self.db.execute("INSERT INTO security_users VALUES(?,?,?,?,?,?,?,?,NULL)",(uid,username,display_name or username,"ACTIVE",credential_ref,stamp,stamp,None))
            for role in roles: self.db.execute("INSERT INTO security_user_roles VALUES(?,?,?,?)",(uid,role,stamp,actor))
        return self.user(uid)
    def set_status(self,user_id,status,actor):
        if status not in USER_STATUSES: raise ValueError("Statut invalide")
        if user_id==ADMIN_ACCOUNT_ID and status!="ACTIVE": raise ValueError("L’administrateur principal ne peut pas être désactivé")
        with self.db:
            self.db.execute("UPDATE security_users SET status=?,locked_until=NULL,updated_at=? WHERE user_id=?",(status,_now(),user_id)); self.bump_version(user_id)
        return self.user(user_id)
    def assign_roles(self,user_id,roles,actor):
        if not roles or not set(roles)<=set(ROLES): raise ValueError("Rôle invalide")
        if user_id==ADMIN_ACCOUNT_ID and "ADMIN" not in roles: raise ValueError("Le rôle ADMIN principal est obligatoire")
        with self.db:
            self.db.execute("DELETE FROM security_user_roles WHERE user_id=?",(user_id,))
            for role in roles: self.db.execute("INSERT INTO security_user_roles VALUES(?,?,?,?)",(user_id,role,_now(),actor))
            self.bump_version(user_id)
    def reset_password(self,user_id,password):
        user=self.user(user_id); validate_password(password,user["username"] if user else "")
        with self.db:
            self.db.execute("UPDATE local_credentials SET password_hash=?,password_changed_at=?,session_version=session_version+1 WHERE account_id=(SELECT credential_ref FROM security_users WHERE user_id=?)",(hash_password(password),_now(),user_id)); self.revoke_sessions(user_id)
    def change_password(self,user_id,current,new,actor=None):
        credential=self.credential(user_id); user=self.user(user_id); validate_password(new,user["username"])
        if not credential or not verify_password(current,credential.password_hash): raise PermissionError("Mot de passe actuel incorrect")
        self.reset_password(user_id,new)
        stamp=_now(); activity_id=str(uuid.uuid4()); activity={"event_type":"PASSWORD_CHANGED","drcloud_product_key":user_id,"source":"SECURITY","metadata":{"actor":actor or (user["username"] if user else user_id),"success":True},"id":activity_id,"timestamp":stamp}
        with self.db: self.db.execute("INSERT INTO activity_logs VALUES(?,?,?)",(activity_id,stamp,json.dumps(activity)))
    def bump_version(self,user_id): self.db.execute("UPDATE local_credentials SET session_version=session_version+1 WHERE account_id=(SELECT credential_ref FROM security_users WHERE user_id=?)",(user_id,)); self.revoke_sessions(user_id)
    def create_session(self,user_id,expires_at,remote):
        sid=str(uuid.uuid4()); c=self.credential(user_id); stamp=_now()
        with self.db: self.db.execute("INSERT INTO security_sessions VALUES(?,?,?,?,?,?,NULL,?)",(sid,user_id,c.session_version,stamp,stamp,expires_at,remote))
        return sid
    def valid_session(self,sid,user_id,version):
        row=self.db.execute("SELECT s.*,u.status FROM security_sessions s JOIN security_users u ON u.user_id=s.user_id WHERE s.session_id=? AND s.user_id=?",(sid,user_id)).fetchone()
        if not row or row["revoked_at"] or row["status"]!="ACTIVE" or row["expires_at"]<=_now() or row["session_version"]!=version: return False
        with self.db: self.db.execute("UPDATE security_sessions SET last_seen_at=? WHERE session_id=?",(_now(),sid))
        return True
    def revoke_sessions(self,user_id,session_id=None):
        query="UPDATE security_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL"; args=[_now(),user_id]
        if session_id: query+=" AND session_id=?"; args.append(session_id)
        self.db.execute(query,args)
    def active_sessions(self): return [dict(r) for r in self.db.execute("SELECT s.session_id,s.user_id,u.username,s.created_at,s.last_seen_at,s.expires_at FROM security_sessions s JOIN security_users u ON u.user_id=s.user_id WHERE s.revoked_at IS NULL AND s.expires_at>? ORDER BY s.last_seen_at DESC",(_now(),))]
    def audit(self,actor,action,entity_type,entity_id=None,request_id=None,source=None,metadata=None,success=True):
        values=(str(uuid.uuid4()),_now(),actor,action,entity_type,entity_id,request_id,source,json.dumps(sanitise(metadata or {}),ensure_ascii=False),int(success))
        if self.db.in_transaction: self.db.execute("INSERT INTO audit_logs VALUES(?,?,?,?,?,?,?,?,?,?)",values)
        else:
            with self.db: self.db.execute("INSERT INTO audit_logs VALUES(?,?,?,?,?,?,?,?,?,?)",values)
    def audits(self,limit=50,*,actor=None,entity_type=None,action=None,success=None,since=None,until=None):
        clauses=[]; args=[]
        for column,value in (("actor_id",actor),("entity_type",entity_type),("action",action)):
            if value: clauses.append(f"{column}=?"); args.append(value)
        if success is not None: clauses.append("success=?"); args.append(int(success))
        if since: clauses.append("timestamp>=?"); args.append(since)
        if until: clauses.append("timestamp<=?"); args.append(until)
        where=" WHERE "+" AND ".join(clauses) if clauses else ""
        args.append(min(max(int(limit),1),200))
        rows=self.db.execute("SELECT * FROM audit_logs"+where+" ORDER BY timestamp DESC LIMIT ?",args)
        return [{**dict(r),"metadata":json.loads(r["metadata_json"])} for r in rows]

    @staticmethod
    def _setting_value(definition,value):
        if SENSITIVE.search(definition.key): raise ValueError("Une clé de secret est interdite dans SystemSetting")
        if definition.value_type=="bool":
            if not isinstance(value,bool): raise ValueError("Valeur booléenne requise")
        elif definition.value_type=="int":
            if isinstance(value,bool) or not isinstance(value,int): raise ValueError("Valeur entière requise")
            if value < 0 or value > 10080: raise ValueError("Valeur hors limites")
        elif definition.value_type=="float":
            if isinstance(value,bool) or not isinstance(value,(int,float)): raise ValueError("Valeur numérique requise")
        elif definition.value_type in {"string","enum"}:
            if not isinstance(value,str) or len(value)>500: raise ValueError("Chaîne invalide")
        else: raise ValueError("Type de setting inconnu")
        if definition.choices and value not in definition.choices: raise ValueError("Valeur non autorisée")
        if isinstance(value,str) and (SENSITIVE.search(value) or re.search(r"(?i)bearer\s+\S+",value)):
            raise ValueError("Une valeur ressemblant à un secret est interdite")
        return value

    def settings(self):
        stored={r["setting_key"]:r for r in self.db.execute("SELECT * FROM system_settings")}
        result=[]
        for key,definition in SETTING_REGISTRY.items():
            row=stored.get(key); value=json.loads(row["value_json"]) if row else definition.default
            result.append({"key":key,"value":value,"value_type":definition.value_type,"category":definition.category,
                           "description":definition.description,"runtime_editable":definition.runtime_editable,
                           "updated_at":row["updated_at"] if row else None,"updated_by":row["updated_by"] if row else None})
        return result

    def set_setting(self,key,value,actor,*,request_id=None,source="security-api"):
        definition=SETTING_REGISTRY.get(key)
        if not definition: raise KeyError("Setting inconnu")
        if not definition.runtime_editable: raise PermissionError("Setting non modifiable à chaud")
        value=self._setting_value(definition,value); stamp=_now()
        with self.db:
            self.db.execute("INSERT INTO system_settings VALUES(?,?,?,?,?) ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json,description=excluded.description,updated_at=excluded.updated_at,updated_by=excluded.updated_by",(key,json.dumps(value),definition.description,stamp,actor))
            self.audit(actor,"SYSTEM_SETTING_CHANGED","SYSTEM_SETTING",key,request_id,source,{"value":value})
        return next(item for item in self.settings() if item["key"]==key)

    def register_secret_reference(self,secret_ref,provider,purpose,actor,*,status="ACTIVE",request_id=None):
        if not re.fullmatch(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)+",secret_ref): raise ValueError("Référence de secret invalide")
        if status not in {"ACTIVE","INVALID","REVOKED"}: raise ValueError("État de secret invalide")
        stamp=_now()
        with self.db:
            self.db.execute("INSERT INTO secret_references VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(secret_ref) DO UPDATE SET provider=excluded.provider,purpose=excluded.purpose,status=excluded.status,updated_at=excluded.updated_at",(secret_ref,provider,purpose,status,stamp,stamp))
            self.audit(actor,"SECRET_REFERENCE_CHANGED","SECRET_REFERENCE",secret_ref,request_id,"security-api",{"provider":provider,"purpose":purpose,"status":status})
        return dict(self.db.execute("SELECT * FROM secret_references WHERE secret_ref=?",(secret_ref,)).fetchone())

class AuditService:
    """Only supported application entry point for the immutable audit ledger."""
    def __init__(self,store: SecurityStore): self.store=store
    def record(self,*,actor,action,entity_type,entity_id=None,success=True,request_id=None,source=None,metadata=None):
        self.store.audit(actor,action,entity_type,entity_id,request_id,source,metadata,success)

class AuthorizationService:
    def __init__(self,store): self.store=store
    def require(self,user_id,permission):
        if permission not in PERMISSIONS or permission not in self.store.permissions_for(user_id): raise PermissionError("permission denied")

class CredentialStore:
    """Compatibility facade retained for callers of the historic admin store."""
    def __init__(self,database,bootstrap_password): self.security=SecurityStore(database,"admin",bootstrap_password)
    def get(self): return self.security.credential(ADMIN_ACCOUNT_ID)
    def verify(self,password):
        c=self.get(); return bool(c and verify_password(password,c.password_hash))
    def change_password(self,current_password,new_password,actor): self.security.change_password(ADMIN_ACCOUNT_ID,current_password,new_password,actor); return self.get().session_version
