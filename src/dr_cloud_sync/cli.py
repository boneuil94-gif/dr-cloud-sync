"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys

from .config import ConfigurationError, Settings
from .prestashop import PrestaShopClient, PrestaShopError
from .store import SnapshotStore
from .sync import synchronize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importe le catalogue maître PrestaShop")
    parser.add_argument("command", choices=["pull"], help="Récupérer un snapshot complet")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

