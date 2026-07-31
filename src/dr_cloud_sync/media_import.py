"""Safe, preview-first import of PrestaShop product images into ProductMedia."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import shutil
from typing import Any

from .domain import MarketingUsage, MediaRole, MediaSource
from .jobs import JobRunner, SqliteJobRepository
from .media import MAX_FILE_SIZE, MIN_FREE_BYTES, MediaError, ProductMediaService
from .prestashop import PrestaShopClient, PrestaShopError

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
JOB_TYPE = "PRODUCT_MEDIA_IMPORT"


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
                 media: ProductMediaService, catalogue) -> None:
        self.database=Path(database); self.client=client; self.media=media; self.catalogue=catalogue
        self.jobs=SqliteJobRepository(self.database)

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
            items.append({"product_key":product.drcloud_product_key,"product_id":product.product_id,
                          "combination_id":product.combination_id,"classification":classification,
                          "candidate_image_id":candidate,"provenance":provenance,
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

    def apply(self, preview: dict[str, Any], *, actor="authenticated", batch_size=25) -> dict[str, Any]:
        candidates=[x for x in preview.get("items", []) if x.get("classification")=="SAFE"][:batch_size]
        job=self.jobs.create(job_type=JOB_TYPE,connector="PRESTASHOP",operation="APPLY_SAFE")
        def operation():
            if shutil.disk_usage(self.media.storage.root).free < MIN_FREE_BYTES:
                raise MediaError("Espace disque insuffisant avant import média")
            result={"processed":0,"imported":0,"skipped":0,"failed":0,"errors":[]}
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
                    result["errors"].append({"product_key":item["product_key"],"image_id":item["candidate_image_id"],"error":str(exc)[:100]})
            return result
        value=asdict(JobRunner(self.jobs).run(job,operation))
        value["status"]=value["status"].value
        return value

    @staticmethod
    def _reference(product_id, combination_id, image_id, provenance) -> str:
        return json.dumps({"resource":f"images/products/{product_id}/{image_id}","image_id":str(image_id),
                           "product_id":str(product_id),"combination_id":str(combination_id) if combination_id is not None else None,
                           "provenance":provenance},sort_keys=True,separators=(",",":"))
