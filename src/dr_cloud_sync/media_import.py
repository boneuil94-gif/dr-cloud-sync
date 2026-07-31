"""Safe, preview-first import of PrestaShop product images into ProductMedia."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import os
import shutil
import hashlib
import sqlite3
from typing import Any

from .domain import MarketingUsage, MediaRole, MediaSource
from .jobs import JobRunner, SqliteJobRepository
from .media import MAX_FILE_SIZE, MIN_FREE_BYTES, MediaError, ProductMediaService
from .prestashop import PrestaShopClient, PrestaShopError
from .config import ConfigurationError, resolve_prestashop_api_url
from .backup_service import BackupService
from .domain import ActivityLog
from .admin_status import application_metadata

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
JOB_TYPE = "PRESTASHOP_MEDIA_IMPORT"


class PrestaShopIntegrationUnavailable(RuntimeError):
    """Operator-safe failure at the optional integration boundary."""
    operator_safe = True


class PrestaShopMediaProvider:
    """Lazy provider: inspecting or booting DrCloud never constructs a client."""
    def __init__(self, database: Path, media: ProductMediaService, catalogue,
                 environ=None, client_factory=None, backup_service=None) -> None:
        self.database=Path(database); self.media=media; self.catalogue=catalogue
        self.environ=environ if environ is not None else os.environ
        self.client_factory=client_factory or PrestaShopClient; self._service=None; self._unavailable=False
        self.backup_service=backup_service or BackupService(Path(database).parent / "backups")

    def status(self) -> dict[str, Any]:
        key=self.environ.get("PRESTASHOP_API_KEY", "").strip()
        if not key or key == "CHANGE_ME":
            return {"status":"warning","state":"NOT_CONFIGURED","configured":False,
                    "message":"Intégration non configurée"}
        try:
            resolve_prestashop_api_url(self.environ.get("PRESTASHOP_API_URL"))
            timeout=float(self.environ.get("PRESTASHOP_TIMEOUT_SECONDS", "10"))
            if timeout <= 0: raise ValueError
        except (ConfigurationError,ValueError,TypeError):
            return {"status":"warning","state":"INVALID_CONFIGURATION","configured":False,
                    "message":"Configuration invalide"}
        if self._unavailable:
            return {"status":"warning","state":"UNAVAILABLE","configured":True,
                    "message":"Service externe temporairement indisponible"}
        return {"status":"ok","state":"CONFIGURED","configured":True,
                "message":"Intégration configurée (disponibilité non testée)"}

    def service(self) -> "ProductMediaImportService":
        state=self.status()["state"]
        if state == "NOT_CONFIGURED":
            raise PrestaShopIntegrationUnavailable(
                "Import PrestaShop indisponible — intégration non configurée")
        if state == "INVALID_CONFIGURATION":
            raise PrestaShopIntegrationUnavailable(
                "Import PrestaShop indisponible — configuration invalide")
        if self._service is None:
            url=resolve_prestashop_api_url(self.environ.get("PRESTASHOP_API_URL"))
            timeout=float(self.environ.get("PRESTASHOP_TIMEOUT_SECONDS", "10"))
            client=self.client_factory(url,self.environ["PRESTASHOP_API_KEY"].strip(),
                                       timeout=timeout,retries=2)
            self._service=ProductMediaImportService(self.database,client,self.media,self.catalogue,
                backup_service=self.backup_service,
                environment=self.environ.get("DRCLOUD_ENV", "development"),
                safe_mode=self.environ.get("DRCLOUD_SAFE_MODE", "true").lower() in {"1","true","yes","on"})
        return self._service

    def mark_unavailable(self) -> None:
        self._unavailable=True


def _ids(row: dict[str, Any]) -> list[str]:
    images = (row.get("associations") or {}).get("images") or []
    if isinstance(images, dict):
        images = images.get("image", images)
    if isinstance(images, dict): images = [images]
    if not isinstance(images, list): return []
    return list(dict.fromkeys(str(item.get("id")) for item in images
                              if isinstance(item, dict) and str(item.get("id", "")).isdigit()))


class ProductMediaImportService:
    """Resolve explicit identities, then download only deterministic candidates."""
    def __init__(self, database: Path, client: PrestaShopClient,
                 media: ProductMediaService, catalogue, *, backup_service=None,
                 environment="development", safe_mode=True) -> None:
        self.database=Path(database); self.client=client; self.media=media; self.catalogue=catalogue
        self.jobs=SqliteJobRepository(self.database)
        self.backup_service=backup_service or BackupService(self.database.parent / "backups")
        self.environment=environment; self.safe_mode=safe_mode

    def preview(self) -> dict[str, Any]:
        products={str(row["id"]): row for row in self.client.iter_resource("products")}
        combinations={str(row["id"]): row for row in self.client.iter_resource("combinations")}
        items=[]
        for product in self.catalogue.all():
            parent=products.get(str(product.product_id), {})
            combination=(combinations.get(str(product.combination_id), {})
                         if product.combination_id is not None else {})
            specific=_ids(combination); parent_ids=_ids(parent)
            current=self.media.primary(product.drcloud_product_key)
            classification="NO_IMAGE"; candidate=None; provenance=None
            if current:
                classification="EXISTING_PRIMARY"
            elif len(specific)==1:
                classification="SAFE"; candidate=specific[0]; provenance="COMBINATION_IMAGE"
            elif len(specific)>1:
                classification="AMBIGUOUS"
            else:
                cover=str(parent.get("id_default_image") or "")
                chosen=cover if cover in parent_ids else (parent_ids[0] if len(parent_ids)==1 else None)
                if chosen:
                    classification="SAFE"; candidate=chosen
                    provenance="PARENT_FALLBACK" if product.combination_id is not None else "PARENT_IMAGE"
                elif len(parent_ids)>1:
                    classification="AMBIGUOUS"
            source_reference=(self._reference(product.product_id, product.combination_id,
                                               candidate, provenance) if candidate else None)
            already=bool(source_reference and self.media.repository.by_source_reference(
                product.drcloud_product_key, MediaSource.PRESTASHOP, source_reference))
            reason = ("plusieurs images de combinaison" if len(specific)>1 else
                      "plusieurs images parentes sans image par défaut déterministe" if classification=="AMBIGUOUS" else None)
            items.append({"product_key":product.drcloud_product_key,"product":product.base_name or product.name,
                          "variant":product.variant_name,
                          "product_id":product.product_id,
                          "combination_id":product.combination_id,"classification":classification,
                          "candidate_image_id":candidate,"provenance":provenance,
                          "candidates":specific if specific else parent_ids,"ambiguity_reason":reason,
                          "existing_primary_source":current.source.value if current else None,
                          "already_imported":already})
        summary={"processed":len(items),"combination_image":sum(x["provenance"]=="COMBINATION_IMAGE" for x in items),
                 "parent_fallback":sum(x["provenance"]=="PARENT_FALLBACK" for x in items),
                 "existing_primary":sum(x["classification"]=="EXISTING_PRIMARY" for x in items),
                 "no_image":sum(x["classification"]=="NO_IMAGE" for x in items),
                 "ambiguous":sum(x["classification"]=="AMBIGUOUS" for x in items),
                 "candidate_images":sum(bool(x["candidate_image_id"]) for x in items),
                 "downloads_required":sum(x["classification"]=="SAFE" and not x["already_imported"] for x in items)}
        return {"job_type":JOB_TYPE,"mode":"PREVIEW","summary":summary,"items":items}

    def apply(self, preview: dict[str, Any] | None = None, *, actor="authenticated", batch_size=None) -> dict[str, Any]:
        # The UI preview is informative only: APPLY always reads PrestaShop and local PRIMARYs again.
        current=self.preview()
        candidates=[x for x in current["items"] if x["classification"]=="SAFE" and not x["already_imported"]]
        if batch_size is not None: candidates=candidates[:batch_size]
        job=self.jobs.create(job_type=JOB_TYPE,connector="PRESTASHOP",operation="APPLY_SAFE")
        def operation():
            # This verified, official bundle is the mutation gate. Failure escapes before any download.
            backup=self.backup_service.create(self.database,reason="PRESTASHOP_MEDIA_IMPORT",
                environment=self.environment,safe_mode=self.safe_mode,application=application_metadata())
            if shutil.disk_usage(self.media.storage.root).free < MIN_FREE_BYTES:
                raise MediaError("Espace disque insuffisant avant import média")
            identities=sorted((p.drcloud_product_key,p.product_id,p.combination_id) for p in self.catalogue.all())
            business_before=self._business_fingerprint()
            result={"processed":0,"imported":0,"skipped":0,"failed":0,"errors":[],
                    "backup_id":backup["backup_id"],"repreview":current["summary"]}
            for item in candidates:
                result["processed"]+=1
                reference=self._reference(item["product_id"],item.get("combination_id"),item["candidate_image_id"],item["provenance"])
                if self.media.primary(item["product_key"]) or self.media.repository.by_source_reference(item["product_key"],MediaSource.PRESTASHOP,reference):
                    result["skipped"]+=1; continue
                try:
                    data,mime=self.client.download_product_image(item["product_id"],item["candidate_image_id"],max_bytes=MAX_FILE_SIZE)
                    if mime not in ALLOWED_MIME: raise MediaError(f"Content-Type PrestaShop refusé: {mime}")
                    self.media.add(item["product_key"],data,filename=f"prestashop-{item['candidate_image_id']}",
                                   role=MediaRole.PRIMARY.value,source=MediaSource.PRESTASHOP.value,
                                   source_reference=reference,marketing_usage=MarketingUsage.UNKNOWN.value,
                                   protected_original=True,actor=actor)
                    result["imported"]+=1
                except (MediaError,PrestaShopError) as exc:
                    result["failed"]+=1
                    message=(str(exc)[:100] if isinstance(exc,MediaError)
                             else "Téléchargement PrestaShop impossible")
                    result["errors"].append({"product_key":item["product_key"],"image_id":item["candidate_image_id"],"error":message})
            after=sorted((p.drcloud_product_key,p.product_id,p.combination_id) for p in self.catalogue.all())
            diagnostics=self.media.diagnostics()
            duplicates=self.media.repository.db.execute("""SELECT COUNT(*) FROM
              (SELECT product_key,source,source_reference,COUNT(*) n FROM product_media
               WHERE active=1 GROUP BY product_key,source,source_reference HAVING n>1)""").fetchone()[0]
            result.update({"total_products":len(after),"products_with_primary":sum(bool(self.media.primary(p.drcloud_product_key)) for p in self.catalogue.all()),
                "products_without_primary":sum(not self.media.primary(p.drcloud_product_key) for p in self.catalogue.all()),
                "ignored_primary_existing":current["summary"]["existing_primary"],"ambiguous":current["summary"]["ambiguous"],
                "no_data":current["summary"]["no_image"],"media_files_present":diagnostics["active_assets"]-diagnostics["missing_files"],
                "missing_files":diagnostics["missing_files"],"corrupt_files":diagnostics["corrupt_files"],
                "duplicate_product_media":duplicates,"identities_unchanged":identities==after,
                "business_data_unchanged":business_before==self._business_fingerprint()})
            if not result["identities_unchanged"] or not result["business_data_unchanged"]:
                raise RuntimeError("Invariant métier modifié pendant l'import média")
            self.catalogue.add_activity(ActivityLog(JOB_TYPE,"catalogue","PRODUCT_MEDIA",
                {"actor":actor,"result":"PARTIAL" if result["failed"] else "SUCCESS",
                 "backup_id":backup["backup_id"],**{k:v for k,v in result.items() if k not in {"errors","repreview"}},
                 "error_count":len(result["errors"])}))
            return result
        value=asdict(JobRunner(self.jobs).run(job,operation))
        value["status"]=value["status"].value
        return value

    def _business_fingerprint(self) -> str:
        """Fingerprint every business table the media workflow must never mutate."""
        tables=("drcloud_products","stock_movements","counts","history","barcode_assignments",
                "catalogue_eans","suppliers","purchase_orders","purchase_order_lines",
                "goods_receipts","goods_receipt_lines")
        digest=hashlib.sha256()
        with sqlite3.connect(f"file:{self.database}?mode=ro",uri=True) as db:
            existing={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in tables:
                if table not in existing: continue
                digest.update(table.encode())
                for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                    digest.update(json.dumps(row,default=str,ensure_ascii=False,separators=(",",":" )).encode())
        return digest.hexdigest()

    @staticmethod
    def _reference(product_id, combination_id, image_id, provenance) -> str:
        return json.dumps({"resource":f"images/products/{product_id}/{image_id}","image_id":str(image_id),
                           "product_id":str(product_id),"combination_id":str(combination_id) if combination_id is not None else None,
                           "provenance":provenance},sort_keys=True,separators=(",",":"))
