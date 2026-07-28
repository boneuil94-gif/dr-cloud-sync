"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import ConfigurationError, Settings
from .prestashop import PrestaShopClient, PrestaShopError
from .shopcaisse import ShopCaisseError, pull_and_write
from .store import SnapshotStore
from .sync import synchronize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importe le catalogue maître PrestaShop")
    parser.add_argument("command", choices=["pull", "shopcaisse-pull"], help="Récupérer un snapshot complet")
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
    else:
        try:
            counts = pull_and_write(os.environ.get("SHOPCAISSE_API_KEY", ""),
                                    Path("dist/catalogue-prestashop-reconstruit.json"), Path("dist"))
        except (ShopCaisseError, ValueError, json.JSONDecodeError) as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"status": "completed", "source": "shopcaisse", "counts": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
