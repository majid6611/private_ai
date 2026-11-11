"""Deterministic synthetic MySQL data for connector tests (never real or personal data).

`build()` returns the rows together with the exact answers a correct profiler must reproduce.
`load()` creates the `shop` schema objects and inserts the rows.

Deliberate problems: duplicate emails, orders pointing at customers that do not exist, mixed date
formats in a text column, a table without any key, a big table to force sampling, enum-like
columns, text-heavy tables, and columns that look like personal data.
"""

import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

SEED = 20261110
FIRST = "Ada Ben Cleo Dev Eli Fay Gus Hana Ivo Jade Kai Lena Milo Nora Omar Pia Quin Rex Sia Tom Uma Vic Wes Xena Yan Zoe".split()
LAST = "Archer Baker Cole Dunn Ellis Frost Grant Hayes Irwin Jones Kerr Lowe Moss Nash Owen Pike Quill Reed Shaw Tate".split()
COUNTRIES = ["Canada"] * 5 + ["USA"] * 3 + ["Germany", "France", "Japan", "Brazil", "India", "Kenya"]
CATEGORIES = ["tools", "toys", "books", "garden", "kitchen", "sports"]
ORDER_STATUS = ["new", "paid", "shipped", "delivered", "cancelled"]
WORDS = ("order delivery late refund invoice account password login error billing update support product "
         "damaged missing replacement warranty shipping address change cancel payment confirmation").split()

DDL = [
    "DROP VIEW IF EXISTS v_open_tickets",
    *[f"DROP TABLE IF EXISTS {t}" for t in ("order_items", "tickets", "orders", "audit_log", "events", "products", "customers")],
    """CREATE TABLE customers (
        id INT PRIMARY KEY, first_name VARCHAR(40) NOT NULL, last_name VARCHAR(40) NOT NULL,
        email VARCHAR(120) NOT NULL, phone VARCHAR(30) NULL, country VARCHAR(30) NOT NULL,
        status ENUM('active','inactive','banned') NOT NULL, notes TEXT NULL,
        created_at DATETIME NOT NULL, birth_date DATE NULL, INDEX idx_country (country)
    ) COMMENT='People and companies we sell to'""",
    """CREATE TABLE products (
        id INT PRIMARY KEY, sku VARCHAR(20) NOT NULL UNIQUE, name VARCHAR(80) NOT NULL,
        category VARCHAR(30) NOT NULL, price DECIMAL(10,2) NOT NULL, description TEXT
    )""",
    """CREATE TABLE orders (
        id INT PRIMARY KEY, customer_id INT NULL, ordered_at DATETIME NOT NULL,
        status VARCHAR(20) NOT NULL, total DECIMAL(12,2) NOT NULL, legacy_date VARCHAR(30) NULL,
        CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
    )""",
    """CREATE TABLE order_items (
        order_id INT NOT NULL, product_id INT NOT NULL, qty INT NOT NULL, unit_price DECIMAL(10,2) NOT NULL,
        PRIMARY KEY (order_id, product_id),
        CONSTRAINT fk_items_order FOREIGN KEY (order_id) REFERENCES orders(id),
        CONSTRAINT fk_items_product FOREIGN KEY (product_id) REFERENCES products(id)
    )""",
    """CREATE TABLE tickets (
        id INT PRIMARY KEY, customer_id INT NULL, subject VARCHAR(120) NOT NULL, body TEXT NOT NULL,
        opened_at DATETIME NOT NULL, status VARCHAR(20) NOT NULL, priority VARCHAR(10) NOT NULL,
        CONSTRAINT fk_tickets_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
    )""",
    "CREATE TABLE audit_log (event_time DATETIME NOT NULL, actor VARCHAR(40) NOT NULL, action VARCHAR(40) NOT NULL, details VARCHAR(200))",
    "CREATE TABLE events (id BIGINT PRIMARY KEY, kind VARCHAR(20) NOT NULL, payload VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL)",
    "CREATE VIEW v_open_tickets AS SELECT id, subject FROM tickets WHERE status = 'open'",
]


@dataclass
class Fixture:
    rows: dict[str, list[tuple[Any, ...]]]
    expected: dict[str, Any] = field(default_factory=dict)


