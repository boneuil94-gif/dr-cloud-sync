from dr_cloud_sync.inventory_web import InventoryApp


class _CountResult:
    def __init__(self, count):
        self._count = count

    def fetchone(self):
        return (self._count,)


class _FakeDB:
    def __init__(self, ledger):
        self.ledger = ledger

    def execute(self, sql):
        assert sql == "SELECT count(*) FROM sumup_merchants"
        return _CountResult(self.ledger.count)


class _FakeLedger:
    def __init__(self, *, updated_at="2026-08-27T15:43:00Z", imported_at="2099-12-31T23:59:59Z"):
        self.count = 0
        self.db = _FakeDB(self)
        self.updated_at = updated_at
        self.imported_at = imported_at
        self.imported_payload = None

    def import_merchant(self, payload):
        self.imported_payload = payload
        self.count = 1
        return {
            "merchant_code": "never-emitted",
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": self.updated_at,
            "imported_at": self.imported_at,
        }


class _FakeProvider:
    def merchant(self):
        return {
            "merchant_code": "never-emitted",
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2026-08-27T15:43:00Z",
        }


def _app(ledger=None):
    app = object.__new__(InventoryApp)
    app.sumup_provider = _FakeProvider()
    app.sumup_transactions = ledger or _FakeLedger()
    return app


def test_sumup_merchant_operation_projects_documented_provider_updated_at_only():
    ledger = _FakeLedger()
    result = _app(ledger).automation_operations()["SUMUP_MERCHANT"](None)

    assert result == {
        "rows_imported": 1,
        "records_available": 1,
        "data_max_at": "2026-08-27T15:43:00Z",
        "cursor": None,
    }
    assert ledger.imported_payload["updated_at"] == result["data_max_at"]
    assert ledger.imported_at not in repr(result)


def test_sumup_merchant_operation_never_uses_imported_at_as_progress():
    ledger = _FakeLedger(
        updated_at="2026-08-27T15:43:00+00:00",
        imported_at="2099-12-31T23:59:59Z",
    )
    result = _app(ledger).automation_operations()["SUMUP_MERCHANT"](None)

    assert result["data_max_at"] == ledger.updated_at
    assert result["data_max_at"] != ledger.imported_at


def test_sumup_merchant_operation_fails_closed_if_validated_updated_at_is_missing():
    class MissingWatermarkLedger(_FakeLedger):
        def import_merchant(self, payload):
            self.count = 1
            return {"merchant_code": "never-emitted", "created_at": "2020-01-01T00:00:00Z"}

    app = _app(MissingWatermarkLedger())

    try:
        app.automation_operations()["SUMUP_MERCHANT"](None)
    except KeyError as exc:
        assert exc.args == ("updated_at",)
    else:
        raise AssertionError("missing validated provider updated_at must fail closed")
