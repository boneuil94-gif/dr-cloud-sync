"""Canonical product media service with replaceable, controlled file storage."""
from __future__ import annotations

from dataclasses import asdict
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

from .domain import (ActivityLog, MarketingUsage, MediaRole, MediaSource,
                     MediaVariantKind, ProductMedia, ProductMediaVariant,
                     VisualType, utc_now)

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_DIMENSION = 10_000
MIN_DIMENSION = 16
MIN_FREE_BYTES = 100 * 1024 * 1024
FORMATS = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}
VARIANT_SIZES = {MediaVariantKind.THUMBNAIL: (160, 160), MediaVariantKind.DISPLAY: (1200, 1200)}


class MediaError(ValueError):
    pass


class LocalMediaStorage:
    """Opaque-reference storage adapter; callers can never provide a path."""
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, media_id: str, kind: MediaVariantKind, extension: str, data: bytes) -> str:
        if shutil.disk_usage(self.root).free < len(data) + MIN_FREE_BYTES:
            raise MediaError("Espace disque insuffisant pour enregistrer le média")
        token = media_id.removeprefix("media:")
        if not re.fullmatch(r"[0-9a-f-]{36}", token):
            raise MediaError("Identité média invalide")
        relative = Path(token) / f"{kind.value.lower()}.{extension}"
        target = self._resolve(relative.as_posix())
        target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o640); temporary.replace(target)
        return relative.as_posix()

    def read(self, reference: str) -> bytes:
        target = self._resolve(reference)
        if not target.is_file(): raise FileNotFoundError(reference)
        return target.read_bytes()

    def path(self, reference: str) -> Path:
        target = self._resolve(reference)
        if not target.is_file(): raise FileNotFoundError(reference)
        return target

    def _resolve(self, reference: str) -> Path:
        if not reference or Path(reference).is_absolute() or ".." in Path(reference).parts:
            raise MediaError("Référence de stockage invalide")
        target = (self.root / reference).resolve()
        if self.root.resolve() not in target.parents:
            raise MediaError("Référence de stockage invalide")
        return target


