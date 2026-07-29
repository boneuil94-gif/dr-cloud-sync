"""Analyse conservatrice, en lecture seule, des exceptions du mapping catalogue."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .mapping import _code, _coherent, _norm


def _report_links(reports: Iterable[dict[str, Any]]) -> dict[str, str]:
    links: dict[str, str] = {}
    for report in reports:
        rows = report.get("resultats", report.get("results", []))
        for row in rows if isinstance(rows, list) else []:
            source = row.get("source") if isinstance(row.get("source"), dict) else row
            key = row.get("prestashop_key") or row.get("source_key") or row.get("key")
            if not key and source.get("product_id") is not None:
                key = f"prestashop:{source['product_id']}"
                if source.get("combination_id") not in (None, "", 0, "0"):
                    key += f":{source['combination_id']}"
            item_id = (row.get("shopcaisse_id") or row.get("shopcaisse_id_cree") or
                       row.get("item_id") or row.get("existing_item_id"))
            status = str(row.get("statut", row.get("status", ""))).upper()
            if key and item_id and status in {"CREATED", "SKIPPED"}:
                links[str(key)] = str(item_id)
    return links


def _initial_actions(reports: Iterable[dict[str, Any]]) -> dict[str, str]:
    actions: dict[str, str] = {}
    for report in reports:
        for row in report.get("entrees", []) if isinstance(report.get("entrees", []), list) else []:
            if row.get("product_id") is None:
                continue
            key = f"prestashop:{row['product_id']}"
            if row.get("combination_id") not in (None, "", 0, "0"):
                key += f":{row['combination_id']}"
            actions[key] = str(row.get("action_prevue", ""))
    return actions


def analyse(mapping: dict[str, Any], shopcaisse: list[dict[str, Any]],
            reports: Iterable[dict[str, Any]] = (),
            current_prestashop: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the exception report and inventory without ever calling an API."""
    by_id = {str(x.get("item_id")): x for x in shopcaisse if x.get("item_id") not in (None, "")}
    indexes: dict[str, dict[str, list[dict[str, Any]]]] = {
        field: defaultdict(list) for field in ("ean", "reference", "name")
    }
    for item in shopcaisse:
        if _code(item.get("ean")):
            indexes["ean"][_code(item.get("ean"))].append(item)
        if _code(item.get("reference")):
            indexes["reference"][_code(item.get("reference"))].append(item)
        indexes["name"][_norm(item.get("name"))].append(item)
    reports = list(reports)
    links = _report_links(reports)
    initial_actions = _initial_actions(reports)
    fresh = {str(x.get("key")): x for x in current_prestashop or []}
    exceptions, inventory = [], []

    for original in mapping.get("mappings", []):
        source = {**original.get("prestashop", {}), **fresh.get(str(original.get("prestashop", {}).get("key")), {})}
        key = str(source.get("key", ""))
        before = original.get("classification")
        after, target, proof = before, original.get("shopcaisse"), original.get("methode", "")
        if before in {"PROBABLE", "NON_TROUVEE"}:
            target = by_id.get(links.get(key, ""))
            if target:
                after, proof = "CERTAINE", "Rapport d'import: item_id déterministe et présent dans le catalogue GET"
            else:
                ean = indexes["ean"].get(_code(source.get("ean")), []) if _code(source.get("ean")) else []
                ref = indexes["reference"].get(_code(source.get("reference")), []) if _code(source.get("reference")) else []
                if len(ean) == 1 and len(ref) == 1 and str(ean[0].get("item_id")) != str(ref[0].get("item_id")):
                    after, target, proof = "CONFLIT", None, "EAN et référence désignent deux articles distincts"
                elif len(ean) > 1 or (not ean and len(ref) > 1):
                    after, target, proof = "AMBIGUE", None, "Identifiant dupliqué dans ShopCaisse"
                elif len(ean) == 1:
                    after, target, proof = "CERTAINE", ean[0], "EAN exact unique dans le catalogue ShopCaisse GET"
                elif len(ref) == 1:
                    after, target, proof = "CERTAINE", ref[0], "Référence exacte unique dans le catalogue ShopCaisse GET"
                else:
                    named = [x for x in indexes["name"].get(_norm(source.get("name")), [])
                             if _coherent(source.get("price_ttc"), x.get("price_ttc"))]
                    if len(named) == 1:
                        target = named[0]
                        after, proof = "PROBABLE", "Nom et prix concordants seulement; preuve non déterministe"
                    else:
                        target = None
                        proof = "Aucun identifiant exact unique partagé"

            causes: list[str] = []
            if after == "NON_TROUVEE":
                if initial_actions.get(key) and initial_actions[key] != "PRET_A_CREER":
                    causes.append("PRODUIT_NON_IMPORTE")
                if not source.get("ean"): causes.append("EAN_ABSENT")
                if not source.get("reference"): causes.append("REFERENCE_ABSENTE")
                if source.get("combination_id") not in (None, "", 0, "0"): causes.append("DECLINAISON")
                if not source.get("name") or source.get("price_ttc") is None: causes.append("DONNEES_PRESTASHOP_INSUFFISANTES")
                if not causes: causes.append("ARTICLE_SHOPCAISSE_MANQUANT")
            elif after == "PROBABLE":
                causes = ["EAN_ABSENT" if not source.get("ean") else "AUTRE",
                          "REFERENCE_ABSENTE" if not source.get("reference") else "NOM_DIFFERENT"]
            else:
                causes = []
            exceptions.append({
                "prestashop_key": key, "product_id": source.get("product_id"),
                "combination_id": source.get("combination_id"), "name": source.get("name"),
                "classification_avant": before, "classification_apres": after,
                "shopcaisse_item_id": target.get("item_id") if target else None,
                "preuve": proof + (f"; plan initial={initial_actions[key]}" if key in initial_actions else ""),
                "cause": causes,
                "action_recommandee": ("Valider le lien déterministe" if after == "CERTAINE" else
                                        "Vérifier manuellement et compléter un identifiant partagé"),
            })
        status = after
        chosen = target or original.get("shopcaisse") or {}
        inventory.append({
            "prestashop_key": key, "product_id": source.get("product_id"),
            "combination_id": source.get("combination_id"), "nom_complet": source.get("name"),
            "ean": source.get("ean"), "reference": source.get("reference"),
            "shopcaisse_item_id": chosen.get("item_id"), "stock_prestashop": source.get("stock"),
            "stock_shopcaisse": chosen.get("stock"), "mapping_status": status,
        })

    counts = {status: sum(x["classification_apres"] == status for x in exceptions)
              for status in ("CERTAINE", "PROBABLE", "NON_TROUVEE", "AMBIGUE", "CONFLIT")}
    report = {"total_analyse": len(exceptions), "resolues_certaines": counts["CERTAINE"],
              "restent_probables": counts["PROBABLE"], "restent_non_trouvees": counts["NON_TROUVEE"],
              "ambigues": counts["AMBIGUE"], "conflits": counts["CONFLIT"], "exceptions": exceptions}
    return report, {"total": len(inventory), "articles": inventory}


def run(mapping_path: Path, output: Path, shopcaisse: list[dict[str, Any]],
        reports: Iterable[dict[str, Any]] = (), current_prestashop: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    mapping = json.loads(mapping_path.read_text())
    report, inventory = analyse(mapping, shopcaisse, reports, current_prestashop)
    output.mkdir(parents=True, exist_ok=True)
    (output / "rapport-exceptions-mapping.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (output / "liste-inventaire-drcloud.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")
    return report