def build() -> Fixture:
    rng = random.Random(SEED)
    base = datetime(2022, 1, 1)

    def when(span_days: int = 1400) -> datetime:
        return base + timedelta(days=rng.randrange(span_days), seconds=rng.randrange(86400))

    customers: list[tuple[Any, ...]] = []
    for i in range(1, 1201):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        email = f"{first.lower()}.{last.lower()}{i}@example.com"
        if 1101 <= i <= 1107:  # seven deliberate duplicates of the first seven emails
            email = customers[i - 1101][3]
        phone = None if rng.random() < 0.10 else f"+1 403-555-{rng.randrange(10000):04d}"
        status = rng.choices(["active", "inactive", "banned"], [70, 25, 5])[0]
        notes = None if rng.random() < 0.30 else " ".join(rng.choice(WORDS) for _ in range(10))
        birth = None if rng.random() < 0.20 else (datetime(1950, 1, 1) + timedelta(days=rng.randrange(20000))).date()
        customers.append((i, first, last, email, phone, rng.choice(COUNTRIES), status, notes, when(), birth))

    products = [
        (i, f"SKU-{i:04d}", f"{rng.choice(WORDS).title()} {rng.choice(WORDS).title()}", rng.choice(CATEGORIES),
         round(rng.uniform(2, 400), 2), " ".join(rng.choice(WORDS) for _ in range(20)))
        for i in range(1, 151)
    ]

    orders: list[tuple[Any, ...]] = []
    formats = [
        lambda d: d.strftime("%Y-%m-%d"),
        lambda d: d.strftime("%d/%m/%Y"),
        lambda d: d.strftime("%b %-d, %Y"),
    ]
    legacy = Counter()
    for i in range(1, 4001):
        customer = rng.randrange(1, 1201)
        if i >= 3996:
            customer = 9000 + (i - 3995)  # orphans: 9001..9005 do not exist
        elif i % 40 == 0:
            customer = None  # guest orders: NULL, not orphans
        at = when()
        choice = rng.choices([0, 1, 2], [40, 35, 25])[0]
        legacy[["date_iso", "date_slash", "date_text"][choice]] += 1
        orders.append((i, customer, at, rng.choice(ORDER_STATUS), round(rng.uniform(5, 900), 2), formats[choice](at)))

    items: list[tuple[Any, ...]] = []
    for order in orders:
        for product in rng.sample(range(1, 151), rng.randrange(1, 5)):
            items.append((order[0], product, rng.randrange(1, 6), round(rng.uniform(2, 400), 2)))

    tickets = [
        (i, None if i % 10 == 0 else rng.randrange(1, 1201), f"{rng.choice(WORDS).title()} problem {i}",
         " ".join(rng.choice(WORDS) for _ in range(rng.randrange(35, 90))), when(),
         rng.choice(["open", "closed", "pending"]), rng.choice(["low", "medium", "high"]))
        for i in range(1, 601)
    ]
    audit = [
        (when(), rng.choice(["alice", "bob", "carol", "dave"]), rng.choice(["login", "logout", "update", "delete"]), "ok")
        for _ in range(2500)
    ]
    events = [(i, rng.choice(["click", "view", "buy", "scroll", "exit"]), f"payload-{i}", when()) for i in range(1, 20001)]

    rows = {
        "customers": customers, "products": products, "orders": orders, "order_items": items,
        "tickets": tickets, "audit_log": audit, "events": events,
    }
    emails = [c[3] for c in customers]
    expected = {
        "row_counts": {name: len(data) for name, data in rows.items()},
        "customers": {
            "email_distinct": len(set(emails)),
            "email_duplicates": len(emails) - len(set(emails)),
            "phone_nulls": sum(1 for c in customers if c[4] is None),
            "notes_nulls": sum(1 for c in customers if c[7] is None),
            "birth_nulls": sum(1 for c in customers if c[9] is None),
            "status_counts": dict(Counter(c[6] for c in customers)),
            "country_distinct": len({c[5] for c in customers}),
        },
        "orders": {
            "orphans_customer": 5,
            "null_customer": sum(1 for o in orders if o[1] is None),
            "legacy_patterns": dict(legacy),
            "status_distinct": len({o[3] for o in orders}),
        },
        "tickets": {"customer_nulls": sum(1 for t in tickets if t[1] is None), "open": sum(1 for t in tickets if t[5] == "open")},
    }
    return Fixture(rows, expected)


def load(connection: Any, fixture: Fixture) -> None:
    """(Re)create the schema objects and insert the rows through a privileged connection."""
    with connection.cursor() as cursor:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        for statement in DDL:
            cursor.execute(statement)
        for table, data in fixture.rows.items():
            marks = ",".join(["%s"] * len(data[0]))
            for start in range(0, len(data), 1000):
                cursor.executemany(f"INSERT INTO {table} VALUES ({marks})", data[start : start + 1000])  # noqa: S608
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        cursor.execute("ANALYZE TABLE customers, products, orders, order_items, tickets, audit_log, events")
        cursor.fetchall()
    connection.commit()
