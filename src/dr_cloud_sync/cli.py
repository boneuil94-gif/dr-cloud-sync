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
from .os_admin import backup, init_catalog
from .os_config import OSSettings
from .rehydration import (CatalogueRehydrationService, historical_observations,
                          packaged_historical_snapshot, run_rehydration_job)
from .repositories import SQLiteOSRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importe le catalogue maître PrestaShop")
    parser.add_argument("command", choices=["pull", "shopcaisse-pull", "shopcaisse-import-dry-run", "shopcaisse-import-pilot", "shopcaisse-import-controlled", "shopcaisse-import-all", "build-catalog-mapping", "analyse-mapping-exceptions", "create-mapping-exceptions", "build-final-mapping", "inventory-serve", "os-serve", "automation-worker", "sumup-migrate", "os-init-catalog", "os-backup", "catalogue-rehydrate"], help="Commande DrCloud")
    parser.add_argument("--apply-safe", action="store_true", help="Applique explicitement les seuls enrichissements SAFE")
    parser.add_argument("--snapshot", type=Path, default=packaged_historical_snapshot())
    parser.add_argument("--report", type=Path, default=Path("dist/rapport-rehydratation-catalogue.json"))
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
                                    packaged_historical_snapshot(), Path("dist"))
        except (ShopCaisseError, ValueError, json.JSONDecodeError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "completed", "source": "shopcaisse", "counts": counts}, sort_keys=True))
    elif args.command == "shopcaisse-import-dry-run":
        try:
            counts = run_import_dry_run(os.environ.get("SHOPCAISSE_API_KEY", ""),
                                        packaged_historical_snapshot(), Path("dist"),
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
    elif args.command == "inventory-serve":
        serve_inventory(
            Path(os.environ.get("INVENTORY_CATALOGUE", "dist/mapping-prestashop-shopcaisse-final.json")),
            Path(os.environ.get("INVENTORY_MAPPING_REPORT", "dist/rapport-mapping-final.json")),
            Path(os.environ.get("INVENTORY_DATABASE", "inventory.sqlite3")),
            os.environ.get("INVENTORY_HOST", "127.0.0.1"), int(os.environ.get("INVENTORY_PORT", "8080")))
    elif args.command == "os-init-catalog":
        settings=OSSettings.from_env(require_secrets=False)
        count=init_catalog(Path(os.environ.get("INVENTORY_CATALOGUE", "dist/mapping-prestashop-shopcaisse-final.json")), Path(os.environ.get("INVENTORY_MAPPING_REPORT", "dist/rapport-mapping-final.json")), settings.database)
        print(json.dumps({"status":"initialized","products":count,"database":str(settings.database)}))
    elif args.command == "os-backup":
        settings=OSSettings.from_env(require_secrets=False); target=backup(settings.database,settings.data_dir / "backups" if not os.environ.get("DRCLOUD_BACKUP_DIR") else Path(os.environ["DRCLOUD_BACKUP_DIR"]),environment=settings.environment,safe_mode=settings.safe_mode)
        print(json.dumps({"status":"backup-created","path":str(target)}))
    elif args.command == "catalogue-rehydrate":
        settings=OSSettings.from_env(require_secrets=False)
        repository=SQLiteOSRepository(settings.database, [])
        observations=historical_observations(args.snapshot, repository.all())
        service=CatalogueRehydrationService(repository, backup=lambda: backup(
          settings.database, Path(os.environ.get("DRCLOUD_BACKUP_DIR", settings.data_dir / "backups")),
          environment=settings.environment, safe_mode=settings.safe_mode))
        preview=service.preview(observations)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(preview,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        job=run_rehydration_job(settings.database,service,observations,apply=args.apply_safe,
                                actor=os.environ.get("DRCLOUD_ACTOR","cli-admin"))
        print(json.dumps({"status":"applied" if args.apply_safe else "dry-run",
                          "job_id":job.job_id,"summary":job.summary,"report":str(args.report)},ensure_ascii=False))
    elif args.command == "automation-worker":
        import time
        from .inventory_web import create_app
        settings=OSSettings.from_env(); app=create_app(settings)
        from .sqlite_diagnostics import register_runtime
        register_runtime(app.bank.db, "automation-worker")
        delay=int(os.environ.get("AUTOMATION_TICK_SECONDS","30"))
        while True:
            try:
                app.data_hub.heartbeat()
                app.data_hub.run_due(app.automation_operations())
            except Exception as exc: print(f"automation cycle degraded: {type(exc).__name__}",file=sys.stderr)
            time.sleep(delay)
    elif args.command == "sumup-migrate":
        import sqlite3
        from .sumup import SCHEMA
        from .sumup_migrations import migrate_sumup_schema
        from .sqlite_diagnostics import register_runtime, sqlite_file_diagnostic
        settings=OSSettings.from_env(require_secrets=False)
        with sqlite3.connect(settings.database) as db:
            migration=migrate_sumup_schema(db,SCHEMA);register_runtime(db,"migration-command")
        print(json.dumps({**migration,"sqlite":sqlite_file_diagnostic(
            settings.database,role="migration-command")},ensure_ascii=False))
    else:
        from waitress import serve
        from .inventory_web import create_app
        settings=OSSettings.from_env()
        logging_config = {"version":1,"disable_existing_loggers":False,"formatters":{"default":{"format":"%(asctime)s %(levelname)s %(name)s %(message)s"}},"handlers":{"console":{"class":"logging.StreamHandler","formatter":"default"}},"root":{"handlers":["console"],"level":"INFO"}}
        import logging.config; logging.config.dictConfig(logging_config)
        proxy = {"trusted_proxy":"*", "trusted_proxy_headers":"x-forwarded-for x-forwarded-proto x-forwarded-host"} if settings.trust_proxy else {}
        serve(create_app(settings),host=settings.host,port=settings.port,threads=4,**proxy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
