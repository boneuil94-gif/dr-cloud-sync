from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from dr_cloud_sync.config import ConfigurationError, Settings
from dr_cloud_sync.prestashop import PrestaShopClient
from dr_cloud_sync.store import SnapshotStore
from dr_cloud_sync.sync import synchronize


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_fetches_all_resources_and_paginates(tmp_path: Path):
    calls = []

    def opener(request, timeout):
        calls.append(request)
        parsed = urlparse(request.full_url)
        resource = parsed.path.rsplit("/", 1)[-1]
        offset = int(parse_qs(parsed.query)["limit"][0].split(",")[0])
        rows = [{"id": str(offset + 1), "ean13": "3760000000001"}] if offset == 0 else []
        return Response({resource: rows})

    client = PrestaShopClient("https://example.test/api", "super-secret", page_size=1, opener=opener)
    database = tmp_path / "snapshot.sqlite3"
    counts = synchronize(client, SnapshotStore(database))

    assert counts == {resource: 1 for resource in client.RESOURCES}
    assert len(calls) == len(client.RESOURCES) * 2
    assert all("super-secret" not in call.full_url for call in calls)
    with SnapshotStore(database).connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM prestashop_entities").fetchone()[0] == 5
        assert connection.execute("SELECT status FROM sync_runs").fetchone()[0] == "completed"


def test_accepts_wrapped_single_resource():
    client = PrestaShopClient("https://example.test/api", "key")
    assert client._extract_rows({"products": {"product": {"id": 7}}}, "products") == [{"id": 7}]


def test_requires_secret_and_https(monkeypatch):
    monkeypatch.delenv("PRESTASHOP_API_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        Settings.from_env()
    monkeypatch.setenv("PRESTASHOP_API_KEY", "secret")
    monkeypatch.setenv("PRESTASHOP_API_URL", "http://example.test/api")
    with pytest.raises(ConfigurationError):
        Settings.from_env()
