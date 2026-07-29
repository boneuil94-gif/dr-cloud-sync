"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import ConfigurationError, Settings, resolve_prestashop_api_url
from .controlled_import import run_all_import, run_controlled_import
from .prestashop import PrestaShopClient, PrestaShopError
from .pilot import PilotSafetyError, run_pilot
from .shopcaisse import ShopCaisseClient, ShopCaisseError, pull_and_write, run_import_dry_run
from .store import SnapshotStore
from .sync import synchronize
from .mapping import run as run_mapping
from .mapping import pull_prestashop, pull_shopcaisse
from .exceptions import run as run_exceptions
from .exception_rebuild import run_exception_rebuild
from .final_mapping import FinalMappingError, finalize_mapping
from .inventory_web import serve as serve_inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importe le catalogue maître PrestaShop")
    parser.add_argument("command", choices=["pull", "shopcaisse-pull", "shopcaisse-import-dry-run", "shopcaisse-import-pilot", "shopcaisse-import-controlled", "shopcaisse-import-all", "build-catalog-mapping", "analyse-mapping-exceptions", "create-mapping-exceptions", "build-final-mapping", "inventory-serve"], help="Récupérer un snapshot complet")
    args = parser.parse_args(argv)
    if args.command == "pull":
        try:
            settings = Settings.from_env()
            counts = synchronize(
                PrestaShopClient(
                    settings.api_url,
                    settings.api_key,
                    timeout=settings.timeout_seconds,
                    page_size=settings.page_size,
                ),
                SnapshotStore(settings.database),
            )
        except (ConfigurationError, PrestaShopError, ValueError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "completed", "source": "prestashop", "counts": counts}, sort_keys=True))
    elif args.command == "shopcaisse-pull":
        try:
            counts = pull_and_write(os.environ.get("SHOPCAISSE_API_KEY", ""),
                                    Path("dist/catalogue-prestashop-reconstruit.json"), Path("dist"))
        except (ShopCaisseError, ValueError, json.JSONDecodeError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "completed", "source": "shopcaisse", "counts": counts}, sort_keys=True))
    elif args.command == "shopcaisse-import-dry-run":
        try:
            counts = run_import_dry_run(os.environ.get("SHOPCAISSE_API_KEY", ""),
                                        Path("dist/catalogue-prestashop-reconstruit.json"), Path("dist"),
                                        prestashop_client=PrestaShopClient(
                                            resolve_prestashop_api_url(),
                                            os.environ.get("PRESTASHOP_API_KEY", ""),
                                        ))
        except (ShopCaisseError, ValueError, json.JSONDecodeError, OSError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "dry-run-completed", "counts": counts}, ensure_ascii=False, sort_keys=True))
    elif args.command == "shopcaisse-import-pilot":
        try:
            report = run_pilot(
                os.environ.get("SHOPCAISSE_API_KEY", ""), os.environ.get("SHOPCAISSE_IMPORT_CONFIRM", ""),
                os.environ.get("SHOPCAISSE_COMPANY_ID", ""), Path("config/shopcaisse-import-pilot.json"),
                Path("dist/rapport-import-pilote-shopcaisse.json"),
            )
        except (PilotSafetyError, ShopCaisseError, ValueError, json.JSONDecodeError, OSError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "pilot-completed", "results": report["resultats"]}, ensure_ascii=False))
        if any(result.get("statut") == "FAILED" for result in report["resultats"]):
            return 1
    elif args.command == "shopcaisse-import-controlled":
        try:
            report = run_controlled_import(
                os.environ.get("SHOPCAISSE_API_KEY", ""), os.environ.get("PRESTASHOP_API_KEY", ""),
                os.environ.get("SHOPCAISSE_IMPORT_CONFIRM", ""),
                os.environ.get("SHOPCAISSE_COMPANY_ID", ""),
                Path("dist/plan-import-prestashop-shopcaisse.json"),
                Path("dist/rapport-import-controle-shopcaisse.json"),
            )
        except (PilotSafetyError, ShopCaisseError, ValueError, json.JSONDecodeError, OSError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "controlled-import-completed", "report": report},
                         ensure_ascii=False))
        if report["failed"]:
            return 1
    elif args.command == "shopcaisse-import-all":
        try:
            report = run_all_import(
                os.environ.get("SHOPCAISSE_API_KEY", ""), os.environ.get("PRESTASHOP_API_KEY", ""),
                os.environ.get("SHOPCAISSE_IMPORT_CONFIRM", ""),
                os.environ.get("SHOPCAISSE_COMPANY_ID", ""),
                Path("dist/plan-import-prestashop-shopcaisse.json"),
                Path("dist/rapport-import-final-shopcaisse.json"),
            )
        except (PilotSafetyError, ShopCaisseError, ValueError, json.JSONDecodeError, OSError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "all-import-completed", "report": report}, ensure_ascii=False))
        if report["failed"]:
            return 1
    elif args.command == "build-catalog-mapping":
        try:
            report_paths = [Path("dist") / name for name in (
                "rapport-import-pilote-shopcaisse.json", "rapport-import-controle-shopcaisse.json",
                "rapport-import-final-shopcaisse.json")]
            quality = run_mapping(report_paths=report_paths)
        except (PrestaShopError, ShopCaisseError, ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "mapping-completed", "quality": quality}, ensure_ascii=False))
    elif args.command == "analyse-mapping-exceptions":
        try:
            ps_client = PrestaShopClient(resolve_prestashop_api_url(),
                                         os.environ.get("PRESTASHOP_API_KEY", ""))
            sc_client = ShopCaisseClient(os.environ.get("SHOPCAISSE_API_KEY", ""))
            current_ps = pull_prestashop(ps_client)
            current_sc = pull_shopcaisse(sc_client, os.environ.get("SHOPCAISSE_COMPANY_ID", ""))
            paths = [*Path("dist").glob("rapport-import-*.json"),
                     Path("dist/plan-import-prestashop-shopcaisse.json")]
            reports = [json.loads(path.read_text()) for path in paths]
            report = run_exceptions(Path("dist/mapping-prestashop-shopcaisse.json"), Path("dist"),
                                    current_sc, reports, current_ps)
        except (PrestaShopError, ShopCaisseError, ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "analysis-completed", "report": report}, ensure_ascii=False))
    elif args.command == "create-mapping-exceptions":
        try:
            report = run_exception_rebuild(
                os.environ.get("SHOPCAISSE_API_KEY", ""), os.environ.get("PRESTASHOP_API_KEY", ""),
                os.environ.get("SHOPCAISSE_EXCEPTION_CONFIRM", ""), os.environ.get("SHOPCAISSE_COMPANY_ID", ""),
                Path("dist/rapport-exceptions-mapping.json"), Path("dist/mapping-corrections-exceptions.json"),
                Path("dist/rapport-creation-exceptions.json"),
                prestashop_api_url=os.environ.get("PRESTASHOP_API_URL"),
                prestashop_loader=lambda url: pull_prestashop(PrestaShopClient(
                    url, os.environ.get("PRESTASHOP_API_KEY", ""))),
            )
        except (PilotSafetyError, PrestaShopError, ShopCaisseError, ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "exception-rebuild-completed", "report": report}, ensure_ascii=False))
        if report["failed"] or not report["complete"]:
            return 1
    elif args.command == "build-final-mapping":
        try:
            report = finalize_mapping(
                Path("dist/mapping-prestashop-shopcaisse.json"),
                Path("dist/mapping-corrections-exceptions.json"),
                Path("dist/rapport-creation-exceptions.json"), Path("dist"),
                os.environ.get("SHOPCAISSE_COMPANY_ID", ""),
                ShopCaisseClient(os.environ.get("SHOPCAISSE_API_KEY", "")),
            )
        except (FinalMappingError, ShopCaisseError, ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "final-mapping-built", "report": report}, ensure_ascii=False))
        if not report["ready_for_inventory"]:
            return 1
    else:
        serve_inventory(
            Path(os.environ.get("INVENTORY_CATALOGUE", "dist/mapping-prestashop-shopcaisse-final.json")),
            Path(os.environ.get("INVENTORY_MAPPING_REPORT", "dist/rapport-mapping-final.json")),
            Path(os.environ.get("INVENTORY_DATABASE", "inventory.sqlite3")),
            os.environ.get("INVENTORY_HOST", "127.0.0.1"), int(os.environ.get("INVENTORY_PORT", "8080")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
