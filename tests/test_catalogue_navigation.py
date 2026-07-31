"""Production-shape regression coverage for the read-only Catalogue path."""
import hashlib
import json
import uuid
from io import BytesIO

from PIL import Image

from test_os_production import configured, login, request


def _png():
    output = BytesIO()
    Image.new("RGB", (16, 16), "peachpuff").save(output, "PNG")
    return output.getvalue()


def _install_primary_media(app, count=406):
    content = _png(); digest = hashlib.sha256(content).hexdigest(); stamp = "2026-07-31T12:00:00+00:00"
    products = app.os_repository.all()[:count]
    with app.media.repository.db:
        for product in products:
            token = str(uuid.uuid4()); media_id = f"media:{token}"
            references = {kind: f"{token}/{kind.lower()}.png" for kind in ("original", "thumbnail", "display")}
            for reference in references.values():
                target = app.media.storage.root / reference; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
            app.media.repository.db.execute(
                "INSERT INTO product_media VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (media_id, product.drcloud_product_key, "IMAGE", "PRIMARY", "PRESTASHOP", product.prestashop_key,
                 references["original"], "image/png", 16, 16, len(content), digest, "image.png", "PACKSHOT",
                 "UNKNOWN", 0, '["catalogue"]', stamp, None, stamp, stamp, 1),
            )
            app.media.repository.db.executemany(
                "INSERT INTO product_media_variants VALUES(?,?,?,?,?,?,?,?)",
                [(media_id, kind.upper(), references[kind], "image/png", 16, 16, len(content), digest)
                 for kind in ("thumbnail", "display")],
            )
    return products, content


def _get(app, cookie, filter_name="ALL", search=""):
    status, _, body = request(app, f"/api/catalogue?q={search}&filter={filter_name}", cookie=cookie)
    assert status == "200 OK"
    return json.loads(body)


def test_production_catalogue_filters_media_contract_and_read_only_integrity(configured):
    app, _ = configured
    pictured, image = _install_primary_media(app)
    peach = pictured[0]
    with app.os_repository.db:
        app.os_repository.db.execute(
            "UPDATE drcloud_products SET base_name=?,variant_name=?,name=?,reference=?,ean=? WHERE drcloud_product_key=?",
            ("AL FAKHER CROWN BAR Hyper Max Prime 50K", "PEACH ICE",
             "AL FAKHER CROWN BAR Hyper Max Prime 50K — PEACH ICE", "PEACH-ICE", "3760000000001",
             peach.drcloud_product_key),
        )
        app.os_repository.db.execute("UPDATE drcloud_products SET combination_id=NULL WHERE drcloud_product_key=?", (pictured[1].drcloud_product_key,))
        app.os_repository.db.execute("UPDATE drcloud_products SET ean='3760000000002' WHERE drcloud_product_key=?", (pictured[2].drcloud_product_key,))
        app.os_repository.db.execute("DROP INDEX IF EXISTS ux_products_ean")
        app.os_repository.db.execute("UPDATE drcloud_products SET ean='CONFLICT-EAN' WHERE drcloud_product_key IN (?,?)", (pictured[3].drcloud_product_key, pictured[4].drcloud_product_key))

    existing_tables = {row[0] for row in app.service.repo.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    protected_tables = tuple(table for table in
        ("drcloud_products", "product_media", "product_media_variants", "stock_movements", "counts")
        if table in existing_tables)
    before = {table: app.service.repo.db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in protected_tables}
    _, cookie = login(app)

    all_rows = _get(app, cookie)
    with_image = _get(app, cookie, "WITH_IMAGE")
    without_image = _get(app, cookie, "WITHOUT_IMAGE")
    assert len(all_rows) == 478
    assert len(with_image) == 406
    assert len(without_image) == 72
    assert len(all_rows) == len(with_image) + len(without_image)
    assert len(_get(app, cookie, "WITH_EAN")) == 4
    assert len(_get(app, cookie, "WITHOUT_EAN")) == 474
    assert len(_get(app, cookie, "CONFLICT")) == 2
    assert len(_get(app, cookie, "INCOMPLETE")) == 477
    assert len(_get(app, cookie, "UNKNOWN_VARIANT")) == 476
    assert len(_get(app, cookie, "ALL", "PEACH%20ICE")) == 1
    assert len(_get(app, cookie, "WITH_IMAGE", "PEACH%20ICE")) == 1
    assert _get(app, cookie, "WITHOUT_IMAGE", "PEACH%20ICE") == []

    peach_row = _get(app, cookie, "ALL", "PEACH%20ICE")[0]
    assert peach_row["drcloud_product_key"] == peach.drcloud_product_key
    assert peach_row["variant_name"] == "PEACH ICE"
    assert peach_row["primary_media"]["product_key"] == peach.drcloud_product_key
    assert peach_row["media_url"] == peach_row["primary_media"]["thumbnail_url"]
    status, headers, body = request(app, peach_row["primary_media"]["url"], cookie=cookie)
    assert status == "200 OK" and headers["Content-Type"] == "image/png" and body == image

    media_rows = app.media.repository.db.execute("SELECT * FROM product_media").fetchall()
    assert len(media_rows) == 406
    assert sum(row["role"] == "PRIMARY" and row["active"] for row in media_rows) == 406
    assert len({row["product_key"] for row in media_rows if row["role"] == "PRIMARY" and row["active"]}) == 406
    known_products = {product.drcloud_product_key for product in app.os_repository.all()}
    for row in media_rows:
        assert row["product_key"] in known_products
        content = app.media.storage.read(row["storage_reference"])
        assert hashlib.sha256(content).hexdigest() == row["sha256"]

    after = {table: app.service.repo.db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in protected_tables}
    assert after == before


def test_catalogue_rejects_unknown_filter(configured):
    app, _ = configured; _, cookie = login(app)
    status, _, body = request(app, "/api/catalogue?filter=TYPO", cookie=cookie)
    assert status == "400 Bad Request" and "Filtre catalogue invalide" in body.decode()