class SQLiteProductMediaRepository:
    def __init__(self, database: Path):
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS product_media(
          media_id TEXT PRIMARY KEY, product_key TEXT NOT NULL, media_type TEXT NOT NULL,
          role TEXT NOT NULL, source TEXT NOT NULL, source_reference TEXT,
          storage_reference TEXT NOT NULL, mime_type TEXT NOT NULL, width INTEGER NOT NULL,
          height INTEGER NOT NULL, file_size INTEGER NOT NULL, sha256 TEXT NOT NULL,
          original_filename TEXT, visual_type TEXT NOT NULL DEFAULT 'UNSPECIFIED',
          marketing_usage TEXT NOT NULL DEFAULT 'UNKNOWN', protected_original INTEGER NOT NULL DEFAULT 0,
          usages TEXT NOT NULL DEFAULT '["catalogue"]', imported_at TEXT, source_updated_at TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS product_media_variants(
          media_id TEXT NOT NULL, kind TEXT NOT NULL, storage_reference TEXT NOT NULL,
          mime_type TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
          file_size INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(media_id,kind));
        CREATE UNIQUE INDEX IF NOT EXISTS ux_product_media_primary
          ON product_media(product_key) WHERE role='PRIMARY' AND active=1;
        CREATE INDEX IF NOT EXISTS ix_product_media_product ON product_media(product_key,active,role);
        CREATE INDEX IF NOT EXISTS ix_product_media_checksum ON product_media(sha256);
        """)

    @staticmethod
    def _media(row) -> ProductMedia | None:
        if row is None: return None
        return ProductMedia(media_id=row["media_id"], product_key=row["product_key"], media_type=row["media_type"],
          role=row["role"], source=row["source"], source_reference=row["source_reference"],
          storage_reference=row["storage_reference"], mime_type=row["mime_type"], width=row["width"],
          height=row["height"], file_size=row["file_size"], sha256=row["sha256"],
          original_filename=row["original_filename"], visual_type=row["visual_type"],
          marketing_usage=row["marketing_usage"], protected_original=bool(row["protected_original"]),
          usages=tuple(json.loads(row["usages"])), imported_at=row["imported_at"],
          source_updated_at=row["source_updated_at"], created_at=row["created_at"],
          updated_at=row["updated_at"], active=bool(row["active"]))

    def list(self, product_key: str, *, active_only=False) -> list[ProductMedia]:
        suffix=" AND active=1" if active_only else ""
        return [self._media(r) for r in self.db.execute("SELECT * FROM product_media WHERE product_key=?"+suffix+" ORDER BY active DESC,role,created_at",(product_key,))]

    def get(self, media_id: str) -> ProductMedia | None:
        return self._media(self.db.execute("SELECT * FROM product_media WHERE media_id=?",(media_id,)).fetchone())

    def variant(self, media_id: str, kind: MediaVariantKind) -> ProductMediaVariant | None:
        kind=MediaVariantKind(kind)
        r=self.db.execute("SELECT * FROM product_media_variants WHERE media_id=? AND kind=?",(media_id,kind.value)).fetchone()
        return ProductMediaVariant(r["media_id"],r["kind"],r["storage_reference"],r["mime_type"],r["width"],r["height"],r["file_size"],r["sha256"]) if r else None

    def add(self, media: ProductMedia, variants: list[ProductMediaVariant]) -> None:
        with self.db:
            if media.role is MediaRole.PRIMARY:
                self.db.execute("UPDATE product_media SET role='SECONDARY',updated_at=? WHERE product_key=? AND role='PRIMARY' AND active=1",(media.created_at,media.product_key))
            self.db.execute("INSERT INTO product_media VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
              media.media_id,media.product_key,media.media_type,media.role.value,media.source.value,media.source_reference,
              media.storage_reference,media.mime_type,media.width,media.height,media.file_size,media.sha256,
              media.original_filename,media.visual_type.value,media.marketing_usage.value,int(media.protected_original),
              json.dumps(media.usages),media.imported_at,media.source_updated_at,media.created_at,media.updated_at,int(media.active)))
            self.db.executemany("INSERT INTO product_media_variants VALUES(?,?,?,?,?,?,?,?)",[(v.media_id,v.kind.value,v.storage_reference,v.mime_type,v.width,v.height,v.file_size,v.sha256) for v in variants])

    def primary(self, product_key: str) -> ProductMedia | None:
        return self._media(self.db.execute("SELECT * FROM product_media WHERE product_key=? AND role='PRIMARY' AND active=1",(product_key,)).fetchone())

    def primaries(self) -> dict[str, ProductMedia]:
        """Return the active PRIMARY relation for every product in one snapshot.

        Catalogue is a bulk read path.  Looking up the relation again for every
        filter, diagnostic and URL made its cost proportional to the number of
        products (and particularly visible once hundreds of images were imported).
        """
        rows = self.db.execute(
            "SELECT * FROM product_media WHERE role='PRIMARY' AND active=1"
        ).fetchall()
        return {row["product_key"]: self._media(row) for row in rows}

    def variants_for(self, media_ids: list[str]) -> dict[tuple[str, MediaVariantKind], ProductMediaVariant]:
        if not media_ids:
            return {}
        placeholders = ",".join("?" for _ in media_ids)
        rows = self.db.execute(
            f"SELECT * FROM product_media_variants WHERE media_id IN ({placeholders})",
            media_ids,
        ).fetchall()
        return {
            (row["media_id"], MediaVariantKind(row["kind"])): ProductMediaVariant(
                row["media_id"], row["kind"], row["storage_reference"], row["mime_type"],
                row["width"], row["height"], row["file_size"], row["sha256"]
            )
            for row in rows
        }

    def by_source_reference(self, product_key: str, source: MediaSource,
                            source_reference: str) -> ProductMedia | None:
        return self._media(self.db.execute(
            "SELECT * FROM product_media WHERE product_key=? AND source=? AND source_reference=? AND active=1 ORDER BY created_at DESC LIMIT 1",
            (product_key, MediaSource(source).value, source_reference)).fetchone())

    def make_primary(self, product_key: str, media_id: str) -> None:
        stamp=utc_now()
        with self.db:
            row=self.db.execute("SELECT active FROM product_media WHERE media_id=? AND product_key=?",(media_id,product_key)).fetchone()
            if not row or not row[0]: raise KeyError(media_id)
            self.db.execute("UPDATE product_media SET role='SECONDARY',updated_at=? WHERE product_key=? AND role='PRIMARY' AND active=1",(stamp,product_key))
            self.db.execute("UPDATE product_media SET role='PRIMARY',updated_at=? WHERE media_id=?",(stamp,media_id))

    def disable(self, product_key: str, media_id: str) -> None:
        with self.db:
            changed=self.db.execute("UPDATE product_media SET active=0,role='SECONDARY',updated_at=? WHERE media_id=? AND product_key=? AND active=1",(utc_now(),media_id,product_key)).rowcount
            if not changed: raise KeyError(media_id)


class ProductMediaService:
    def __init__(self, repository: SQLiteProductMediaRepository, storage: LocalMediaStorage, catalogue, audit):
        self.repository=repository; self.storage=storage; self.catalogue=catalogue; self.audit=audit

    def add(self, product_key: str, data: bytes, *, filename="upload", role="PRIMARY", source="MANUAL_UPLOAD",
            source_reference=None, marketing_usage="UNKNOWN", visual_type="UNSPECIFIED", protected_original=False,
            usages=("catalogue",), actor="authenticated") -> ProductMedia:
        if self.catalogue.get(product_key) is None: raise KeyError(product_key)
        if not data or len(data)>MAX_FILE_SIZE: raise MediaError("Image vide ou supérieure à 10 Mio")
        clean_name=re.sub(r"[^A-Za-z0-9._-]","_",Path(filename).name)[:120] or "upload"
        try:
            Image.MAX_IMAGE_PIXELS=MAX_PIXELS
            with Image.open(BytesIO(data)) as probe:
                probe.verify(); fmt=probe.format
            if fmt not in FORMATS: raise MediaError("Format refusé; JPEG, PNG et WebP uniquement")
            with Image.open(BytesIO(data)) as opened:
                opened.load(); image=ImageOps.exif_transpose(opened)
                if min(image.size)<MIN_DIMENSION or max(image.size)>MAX_DIMENSION or image.width*image.height>MAX_PIXELS:
                    raise MediaError("Dimensions image hors limites (16 à 10000 px, 40 MP maximum)")
                # Re-encoding strips EXIF/geolocation and makes MIME authoritative.
                normalized=BytesIO(); save_format="JPEG" if fmt=="JPEG" else fmt
                if save_format=="JPEG" and image.mode not in ("RGB","L"): image=image.convert("RGB")
                image.save(normalized,save_format,quality=90,optimize=True)
                original=normalized.getvalue(); width,height=image.size
                variant_images=[]
                for kind,size in VARIANT_SIZES.items():
                    derived=image.copy(); derived.thumbnail(size)
                    output=BytesIO(); derived.save(output,save_format,quality=85,optimize=True)
                    variant_images.append((kind,derived.size,output.getvalue()))
        except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
            raise MediaError("Contenu image invalide ou dangereux") from exc
        mime,extension=FORMATS[fmt]; media_id=f"media:{uuid.uuid4()}"; stamp=utc_now()
        original_ref=self.storage.write(media_id,MediaVariantKind.ORIGINAL,extension,original)
        media=ProductMedia(media_id,product_key,"IMAGE",MediaRole(role),MediaSource(source),original_ref,mime,width,height,
          len(original),hashlib.sha256(original).hexdigest(),source_reference,clean_name,VisualType(visual_type),
          MarketingUsage(marketing_usage),bool(protected_original),tuple(usages),stamp if source!=MediaSource.MANUAL_UPLOAD.value else None,None,stamp,stamp)
        variants=[]
        for kind,(vw,vh),content in variant_images:
            ref=self.storage.write(media_id,kind,extension,content)
            variants.append(ProductMediaVariant(media_id,kind,ref,mime,vw,vh,len(content),hashlib.sha256(content).hexdigest()))
        self.repository.add(media,variants)
        event="PRODUCT_MEDIA_IMPORTED" if media.source is MediaSource.PRESTASHOP else "PRODUCT_MEDIA_ADDED"
        self.audit.add_activity(ActivityLog(event,product_key,"PRODUCT_MEDIA",{"media_id":media_id,"source":media.source.value,"actor":actor}))
        return media

    def primary(self, product_key: str): return self.repository.primary(product_key)
    def make_primary(self, product_key, media_id, actor="authenticated"):
        self.repository.make_primary(product_key,media_id); self.audit.add_activity(ActivityLog("PRODUCT_MEDIA_PRIMARY_CHANGED",product_key,"PRODUCT_MEDIA",{"media_id":media_id,"actor":actor}))
    def disable(self, product_key, media_id, actor="authenticated"):
        self.repository.disable(product_key,media_id); self.audit.add_activity(ActivityLog("PRODUCT_MEDIA_DISABLED",product_key,"PRODUCT_MEDIA",{"media_id":media_id,"actor":actor}))
    def url(self, media: ProductMedia | None, kind=MediaVariantKind.THUMBNAIL):
        if not media: return None
        variant=self.repository.variant(media.media_id,kind) or self.repository.variant(media.media_id,MediaVariantKind.ORIGINAL)
        return f"/media/{media.media_id}/{variant.kind.value.lower()}?v={variant.sha256[:16]}" if variant else None
    def diagnostics(self):
        rows=self.repository.db.execute("SELECT media_id,product_key,active,storage_reference,sha256 FROM product_media").fetchall()
        missing=corrupt=0; size=0
        known=set()
        for row in rows:
            known.add(row["storage_reference"])
            try:
                content=self.storage.read(row["storage_reference"]); size+=len(content)
                corrupt+=hashlib.sha256(content).hexdigest()!=row["sha256"]
            except (OSError,MediaError): missing+=1
        products=self.catalogue.all(); pictured=len({r["product_key"] for r in rows if r["active"]})
        return {"status":"warning" if missing or corrupt else "ok","active_assets":sum(r["active"] for r in rows),
          "products_with_image":pictured,"products_without_image":max(0,len(products)-pictured),"storage_bytes":size,
          "missing_files":missing,"corrupt_files":corrupt}
