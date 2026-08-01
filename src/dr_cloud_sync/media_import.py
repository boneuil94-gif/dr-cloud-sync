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
from .qonto import EnvironmentSecretProvider

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
        self.secret_ref=self.environ.get("PRESTASHOP_SECRET_REF") or "prestashop.production"
        self.secrets=EnvironmentSecretProvider(self.environ,{self.secret_ref:"PRESTASHOP_API_KEY"})
        self.backup_service=backup_service or BackupService(Path(database).parent / "backups")

    def status(self) -> dict[str, Any]:
        key=self.secrets.resolve(self.secret_ref)
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
            if self.client_factory is PrestaShopClient:
                client=PrestaShopClient.from_secret_ref(url,self.secret_ref,self.secrets,timeout=timeout,retries=2)
            else:  # injectable test adapter; the real adapter always resolves by ref
                client=self.client_factory(url,self.secrets.resolve(self.secret_ref),timeout=timeout,retries=2)
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


def _label(value: Any) -> str | None:
    """Flatten PrestaShop's scalar or translated Webservice values."""
    if isinstance(value, list):
        value = next((item.get("value") for item in value if isinstance(item, dict)
                      and item.get("value")), None)
    elif isinstance(value, dict):
        value = value.get("value")
    return str(value).strip() if value not in (None, "") else None


def _association_ids(row: dict[str, Any], name: str, singular: str) -> list[str]:
    values = (row.get("associations") or {}).get(name) or []
    if isinstance(values, dict): values = values.get(singular, values)
    if isinstance(values, dict): values = [values]
    return [str(item["id"]) for item in values if isinstance(item, dict)
            and str(item.get("id", "")).isdigit()] if isinstance(values, list) else []


