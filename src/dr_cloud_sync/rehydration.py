"""Conservative, explicit catalogue commercial-data rehydration.

Identity is always the already persisted ``product_id + combination_id`` pair.
Preview is side-effect free; apply requires a successful SQLite/media backup.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib.resources import files
import json
from pathlib import Path
from typing import Any, Callable, Iterable

from .domain import ActivityLog
from .hydration import PROTECTED_SOURCES, ProductObservation, valid_ean, variant_name
from .jobs import JobRunner, SqliteJobRepository

SAFE, AMBIGUOUS, NO_DATA = "SAFE", "AMBIGUOUS", "NO_DATA"

HISTORICAL_SNAPSHOT_NAME = "catalogue-prestashop-reconstruit.json"


class HistoricalCatalogueUnavailable(RuntimeError):
    """Operator-safe failure raised when the immutable source cannot be trusted."""

    def __init__(self, status: str = "indisponible"):
        self.operator_safe = True
        super().__init__(
            "Analyse impossible : la source historique du catalogue n'est pas disponible. "
            f"Source : mapping historique versionné ; statut : {status}. "
            "Action recommandée : vérifier le package déployé puis redéployer ; "
            "l'application reste en lecture seule."
        )


def packaged_historical_snapshot() -> Path:
    """Resolve the installed resource independently of the working directory."""
    return Path(str(files("dr_cloud_sync").joinpath("data", HISTORICAL_SNAPSHOT_NAME)))


def validate_historical_snapshot(path: Path) -> dict[str, Any]:
    """Fail closed on an absent, malformed, or identity-ambiguous source."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        raise HistoricalCatalogueUnavailable("absente ou illisible") from None
    catalogue = document.get("catalogue") if isinstance(document, dict) else None
    if not isinstance(catalogue, list):
        raise HistoricalCatalogueUnavailable("format invalide")
    identities: set[tuple[str, str | None]] = set()
    for parent in catalogue:
        if not isinstance(parent, dict) or parent.get("id", parent.get("product_id")) in (None, ""):
            raise HistoricalCatalogueUnavailable("format invalide")
        product_id = str(parent.get("id") or parent.get("product_id"))
        combinations = parent.get("declinaisons", [])
        if not isinstance(combinations, list):
            raise HistoricalCatalogueUnavailable("format invalide")
        for combination in combinations or [None]:
            combination_id = None
            if combination is not None:
                if not isinstance(combination, dict) or combination.get("id") in (None, ""):
                    raise HistoricalCatalogueUnavailable("format invalide")
                combination_id = str(combination["id"])
            identity = (product_id, combination_id)
            if identity in identities:
                raise HistoricalCatalogueUnavailable("identité PrestaShop dupliquée")
            identities.add(identity)
    if not identities:
        raise HistoricalCatalogueUnavailable("catalogue vide")
    return document


@dataclass
class RehydrationItem:
    product_key: str
    product_id: str
    combination_id: str | None
    current: dict[str, Any]
    candidates: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    classification: str = NO_DATA


def historical_observations(path: Path, products: Iterable[Any]) -> list[ProductObservation]:
    """Read the rich repository snapshot and join solely by persisted IDs."""
    document = validate_historical_snapshot(path)
    parents = {str(row.get("id") or row.get("product_id")): row
               for row in document.get("catalogue", [])}
    observations = []
    for product in products:
        parent = parents.get(str(product.product_id))
        if not parent:
            continue
        combination = None
        if product.combination_id not in (None, "", 0, "0"):
            combination = next((row for row in parent.get("declinaisons", [])
                                if str(row.get("id")) == str(product.combination_id)), None)
            if combination is None:
                continue
        source = combination or parent
        rich = source.get("attributs") or source.get("attributes") or []
        attrs = {}
        for item in rich if isinstance(rich, list) else []:
            if isinstance(item, dict):
                group = item.get("groupe") or item.get("group")
                value = item.get("nom") or item.get("value")
                if group and value:
                    attrs[str(group).strip()] = str(value).strip()
        if isinstance(rich, dict):
            attrs = {str(k).strip(): str(v).strip() for k, v in rich.items() if k and v}
        observations.append(ProductObservation(
            product.drcloud_product_key, "HISTORICAL_SNAPSHOT", str(product.combination_id or product.product_id),
            str(parent.get("nom") or parent.get("name") or "").strip(), variant_name(attrs),
            str(source.get("reference") or "").strip(), str(source.get("ean") or source.get("ean13") or "").strip(),
            attrs, image_scope="HISTORICAL_SNAPSHOT",
            raw={"attributes": rich, "image_ids": source.get("images") or source.get("image_ids") or [],
                 "source": path.name}))
    return observations


