import pytest

from connector_sdk import ConnectorError
from mysql_connector.db import Session
from mysql_connector.grants import check_grants
from mysql_connector.schema import read_schema, visible_schemas
from mysql_connector.server import routes
from tests.conftest import config


def call(cfg, path, body=None):
    return routes(cfg)[path](body or {})


def test_a_read_only_account_passes_and_a_writing_account_is_flagged(data):
    good = call(config("reader"), "/v1/check")
    assert good["ok"] and good["read_only"] and good["violations"] == []
    assert good["tls"] is True and good["server_version"].startswith("8.4")
    assert good["schemas"] == ["shop"]  # the allow-list hides `hidden`
    assert any("not verified" in w for w in good["warnings"])  # fixture uses a self-signed certificate

    bad = call(config("writer"), "/v1/check")
    assert not bad["ok"] and not bad["read_only"]
    assert any(v.startswith("INSERT") for v in bad["violations"]) and any(v.startswith("UPDATE") for v in bad["violations"])

    power = call(config("power"), "/v1/check")
    assert any("ALL PRIVILEGES" in v for v in power["violations"]) and any("GRANT OPTION" in v for v in power["violations"])


def test_without_an_allow_list_every_visible_schema_is_offered(data):
    session = Session(config("reader", schemas=()))
    try:
        assert visible_schemas(session, config("reader", schemas=())) == ["hidden", "shop"]
    finally:
        session.close()


def test_bad_credentials_fail_without_echoing_them(data):
    cfg = config("reader", password="definitely-wrong-password-123")
    with pytest.raises(ConnectorError) as raised:
        Session(cfg)
    assert raised.value.code == "forbidden"
    assert "definitely-wrong-password-123" not in raised.value.message and "reader" not in raised.value.message


def test_a_verified_connection_to_a_self_signed_server_is_refused(data):
    with pytest.raises(ConnectorError):
        Session(config("reader", tls_verify=True))  # no CA is configured, so verification must fail


def test_schema_matches_what_the_generator_created(data):
    session = Session(config("reader"))
    try:
        found = read_schema(session, config("reader"), ["shop"])
    finally:
        session.close()
    assert [s["name"] for s in found["schemas"]] == ["shop"]
    tables = {t["name"]: t for t in found["schemas"][0]["tables"]}
    assert set(tables) == {"customers", "products", "orders", "order_items", "tickets", "audit_log", "events", "v_open_tickets"}
    assert tables["v_open_tickets"]["type"] == "view" and tables["customers"]["type"] == "table"
    customers = tables["customers"]
    assert [c["name"] for c in customers["columns"]] == ["id", "first_name", "last_name", "email", "phone", "country", "status", "notes", "created_at", "birth_date"]
    assert customers["primary_key"] == ["id"] and customers["comment"] == "People and companies we sell to"
    assert any(ix["name"] == "idx_country" and ix["columns"] == ["country"] and not ix["unique"] for ix in customers["indexes"])
    by_name = {c["name"]: c for c in customers["columns"]}
    assert by_name["status"]["column_type"] == "enum('active','inactive','banned')" and not by_name["status"]["nullable"]
    assert {n: by_name[n]["sensitivity"] for n in ("email", "first_name", "phone", "birth_date", "status", "country")} == {
        "email": "email", "first_name": "name", "phone": "phone", "birth_date": "birth_date", "status": None, "country": None}
    assert tables["order_items"]["primary_key"] == ["order_id", "product_id"]
    assert tables["audit_log"]["primary_key"] == [] and tables["audit_log"]["indexes"] == []
    fk = tables["orders"]["foreign_keys"][0]
    assert fk["name"] == "fk_orders_customer" and fk["columns"] == ["customer_id"]
    assert fk["references"] == {"schema": "shop", "table": "customers", "columns": ["id"]}
    assert {f["references"]["table"] for f in tables["order_items"]["foreign_keys"]} == {"orders", "products"}
    assert any(ix["unique"] and ix["columns"] == ["sku"] for ix in tables["products"]["indexes"])


def test_the_schema_call_refuses_an_account_that_can_write(data):
    with pytest.raises(ConnectorError) as raised:
        call(config("writer"), "/v1/schema")
    assert raised.value.code == "forbidden" and "not read-only" in raised.value.message


def test_schemas_outside_the_allow_list_do_not_exist_for_the_caller(data):
    with pytest.raises(ConnectorError) as raised:
        call(config("reader"), "/v1/schema", {"schemas": ["hidden"]})
    assert raised.value.code == "not_found"


def test_grants_parser_handles_column_level_and_usage_privileges():
    class Fake:
        def __init__(self, lines):
            self.lines = lines

        def query(self, sql, params=()):
            return [(line,) for line in self.lines]

    ok, _ = check_grants(Fake(["GRANT USAGE ON *.* TO `u`@`%`", "GRANT SELECT (id, name) ON `shop`.`t` TO `u`@`%`", "GRANT SHOW VIEW ON `shop`.* TO `u`@`%`"]))
    assert ok == []
    bad, _ = check_grants(Fake(["GRANT SELECT, FILE ON *.* TO `u`@`%`", "GRANT `analyst`@`%` TO `u`@`%`", "GRANT SELECT ON `shop`.* TO `u`@`%` WITH GRANT OPTION"]))
    assert any(v.startswith("FILE") for v in bad) and any("roles" in v for v in bad) and any("GRANT OPTION" in v for v in bad)
