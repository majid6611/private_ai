import json

import pytest

from connector_sdk import ConnectorError
from mysql_connector.db import Session
from mysql_connector.profile import profile_table
from tests.conftest import config


def profile(table, columns=None, limits=None, **overrides):
    cfg = config("reader", **overrides)
    session = Session(cfg)
    try:
        return profile_table(session, cfg, "shop", table, columns, limits or {})
    finally:
        session.close()


def column(result, name):
    return next(c for c in result["columns"] if c["name"] == name)


def test_customers_are_profiled_exactly(data):
    p = profile("customers")
    e = data.expected["customers"]
    assert p["exact"] and p["row_count_exact"] and p["row_count"] == 1200 and p["sampled_rows"] is None
    email = column(p, "email")
    assert email["distinct"] == e["email_distinct"] and email["duplicates"] == e["email_duplicates"] == 7
    assert email["likely_identifier"] is True and email["sensitivity"] == "email"
    assert set(email["suppressed"]) == {"min", "max"} and "top_values" not in email  # 1193 distinct: no list anyway
    first = column(p, "first_name")  # few distinct values: the list would be built, so it is suppressed
    assert first["sensitivity"] == "name" and "top_values" in first["suppressed"] and "top_values" not in first
    assert column(p, "phone")["null_count"] == e["phone_nulls"] and column(p, "notes")["null_count"] == e["notes_nulls"]
    assert column(p, "birth_date")["null_count"] == e["birth_nulls"] and column(p, "id")["null_count"] == 0
    status = column(p, "status")
    assert status["distinct"] == 3 and status["top_values_complete"] is True
    assert {t["value"]: t["count"] for t in status["top_values"]} == e["status_counts"]
    assert column(p, "country")["distinct"] == e["country_distinct"]
    assert column(p, "id")["min"] == 1 and column(p, "id")["max"] == 1200
    assert "distinct" not in column(p, "notes") and column(p, "notes")["length"]["max"] > 0  # long text: no distinct count
    created = column(p, "created_at")
    assert created["min"] < created["max"] and created["min"].startswith("202")


def test_personal_data_never_appears_in_a_profile(data):
    dumped = json.dumps(profile("customers"))
    assert "@example.com" not in dumped and "+1 403" not in dumped
    assert column(profile("customers"), "first_name")["sensitivity"] == "name"


def test_orphans_duplicates_and_mixed_formats_are_found(data):
    orders = profile("orders")
    fk = orders["foreign_keys"][0]
    assert fk["name"] == "fk_orders_customer" and fk["orphans"] == data.expected["orders"]["orphans_customer"] == 5
    legacy = column(orders, "legacy_date")
    assert legacy["mixed_date_formats"] is True and set(legacy["patterns"]) == {"date_iso", "date_slash", "date_text"}
    assert column(orders, "customer_id")["null_count"] == data.expected["orders"]["null_customer"]
    assert column(orders, "status")["distinct"] == data.expected["orders"]["status_distinct"]
    assert all(f["orphans"] == 0 for f in profile("tickets")["foreign_keys"] + profile("order_items")["foreign_keys"])
    assert column(profile("tickets"), "customer_id")["null_count"] == data.expected["tickets"]["customer_nulls"]


def test_keys_without_enforcement_and_enforced_unique_columns_are_told_apart(data):
    products = profile("products")
    sku = column(products, "sku")
    assert sku["distinct"] == 150 and "likely_identifier" not in sku  # unique index: nothing to warn about
    audit = profile("audit_log")
    assert audit["has_primary_key"] is False and audit["row_count"] == 2500
    assert profile("order_items")["row_count"] == data.expected["row_counts"]["order_items"]


def test_a_huge_table_is_sampled_and_says_so(data):
    p = profile("events", limits={}, max_rows_exact=5000)
    assert p["exact"] is False and p["row_count_exact"] is False
    assert 15000 <= p["row_count"] <= 25000  # the optimiser's estimate, not a count
    assert p["sample_basis"] == "pk_ranges" and 3000 <= p["sampled_rows"] <= 5500
    assert any("Approximate" in w for w in p["warnings"])
    kind = column(p, "kind")
    assert kind["distinct"] == 5 and kind["distinct_exact"] is False and kind["null_rate"] == 0
    assert p["foreign_keys"] == []


def test_tables_without_a_numeric_key_fall_back_to_the_first_rows(data):
    p = profile("audit_log", limits={"max_rows_exact": 100})
    assert p["exact"] is False and p["sample_basis"] == "first_rows" and p["sampled_rows"] == 2500


def test_only_requested_columns_are_profiled(data):
    p = profile("customers", ["status", "country"])
    assert [c["name"] for c in p["columns"]] == ["status", "country"]


def test_bad_requests_are_typed_errors(data):
    for table, columns, code in (("v_open_tickets", None, "unsupported"), ("nope", None, "not_found"), ("customers; DROP TABLE x", None, "bad_request"), ("customers", ["missing"], "bad_request")):
        with pytest.raises(ConnectorError) as raised:
            profile(table, columns)
        assert raised.value.code == code, (table, columns)


def test_every_statement_runs_under_a_time_limit_in_a_read_only_session(data):
    session = Session(config("reader", max_seconds=7))
    try:
        assert session.query("SELECT @@max_execution_time")[0][0] == 7000
        assert session.query("SELECT @@transaction_read_only")[0][0] == 1
    finally:
        session.close()