class CatalogueRehydrationService:
    """Build full previews and transactionally apply deterministic empty fields."""

    def __init__(self, repository, *, backup: Callable[[], Any] | None = None):
        self.repository, self.backup = repository, backup

    def preview(self, observations: Iterable[ProductObservation]) -> dict[str, Any]:
        grouped: dict[str, list[ProductObservation]] = {}
        for row in observations:
            grouped.setdefault(row.product_key, []).append(row)
        items = [self._analyse(product, grouped.get(product.drcloud_product_key, []))
                 for product in self.repository.all()]
        summary = self._summary(items)
        before = self._quality(self.repository.all())
        projected = dict(before)
        variants_added = sum(not x.current["variant_name"] and bool(x.candidates.get("variant_name"))
                             and x.fields.get("variant_name") == SAFE for x in items)
        projected["variants_known"] += variants_added
        projected["variants_unknown"] -= variants_added
        projected["ean_present"] += sum(not x.current["ean"] and bool(x.candidates.get("ean"))
                                        and x.fields.get("ean") == SAFE for x in items)
        projected["reference_present"] += sum(not x.current["reference"] and bool(x.candidates.get("reference"))
                                              and x.fields.get("reference") == SAFE for x in items)
        projected["ean_missing"] = projected["total"] - projected["ean_present"]
        projected["reference_missing"] = projected["total"] - projected["reference_present"]
        return {"summary": summary, "before": before, "after_projected": projected,
                "items": [asdict(item) for item in items],
                "safe": [asdict(x) for x in items if x.classification == SAFE],
                "ambiguous": [asdict(x) for x in items if x.classification == AMBIGUOUS],
                "no_data": [asdict(x) for x in items if x.classification == NO_DATA]}

    def _analyse(self, product, rows: list[ProductObservation]) -> RehydrationItem:
        current = {name: getattr(product, name) for name in
                   ("base_name", "variant_name", "attributes", "reference", "ean")}
        item = RehydrationItem(product.drcloud_product_key, str(product.product_id),
                               None if product.combination_id is None else str(product.combination_id), current)
        specs = (("base_name", lambda r: r.base_name), ("variant_name", lambda r: r.variant_name or variant_name(r.attributes)),
                 ("attributes", lambda r: r.attributes), ("reference", lambda r: r.reference), ("ean", lambda r: r.ean))
        for name, getter in specs:
            found = [(getter(row), row.source, row) for row in rows if getter(row)]
            distinct = {json.dumps(value, sort_keys=True, ensure_ascii=False) for value, _, _ in found}
            if not found:
                item.fields[name] = NO_DATA
                continue
            if len(distinct) != 1:
                item.fields[name] = AMBIGUOUS; item.reasons.append(f"{name}: sources contradictoires"); continue
            value, source, row = found[0]
            if name == "ean" and not valid_ean(str(value)):
                item.fields[name] = AMBIGUOUS; item.reasons.append("ean: format ou checksum invalide"); continue
            if name == "ean" and any(p.drcloud_product_key != product.drcloud_product_key and p.ean == value
                                     for p in self.repository.all()):
                item.fields[name] = AMBIGUOUS; item.reasons.append("ean: deja utilise"); continue
            source_field = {"base_name":"name_source", "variant_name":"variant_source",
                            "reference":"reference_source", "ean":"ean_source"}.get(name, "variant_source")
            existing, existing_source = current[name], getattr(product, source_field)
            if existing and existing_source in PROTECTED_SOURCES:
                item.fields[name] = SAFE if existing == value else AMBIGUOUS
                if existing != value: item.reasons.append(f"{name}: surcharge locale conservee")
                continue
            item.candidates[name] = value; item.provenance[name] = source
            item.fields[name] = SAFE
            if name == "attributes":
                item.candidates["attributes_detail"] = row.raw.get("attributes", [])
        image_sets = {tuple(str(x) for x in (row.raw.get("image_ids") or [])) for row in rows if row.raw.get("image_ids")}
        if len(image_sets) == 1:
            images = list(next(iter(image_sets))); item.candidates["image_ids"] = images
            item.provenance["image_ids"] = next((r.image_scope for r in rows if r.raw.get("image_ids")), "")
            item.fields["image_ids"] = SAFE if len(images) == 1 else AMBIGUOUS
            if len(images) > 1: item.reasons.append("image_ids: plusieurs candidates; aucun PRIMARY automatique")
        else: item.fields["image_ids"] = AMBIGUOUS if image_sets else NO_DATA
        statuses = set(item.fields.values())
        item.classification = AMBIGUOUS if AMBIGUOUS in statuses else SAFE if SAFE in statuses else NO_DATA
        return item

    @staticmethod
    def _summary(items):
        return {"total": len(items), "processed": len(items),
                "safe": sum(x.classification == SAFE for x in items),
                "ambiguous": sum(x.classification == AMBIGUOUS for x in items),
                "no_data": sum(x.classification == NO_DATA for x in items),
                "products_to_change": sum(any(k in x.candidates and x.current.get(k) != x.candidates[k]
                                              for k in x.current) for x in items),
                "fields_to_change": sum(sum(k in x.candidates and x.current.get(k) != x.candidates[k]
                                            for k in x.current) for x in items)}

    def apply_safe(self, observations: Iterable[ProductObservation], *, actor: str) -> dict[str, Any]:
        observations = list(observations)
        preview = self.preview(observations); before = self._invariants()
        if self.backup is None: raise RuntimeError("sauvegarde prealable requise")
        backup_path = self.backup()  # must succeed before the first write
        for observation in observations:
            self.repository.save_observation(observation)
        changed = fields_changed = 0
        self.repository.add_activity(ActivityLog("CATALOGUE_REHYDRATION_STARTED", "catalogue", "CATALOGUE", {"actor": actor}))
        for raw in preview["safe"]:
            changes = {name: (raw["candidates"][name], raw["provenance"][name])
                       for name in raw["current"] if name in raw["candidates"]
                       and raw["current"][name] != raw["candidates"][name]}
            if changes:
                self.repository.apply_commercial_changes(raw["product_key"], changes, [])
                changed += 1; fields_changed += len(changes)
        after = self._invariants()
        if before != after: raise RuntimeError("invariant catalogue/identite/stock viole")
        summary = {**preview["summary"], "changed": changed,
                   "unchanged": len(preview["items"])-changed, "fields_changed": fields_changed,
                   "errors": 0, "backup": str(backup_path)}
        self.repository.add_activity(ActivityLog("CATALOGUE_REHYDRATION_COMPLETED", "catalogue", "CATALOGUE",
          {"actor": actor, "changed_products": changed, "fields_changed": fields_changed,
           "ambiguous_count": preview["summary"]["ambiguous"]}))
        return summary

    def _invariants(self):
        products = self.repository.all()
        protected_tables = ("stock_movements", "sessions", "counts", "history",
                            "inventory_stock_proposals", "inventory_stock_proposal_lines",
                            "purchase_orders", "purchase_order_lines", "goods_receipts",
                            "goods_receipt_lines")
        preserved = tuple((name, self.repository.db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
                          for name in protected_tables if self._table(name))
        return (len(products), tuple(sorted(p.drcloud_product_key for p in products)), preserved)

    def _table(self, name):
        return bool(self.repository.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

    @staticmethod
    def _quality(products):
        total = len(products)
        return {"total": total, "variants_known": sum(bool(p.variant_name) for p in products),
                "variants_unknown": sum(bool(p.combination_id) and not p.variant_name for p in products),
                "ean_present": sum(bool(p.ean) for p in products),
                "ean_missing": sum(not p.ean for p in products),
                "reference_present": sum(bool(p.reference) for p in products),
                "reference_missing": sum(not p.reference for p in products)}


def run_rehydration_job(database: Path, service: CatalogueRehydrationService,
                        observations: list[ProductObservation], *, apply: bool, actor: str):
    """Record PREVIEW or APPLY_SAFE as an explicit durable JobRun."""
    jobs = SqliteJobRepository(database)
    operation = "APPLY_SAFE" if apply else "PREVIEW"
    job = jobs.create(job_type="CATALOGUE_REHYDRATION", connector="CATALOGUE",
                      operation=operation)
    return JobRunner(jobs).run(job, lambda: service.apply_safe(observations, actor=actor)
                               if apply else service.preview(observations)["summary"])
