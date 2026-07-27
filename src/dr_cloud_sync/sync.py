"""Catalogue ingestion orchestration. There is intentionally no Shop Caisse adapter."""

from __future__ import annotations

from typing import Any

from .prestashop import PrestaShopClient
from .store import SnapshotStore


def synchronize(client: PrestaShopClient, store: SnapshotStore) -> dict[str, int]:
    """Fetch every master resource before atomically replacing the local snapshot."""
    with store.connect() as connection:
        run_id = store.begin_run(connection)
        try:
            resources: dict[str, list[dict[str, Any]]] = {
                resource: list(client.iter_resource(resource)) for resource in client.RESOURCES
            }
            return store.replace_snapshot(connection, run_id, resources)
        except Exception as exc:
            store.fail_run(connection, run_id, f"{type(exc).__name__}: {exc}")
            raise

