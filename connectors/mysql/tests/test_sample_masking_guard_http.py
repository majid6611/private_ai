import json
import logging
import threading
import urllib.error
import urllib.request

import pymysql
import pytest

from connector_sdk import ConnectorError, make_server
from mysql_connector.db import Session, quote
from mysql_connector.sensitive import classify_name, classify_value, mask_cell
from mysql_connector.server import manifest, routes
from tests.conftest import config


def test_samples_mask_personal_columns_and_cap_the_row_count(data):
    out = routes(config("reader"))["/v1/sample"]({"schema": "shop", "table": "customers", "n": 500})
    assert len(out["rows"]) == 20  # capped
    index = {n: i for i, n in enumerate(out["columns"])}
    for row in out["rows"]:
        assert row[index["email"]] == "[email]" and row[index["first_name"]] == "[name]"
        assert row[index["phone"]] in ("[phone]", None) and row[index["birth_date"]] in ("[date of birth]", None)
        assert row[index["status"]] in ("active", "inactive", "banned")
    assert {"email", "first_name", "last_name", "phone", "birth_date"} <= set(out["masked_columns"])
    assert "@example.com" not in json.dumps(out)


def test_values_that_look_personal_are_masked_even_in_innocent_columns():
    assert mask_cell("jane@corp.example", None) == "[email]"
    assert mask_cell("4111 1111 1111 1111", None) == "[card]"  # passes the Luhn check
    assert mask_cell("4111 1111 1111 1112", None) == "4111 1111 1111 1112"  # does not: just digits
    assert mask_cell("x" * 500, None).endswith("…") and len(mask_cell("x" * 500, None)) == 201
    assert mask_cell(b"\x00\x01", None) == "<binary 2 bytes>" and mask_cell(None, "email") is None
    assert classify_value("123-45-6789") == "id_number" and classify_value("hello") is None
    assert [classify_name(n) for n in ("password_hash", "e_mail", "mobile", "ssn", "iban", "dob", "last_name", "street", "country", "status")] == [
        "credential", "email", "phone", "id_number", "financial", "birth_date", "name", "address", None, None]


def test_the_statement_guard_allows_reads_and_nothing_else(data):
    session = Session(config("reader"))
    try:
        assert session.query("SHOW TABLES FROM shop")
        for sql in ("INSERT INTO shop.audit_log VALUES (NOW(),'a','b','c')", "SELECT 1; DROP TABLE shop.events", "SELECT SLEEP(5)",
                    "SELECT * FROM shop.customers INTO OUTFILE '/tmp/x'", "SELECT LOAD_FILE('/etc/passwd')", "UPDATE shop.customers SET status='banned'", "DELETE FROM shop.events"):
            with pytest.raises(ConnectorError) as raised:
                session.query(sql)
            assert raised.value.code == "forbidden", sql
    finally:
        session.close()


def test_even_an_account_that_may_write_cannot_inside_the_connector_session(data):
    session = Session(config("writer"))
    try:
        with pytest.raises(pymysql.err.OperationalError) as raised:
            with session.conn.cursor() as cursor:  # bypassing the guard on purpose
                cursor.execute("INSERT INTO shop.audit_log VALUES (NOW(), 'x', 'y', 'z')")
        assert raised.value.args[0] == 1792  # "cannot execute statement in a READ ONLY transaction"
    finally:
        session.close()
    for path in ("/v1/profile", "/v1/sample"):
        with pytest.raises(ConnectorError) as refused:
            routes(config("writer"))[path]({"schema": "shop", "table": "customers"})
        assert refused.value.code == "forbidden"


def test_identifiers_are_restricted_before_they_reach_sql():
    assert quote("customers") == "`customers`" and quote("order items") == "`order items`"
    for bad in ("a`b", "x;y", "a" * 65, "", "a'b", "a/b"):
        with pytest.raises(ConnectorError):
            quote(bad)


@pytest.fixture
def server(data):
    cfg = config("reader")
    key = "k" * 40
    httpd = make_server(manifest(cfg), routes(cfg), key, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", key
    httpd.shutdown()
    httpd.server_close()


def http(url, key=None, body=None, method="POST", raw=None):
    request = urllib.request.Request(url, data=raw if raw is not None else json.dumps(body or {}).encode(), method=method,
                                     headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + key} if key else {})})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_http_requires_the_key_and_speaks_typed_json(server, caplog):
    base, key = server
    assert http(base + "/v1/describe", method="GET")[0] == 401
    assert http(base + "/v1/describe", "wrong-key-wrong-key", method="GET")[0] == 401
    status, described = http(base + "/v1/describe", key, method="GET")
    assert status == 200 and described["protocol"] == "1" and described["id"] == "core.mysql" and described["kinds"] == ["records"]
    assert described["network"] == {"mode": "egress", "hosts": ["mysql-fixture:3306"]}
    assert http(base + "/v1/check", key)[1]["ok"] is True
    assert http(base + "/v1/nope", key) == (404, {"error": {"code": "not_found", "message": "Unknown call"}})
    assert http(base + "/v1/profile", key, raw=b"not json")[0] == 400
    assert http(base + "/v1/profile", key, raw=b"[1,2]")[0] == 400
    assert http(base + "/v1/profile", key, {"schema": "shop", "table": "missing"})[0] == 404
    assert http(base + "/v1/sample", key, raw=b"x" * 2_000_000)[0] in (400, 413)
    status, profiled = http(base + "/v1/profile", key, {"schema": "shop", "table": "orders", "columns": ["status"]})
    assert status == 200 and profiled["columns"][0]["name"] == "status"
    for text in (json.dumps(profiled), caplog.text):
        assert "reader-fixture-pw" not in text


def test_errors_never_carry_driver_text(data):
    cfg = config("reader", host="127.0.0.1", port=9)  # nothing listens here
    with pytest.raises(ConnectorError) as raised:
        Session(cfg)
    assert raised.value.code == "retry_later" and "127.0.0.1" not in raised.value.message and "Errno" not in raised.value.message
    logging.getLogger("connector").info("smoke")  # the logger exists and is quiet by default