def combination_exclusivity(candidate_image_ids: list[str], sibling_candidate_sets: list[list[str]],
                            *, explicitly_associated_image_ids: list[str] | None = None,
                            existing_primary_image_id: str | None = None,
                            deterministic_image_ids: list[str] | None = None) -> dict[str, Any]:
    """Classify a multiple-image association using set membership only.

    This deliberately does not inspect ordering, names, colours, files or pixels.  The
    extra arguments make every negative decision auditable rather than silently
    weakening the rule when another deterministic source is available.
    """
    candidates=set(map(str,candidate_image_ids))
    siblings=set().union(*(set(map(str, values)) for values in sibling_candidate_sets)) \
        if sibling_candidate_sets else set()
    exclusive=sorted(candidates-siblings,key=int)
    shared=sorted(candidates & siblings,key=int)
    explicit=set(map(str, explicitly_associated_image_ids or []))
    deterministic=set(map(str, deterministic_image_ids or []))
    selected=exclusive[0] if len(exclusive)==1 else None
    contradictions=[]
    if selected and selected not in explicit: contradictions.append("NOT_EXPLICITLY_ASSOCIATED")
    if selected and existing_primary_image_id and str(existing_primary_image_id)!=selected:
        contradictions.append("EXISTING_PRIMARY_DESIGNATES_DIFFERENT_IMAGE")
    if selected and any(image_id != selected for image_id in deterministic):
        contradictions.append("OTHER_DETERMINISTIC_SOURCE_DESIGNATES_DIFFERENT_IMAGE")
    safe=bool(selected and not contradictions)
    return {"shared_image_ids":shared,"exclusive_image_ids":exclusive,
            "classification":"SAFE_BY_COMBINATION_EXCLUSIVITY" if safe else "AMBIGUOUS_REMAINING",
            "candidate_image_id":selected if safe else None,"contradictions":contradictions,
            "proof":(f"L’image {selected} est explicitement associée à cette combinaison et n’est "
                     "candidate d’aucune autre combinaison du parent; aucune source déterministe "
                     "ni PRIMARY local ne la contredit." if safe else None)}


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
        # These two resources are read solely to make the diagnostic independently
        # auditable.  Older/fake clients may not expose them.
        try:
            option_values={str(row["id"]): row for row in self.client.iter_resource("product_option_values")}
            options={str(row["id"]): row for row in self.client.iter_resource("product_options")}
        except (KeyError, ValueError, PrestaShopError):
            option_values={}; options={}
        primary_snapshot=self.media.repository.primaries()
        combinations_by_parent: dict[str,list[dict[str,Any]]]={}
        for row in combinations.values():
            parent_id=str(row.get("id_product") or "")
            if parent_id: combinations_by_parent.setdefault(parent_id,[]).append(row)
        items=[]
        for product in self.catalogue.all():
            parent=products.get(str(product.product_id), {})
            combination=(combinations.get(str(product.combination_id), {})
                         if product.combination_id is not None else {})
            specific=_ids(combination); parent_ids=_ids(parent)
            current=primary_snapshot.get(product.drcloud_product_key)
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
            cause = ("MULTIPLE_COMBINATION_IMAGES" if len(specific)>1 else
                     "MULTIPLE_PARENT_IMAGES" if classification=="AMBIGUOUS" else None)
            reason = ("La combinaison référence plusieurs images explicites; aucune relation PrestaShop ne les départage."
                      if cause=="MULTIPLE_COMBINATION_IMAGES" else
                      "Le parent référence plusieurs images sans image cover valide et la combinaison n'en référence aucune."
                      if cause=="MULTIPLE_PARENT_IMAGES" else None)
            value_ids=_association_ids(combination,"product_option_values","product_option_value")
            attributes=[]
            for value_id in value_ids:
                value=option_values.get(value_id,{})
                option_id=str(value.get("id_attribute_group") or value.get("id_product_option") or "")
                attributes.append({"option_id":option_id or None,"option":_label(options.get(option_id,{}).get("name")),
                                   "value_id":value_id,"value":_label(value.get("name"))})
            candidate_details=[]
            for image_id in list(dict.fromkeys(specific+parent_ids)):
                sources=[]
                if image_id in specific: sources.append("COMBINATION_ASSOCIATION")
                if image_id in parent_ids: sources.append("PARENT_ASSOCIATION")
                candidate_details.append({"image_id":image_id,"sources":sources,
                    "combination_associated":image_id in specific,"parent_associated":image_id in parent_ids,
                    "parent_cover":str(parent.get("id_default_image") or "")==image_id})
            projected=("SAFE_RESOLVABLE" if classification=="SAFE" else
                       "AMBIGUOUS_REMAINING" if classification=="AMBIGUOUS" else
                       "PROTECTED_EXISTING_PRIMARY" if classification=="EXISTING_PRIMARY" else "NO_DATA")
            proof=("Une unique image est explicitement associée à la combinaison."
                   if provenance=="COMBINATION_IMAGE" else
                   "L'image cover du parent est explicitement désignée par id_default_image."
                   if candidate and str(parent.get("id_default_image") or "")==candidate else
                   "Le parent ne possède qu'une unique image candidate."
                   if candidate else None)
            items.append({"drcloud_product_key":product.drcloud_product_key,"product_key":product.drcloud_product_key,
                          "base_name":product.base_name or product.name,"display_name":product.display_name,
                          "product":product.base_name or product.name,
                          "variant":product.variant_name,
                          "variant_name":product.variant_name,
                          "attributes":attributes or [{"option":key,"value":value,"option_id":None,"value_id":None}
                                                       for key,value in product.attributes.items()],
                          "product_id":product.product_id,
                          "combination_id":product.combination_id,"classification":classification,
                          "projected_classification":projected,"ambiguity_cause":cause,
                          "candidate_image_id":candidate,"provenance":provenance,
                          "candidates":specific if specific else parent_ids,"candidate_images":candidate_details,
                          "combination_image_ids":specific,"parent_image_ids":parent_ids,
                          "ambiguity_reason":reason,"deterministic_proof":proof,
                          "family":{"product_id":product.product_id,"base_name":product.base_name or product.name},
                          "existing_primary":({"media_id":current.media_id,"product_key":current.product_key,
                            "source":current.source.value,"storage_reference":current.storage_reference} if current else None),
                          "existing_primary_source":current.source.value if current else None,
                          "already_imported":already})
        # Deepen only the diagnosis of items that were already ambiguous.  Their base
        # classification stays AMBIGUOUS, so APPLY can never consume this preview rule.
        local_by_combination={str(item["combination_id"]):item for item in items
                              if item["combination_id"] is not None}
        ambiguous_parent_ids={str(item["product_id"]) for item in items
                              if item["classification"]=="AMBIGUOUS"}
        family_matrices={}
        for parent_id in ambiguous_parent_ids:
            rows=combinations_by_parent.get(parent_id,[])
            # Some test/legacy payloads omit id_product.  The locally known siblings
            # remain exact and ensure the report is useful without inventing links.
            if not rows:
                ids={str(item["combination_id"]) for item in items
                     if str(item["product_id"])==parent_id and item["combination_id"] is not None}
                rows=[combinations[cid] for cid in ids if cid in combinations]
            sets={str(row["id"]):_ids(row) for row in rows}
            union=sorted(set().union(*(set(v) for v in sets.values())),key=int) if sets else []
            intersection=sorted(set.intersection(*(set(v) for v in sets.values())),key=int) if sets else []
            matrix=[]
            for combination_id,candidates in sorted(sets.items(),key=lambda pair:int(pair[0])):
                local=local_by_combination.get(combination_id)
                decision=combination_exclusivity(candidates,[values for cid,values in sets.items()
                    if cid!=combination_id],explicitly_associated_image_ids=candidates)
                matrix.append({"parent":parent_id,"product_id":parent_id,
                    "combination_id":combination_id,
                    "variant_name":local["variant_name"] if local else None,
                    "candidate_image_ids":candidates,**{key:decision[key] for key in
                    ("shared_image_ids","exclusive_image_ids")}})
                if local and local["classification"]=="AMBIGUOUS":
                    local.update({"shared_image_ids":decision["shared_image_ids"],
                        "exclusive_image_ids":decision["exclusive_image_ids"],
                        "projected_classification":decision["classification"],
                        "candidate_image_id":decision["candidate_image_id"],
                        "provenance":"COMBINATION_EXCLUSIVITY" if decision["candidate_image_id"] else None,
                        "deterministic_proof":decision["proof"],
                        "exclusivity_contradictions":decision["contradictions"]})
            family_matrices[parent_id]={"product_id":parent_id,"union_image_ids":union,
                "intersection_image_ids":intersection,"matrix":matrix}
        summary={"processed":len(items),"combination_image":sum(x["provenance"]=="COMBINATION_IMAGE" for x in items),
                 "parent_fallback":sum(x["provenance"]=="PARENT_FALLBACK" for x in items),
                 "existing_primary":sum(x["classification"]=="EXISTING_PRIMARY" for x in items),
                 "no_image":sum(x["classification"]=="NO_IMAGE" for x in items),
                 "ambiguous":sum(x["classification"]=="AMBIGUOUS" for x in items),
                 "candidate_images":sum(bool(x["candidate_image_id"]) for x in items),
                 "downloads_required":sum(x["classification"]=="SAFE" and not x["already_imported"] for x in items)}
        ambiguous=[x for x in items if x["classification"]=="AMBIGUOUS"]
        causes={cause:sum(x["ambiguity_cause"]==cause for x in ambiguous)
                for cause in sorted({x["ambiguity_cause"] for x in ambiguous})}
        families={str(pid):{"product_id":pid,"base_name":group[0]["base_name"],"count":len(group),
                            "ambiguous_variants":len(group),
                            "common_image_ids":family_matrices.get(str(pid),{}).get("intersection_image_ids",[]),
                            "union_image_ids":family_matrices.get(str(pid),{}).get("union_image_ids",[]),
                            "matrix":family_matrices.get(str(pid),{}).get("matrix",[]),
                            "safe_by_combination_exclusivity":sum(x["projected_classification"]=="SAFE_BY_COMBINATION_EXCLUSIVITY" for x in group),
                            "ambiguous_remaining":sum(x["projected_classification"]=="AMBIGUOUS_REMAINING" for x in group),
                            "causes":sorted({x["ambiguity_cause"] for x in group})}
                  for pid in sorted({x["product_id"] for x in ambiguous},key=lambda value:int(value))
                  for group in [[x for x in ambiguous if x["product_id"]==pid]]}
        catalogue_keys={p.drcloud_product_key for p in self.catalogue.all()}
        missing=[]
        for media in primary_snapshot.values():
            try: self.media.storage.path(media.storage_reference)
            except (FileNotFoundError, MediaError): missing.append(media.media_id)
        integrity={"distinct_products_with_primary":len(primary_snapshot),
                   "primary_rows":len(primary_snapshot),"missing_primary_files":missing,
                   "duplicate_primary_count":self.media.repository.db.execute("""SELECT COUNT(*) FROM
                     (SELECT product_key FROM product_media WHERE active=1 AND role='PRIMARY'
                      GROUP BY product_key HAVING COUNT(*)>1)""").fetchone()[0],
                   "orphan_primary_product_keys":sorted(set(primary_snapshot)-catalogue_keys),
                   "primary_product_associations_correct":all(k==m.product_key for k,m in primary_snapshot.items())}
        summary.update({"safe_resolvable":sum(x["projected_classification"]=="SAFE_RESOLVABLE" for x in ambiguous),
                        "safe_by_combination_exclusivity":sum(x["projected_classification"]=="SAFE_BY_COMBINATION_EXCLUSIVITY" for x in ambiguous),
                        "ambiguous_remaining":sum(x["projected_classification"]=="AMBIGUOUS_REMAINING" for x in ambiguous),
                        "ambiguity_causes":causes,"ambiguous_families":len(families)})
        return {"job_type":JOB_TYPE,"mode":"PREVIEW","read_only":True,"summary":summary,
                "families":list(families.values()),"primary_integrity":integrity,"items":items}

    def apply(self, preview: dict[str, Any] | None = None, *, actor="authenticated", batch_size=None) -> dict[str, Any]:
        # The UI preview is informative only: APPLY always reads PrestaShop and local PRIMARYs again.
        current=self.preview()
        # This operation deliberately has a narrower gate than the historic generic
        # importer: only the independently proven exclusivity classification is legal.
        candidates=[x for x in current["items"]
                    if x["classification"]=="AMBIGUOUS"
                    and x["projected_classification"]=="SAFE_BY_COMBINATION_EXCLUSIVITY"
                    and x["candidate_image_id"] and x["provenance"]=="COMBINATION_EXCLUSIVITY"
                    and not x["already_imported"]]
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
            protected_keys=set(self.media.repository.primaries())
            protected_before=self._media_snapshot(protected_keys)
            result={"processed":0,"imported":0,"skipped":0,"failed":0,"errors":[],
                    "backup_id":backup["backup_id"],"repreview":current["summary"],
                    "primary_before":len(protected_keys)}
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
            primary_after=self.media.repository.primaries()
            catalogue_by_key={p.drcloud_product_key:p for p in self.catalogue.all()}
            catalogue_keys=set(catalogue_by_key)
            orphan_count=self.media.repository.db.execute("""SELECT COUNT(*) FROM product_media
                WHERE active=1 AND product_key NOT IN (SELECT drcloud_product_key FROM drcloud_products)""").fetchone()[0]
            wrong_sources=[]
            for row in self.media.repository.db.execute("""SELECT media_id,product_key,source_reference
                    FROM product_media WHERE active=1 AND source='PRESTASHOP'"""):
                try:
                    reference=json.loads(row[2]); product=catalogue_by_key[row[1]]
                    valid=(str(reference.get("product_id"))==str(product.product_id)
                           and reference.get("combination_id")==(
                               str(product.combination_id) if product.combination_id is not None else None))
                except (KeyError,TypeError,ValueError,json.JSONDecodeError):
                    valid=False
                if not valid: wrong_sources.append(row[0])
            protected_unchanged=protected_before==self._media_snapshot(protected_keys)
            result.update({"total_products":len(after),"products_with_primary":len(primary_after),
                "products_without_primary":sum(not self.media.primary(p.drcloud_product_key) for p in self.catalogue.all()),
                "primary_after":len(primary_after),
                "ignored_primary_existing":current["summary"]["existing_primary"],
                "ambiguous":current["summary"]["ambiguous_remaining"],
                "safe_by_combination_exclusivity":current["summary"]["safe_by_combination_exclusivity"],
                "no_data":current["summary"]["no_image"],"media_files_present":diagnostics["active_assets"]-diagnostics["missing_files"],
                "missing_files":diagnostics["missing_files"],"corrupt_files":diagnostics["corrupt_files"],
                "duplicate_product_media":duplicates,"orphan_product_media":orphan_count,
                "wrong_prestashop_associations":wrong_sources,
                "primary_product_associations_correct":all(key in catalogue_keys and key==media.product_key
                                                             for key,media in primary_after.items()),
                "protected_primary_count":len(protected_keys),"protected_primary_unchanged":protected_unchanged,
                "identities_unchanged":identities==after,
                "business_data_unchanged":business_before==self._business_fingerprint()})
            if (not result["identities_unchanged"] or not result["business_data_unchanged"]
                    or not protected_unchanged):
                raise RuntimeError("Invariant métier modifié pendant l'import média")
            self.catalogue.add_activity(ActivityLog(JOB_TYPE,"catalogue","PRODUCT_MEDIA",
                {"actor":actor,"result":"PARTIAL" if result["failed"] else "SUCCESS",
                 "backup_id":backup["backup_id"],**{k:v for k,v in result.items() if k not in {"errors","repreview"}},
                 "error_count":len(result["errors"])}))
            return result
        value=asdict(JobRunner(self.jobs).run(job,operation))
        value["status"]=value["status"].value
        return value

    def manual_candidate(self, product_key: str, image_id: str) -> tuple[dict[str, Any], str]:
        """Return the current, exact ambiguous item containing ``image_id``.

        This is deliberately a fresh PrestaShop read.  Neither a stale browser
        preview nor a guessed identifier is an authority for a manual decision.
        """
        product_key=str(product_key); image_id=str(image_id)
        item=next((row for row in self.preview()["items"]
                   if row["drcloud_product_key"] == product_key), None)
        if item is None: raise KeyError("Produit DrCloud introuvable")
        if item["classification"] != "AMBIGUOUS" or \
                item["projected_classification"] != "AMBIGUOUS_REMAINING":
            raise ValueError("Ce produit n'est plus un cas ambigu à résoudre")
        if image_id not in set(map(str,item["candidates"])):
            raise ValueError("L'image choisie ne fait pas partie des candidates actuelles")
        return item,image_id

    def download_manual_candidate(self, product_key: str, image_id: str) -> tuple[bytes,str]:
        item,image_id=self.manual_candidate(product_key,image_id)
        data,mime=self.client.download_product_image(item["product_id"],image_id,max_bytes=MAX_FILE_SIZE)
        if mime not in ALLOWED_MIME: raise MediaError(f"Content-Type PrestaShop refusé: {mime}")
        return data,mime

    def resolve_manual(self, product_key: str, image_id: str, *, actor="authenticated") -> dict[str,Any]:
        """Persist one operator-selected PRIMARY without ever writing to PrestaShop."""
        product_key=str(product_key); image_id=str(image_id)
        existing=self.media.primary(product_key)
        if existing:
            try: reference=json.loads(existing.source_reference or "{}")
            except json.JSONDecodeError: reference={}
            if (existing.source is MediaSource.PRESTASHOP and
                    reference.get("provenance")=="MANUAL_AMBIGUITY_RESOLUTION" and
                    str(reference.get("image_id"))==image_id):
                return {"status":"ALREADY_RESOLVED","product_key":product_key,
                        "image_id":image_id,"media_id":existing.media_id,
                        "summary":self.preview()["summary"]}
            raise ValueError("Ce produit possède déjà une image PRIMARY protégée")
        item,image_id=self.manual_candidate(product_key,image_id)
        backup=self.backup_service.create(self.database,reason="PRESTASHOP_MEDIA_MANUAL_RESOLUTION",
            environment=self.environment,safe_mode=self.safe_mode,application=application_metadata())
        identities=sorted((p.drcloud_product_key,p.product_id,p.combination_id) for p in self.catalogue.all())
        business_before=self._business_fingerprint()
        protected_keys=set(self.media.repository.primaries())
        protected_before=self._media_snapshot(protected_keys)
        # Re-check after the backup, immediately before the only local mutation.
        item,image_id=self.manual_candidate(product_key,image_id)
        if self.media.primary(product_key):
            raise ValueError("Ce produit possède déjà une image PRIMARY protégée")
        data,mime=self.client.download_product_image(item["product_id"],image_id,max_bytes=MAX_FILE_SIZE)
        if mime not in ALLOWED_MIME: raise MediaError(f"Content-Type PrestaShop refusé: {mime}")
        reference=self._reference(item["product_id"],item.get("combination_id"),image_id,
                                  "MANUAL_AMBIGUITY_RESOLUTION")
        media=self.media.add(product_key,data,filename=f"prestashop-{image_id}",
            role=MediaRole.PRIMARY.value,source=MediaSource.PRESTASHOP.value,
            source_reference=reference,marketing_usage=MarketingUsage.UNKNOWN.value,
            protected_original=True,actor=actor)
        after=sorted((p.drcloud_product_key,p.product_id,p.combination_id) for p in self.catalogue.all())
        if (identities!=after or business_before!=self._business_fingerprint() or
                protected_before!=self._media_snapshot(protected_keys)):
            raise RuntimeError("Invariant métier modifié pendant la résolution manuelle")
        self.catalogue.add_activity(ActivityLog("PRESTASHOP_MEDIA_MANUALLY_RESOLVED",product_key,
            "PRODUCT_MEDIA",{"actor":actor,"media_id":media.media_id,"image_id":image_id,
             "product_id":str(item["product_id"]),
             "combination_id":str(item["combination_id"]) if item.get("combination_id") is not None else None,
             "backup_id":backup["backup_id"]}))
        refreshed=self.preview()
        return {"status":"RESOLVED","product_key":product_key,"image_id":image_id,
                "media_id":media.media_id,"backup_id":backup["backup_id"],
                "summary":refreshed["summary"]}

    def _media_snapshot(self, product_keys: set[str]) -> tuple:
        """Capture every media association (including variants) of protected products."""
        if not product_keys:
            return ()
        placeholders=",".join("?" for _ in product_keys)
        keys=sorted(product_keys)
        media_rows=self.media.repository.db.execute(
            f"SELECT * FROM product_media WHERE product_key IN ({placeholders}) ORDER BY media_id",keys).fetchall()
        media_ids=[row[0] for row in media_rows]
        variant_rows=[]
        if media_ids:
            marks=",".join("?" for _ in media_ids)
            variant_rows=self.media.repository.db.execute(
                f"SELECT * FROM product_media_variants WHERE media_id IN ({marks}) ORDER BY media_id,kind",
                media_ids).fetchall()
        return (tuple(tuple(row) for row in media_rows),tuple(tuple(row) for row in variant_rows))

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
