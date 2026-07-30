"""Read-only external observations and conservative canonical product hydration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .domain import ActivityLog, ProductStatus
from .jobs import JobRunner, SqliteJobRepository


SOURCES = {"DRCLOUD", "PRESTASHOP", "SHOPCAISSE", "HISTORICAL_SNAPSHOT", "MANUAL"}
PROTECTED_SOURCES = {"DRCLOUD", "MANUAL"}
FIELDS = ("base_name", "variant_name", "reference", "ean")


def variant_name(attributes: dict[str, str]) -> str:
    """Render resolved option values in their stable source order."""
    return " · ".join(str(value).strip() for value in attributes.values() if str(value).strip())


def valid_ean(value: str) -> bool:
    """Validate GTIN-8/12/13/14 digits and its modulo-10 check digit."""
    value = str(value or "").strip()
    if len(value) not in {8, 12, 13, 14} or not value.isdigit():
        return False
    total = sum(int(char) * (3 if (len(value) - index) % 2 == 0 else 1)
                for index, char in enumerate(value[:-1]))
    return (10 - total % 10) % 10 == int(value[-1])


@dataclass(frozen=True)
class ProductObservation:
    product_key: str
    source: str
    external_id: str
    base_name: str = ""
    variant_name: str = ""
    reference: str = ""
    ean: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    image_url: str = ""
    image_scope: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.source not in SOURCES or not self.product_key or not self.external_id:
            raise ValueError("invalid product observation")


class ProductHydrationService:
    """Persist observations, then update only empty or externally-owned fields."""

    def __init__(self, repository):
        self.repository = repository

    def hydrate(self, observations: Iterable[ProductObservation]) -> dict[str, int]:
        grouped: dict[str, list[ProductObservation]] = {}
        for observation in observations:
            self.repository.save_observation(observation)
            grouped.setdefault(observation.product_key, []).append(observation)
        stats = {"total": len(grouped), "enriched": 0, "unchanged": 0,
                 "incomplete": 0, "conflicts": 0, "errors": 0}
        for key, rows in grouped.items():
            try:
                result = self._hydrate_product(key, rows)
                stats["enriched" if result["changed"] else "unchanged"] += 1
                stats["conflicts"] += bool(result["conflicts"])
                stats["incomplete"] += bool(result["missing"])
            except (KeyError, ValueError):
                stats["errors"] += 1
        return stats

    def _hydrate_product(self, key: str, observations: list[ProductObservation]) -> dict[str, Any]:
        product = self.repository.get(key)
        if product is None:
            raise KeyError(key)
        changes: dict[str, tuple[str, str]] = {}
        conflicts: list[str] = []
        for field_name in FIELDS:
            values = {getattr(row, field_name).strip() for row in observations
                      if getattr(row, field_name).strip()}
            if field_name == "variant_name" and not values:
                values = {variant_name(row.attributes) for row in observations if variant_name(row.attributes)}
            if len(values) > 1:
                conflicts.append(f"{field_name}: sources externes divergentes")
                continue
            if not values:
                continue
            value = next(iter(values))
            if field_name == "ean" and not valid_ean(value):
                conflicts.append("ean: format ou checksum invalide")
                continue
            source = next(row.source for row in observations
                          if getattr(row, field_name).strip() == value or
                          (field_name == "variant_name" and variant_name(row.attributes) == value))
            source_field = {"base_name": "name_source", "variant_name": "variant_source",
                            "reference": "reference_source", "ean": "ean_source"}[field_name]
            current_source = getattr(product, source_field)
            current = getattr(product, field_name)
            if current and current_source in PROTECTED_SOURCES:
                if current != value:
                    conflicts.append(f"{field_name}: surcharge locale conservée")
                continue
            if current != value or current_source != source:
                changes[field_name] = (value, source)
        attributes = next((row.attributes for row in observations if row.attributes), {})
        if attributes and product.attributes != attributes and product.variant_source not in PROTECTED_SOURCES:
            changes["attributes"] = (attributes, next(row.source for row in observations if row.attributes))
        self.repository.apply_commercial_changes(key, changes, conflicts)
        conflicts = [row["reason"] for row in self.repository.diagnostics(key)]
        refreshed = self.repository.get(key)
        missing = [name for name in ("variant_name", "reference", "ean") if not getattr(refreshed, name)]
        return {"changed": bool(changes), "conflicts": conflicts, "missing": missing}

    def update_manual(self, key: str, values: dict[str, Any], actor: str) -> Any:
        changes: dict[str, tuple[Any, str]] = {}
        for name in FIELDS:
            if name in values:
                value = str(values[name] or "").strip()
                if name == "ean" and value and not valid_ean(value):
                    raise ValueError("EAN invalide")
                changes[name] = (value, "MANUAL")
        if "attributes" in values:
            attrs = values["attributes"]
            if not isinstance(attrs, dict):
                raise ValueError("Attributs invalides")
            changes["attributes"] = ({str(k).strip(): str(v).strip() for k, v in attrs.items()
                                      if str(k).strip() and str(v).strip()}, "MANUAL")
        self.repository.apply_commercial_changes(key, changes, [])
        self.repository.add_activity(ActivityLog("PRODUCT_COMMERCIAL_DATA_UPDATED", key, "CATALOGUE",
                                                  {"fields": sorted(changes), "actor": actor}))
        return self.repository.get(key)


def prestashop_observations(client, products) -> list[ProductObservation]:
    """Resolve arbitrary PrestaShop option groups for existing identities only."""
    combinations = {str(x.get("id")): x for x in client.iter_resource("combinations")}
    parents = {str(x.get("id")): x for x in client.iter_resource("products")}
    groups = {str(x.get("id")): _label(x.get("name")) for x in client.iter_resource("product_options")}
    values = {str(x.get("id")): (_label(x.get("name")), groups.get(str(x.get("id_attribute_group") or
              x.get("id_product_option")), "Attribut"), str(x.get("id_attribute_group") or x.get("id_product_option") or ""))
              for x in client.iter_resource("product_option_values")}
    result = []
    for product in products:
        parent, combination = parents.get(str(product.product_id), {}), combinations.get(str(product.combination_id), {})
        associations = combination.get("associations", {}).get("product_option_values", [])
        if isinstance(associations, dict): associations = associations.get("product_option_value", associations)
        if isinstance(associations, dict): associations = [associations]
        attributes = {values[str(x.get("id"))][1]: values[str(x.get("id"))][0]
                      for x in associations or [] if str(x.get("id")) in values}
        details = [{"groupe": values[str(x.get("id"))][1], "groupe_id": values[str(x.get("id"))][2],
                    "valeur": values[str(x.get("id"))][0], "value_id": str(x.get("id"))}
                   for x in associations or [] if str(x.get("id")) in values]
        image_ids = _association_ids(combination.get("associations", {}).get("images", []), "image")
        parent_images = _association_ids(parent.get("associations", {}).get("images", []), "image")
        selected_images, scope = (image_ids, "COMBINATION_IMAGE") if image_ids else (parent_images, "PARENT_FALLBACK")
        result.append(ProductObservation(product.drcloud_product_key, "PRESTASHOP", str(product.combination_id or product.product_id),
          _label(parent.get("name")), variant_name(attributes), str(combination.get("reference") or parent.get("reference") or "").strip(),
          str(combination.get("ean13") or parent.get("ean13") or "").strip(), attributes,
          image_scope=scope if selected_images else "", raw={"upc": combination.get("upc"),
          "isbn": combination.get("isbn"), "mpn": combination.get("mpn"), "active": parent.get("active"),
          "attributes": details, "image_ids": selected_images},))
    return result


def _label(value: Any) -> str:
    if isinstance(value, list):
        value = next((x.get("value") for x in value if isinstance(x, dict) and x.get("value")), "")
    return str(value or "").strip()


def _association_ids(value: Any, singular: str) -> list[str]:
    if isinstance(value, dict): value = value.get(singular, value)
    if isinstance(value, dict): value = [value]
    return [str(row.get("id")) for row in value or [] if isinstance(row, dict) and row.get("id")]


def run_enrichment_job(database, repository, observations, *, idempotency_key: str):
    """Run an idempotent catalogue-enrichment batch through the durable job ledger."""
    jobs=SqliteJobRepository(database)
    job=jobs.create(job_type="CATALOGUE_ENRICHMENT",connector="EXTERNAL_CATALOGUE",
                    operation="HYDRATE_PRODUCTS",idempotency_key=idempotency_key)
    return JobRunner(jobs).run(job,lambda: ProductHydrationService(repository).hydrate(observations))
