import sqlalchemy as sa

import db
import ingest

DAILY_HEADER = ("Product Group,Product Code,Product Name,Qty Type,Quantity,"
                "Customer Notes,Supplier Notes,Product Status,Delivery Run,"
                "Customer Name,Customer Code,Delivery Address,Order Number,"
                "Delivery Date,Order State,Additional Notes,Purchase Order Number")


def write_daily(tmp_path, name, lines):
    path = tmp_path / name
    path.write_text(DAILY_HEADER + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return path


LINE = ("S010. Vegetables - SPLIT,'1030-KG',Aubergines,Kg,4.0,,,supplied,ROUTE 1,"
        "Cafe One,'C ONE',\"1 Road, London\",54000001,2026-06-02,Invoiced,,")
DUP = ("S100. Herbs - SPLIT,'4120-EA',Parsley - Flat Bunched,Each,1.0,,,supplied,"
       "ROUTE 1,Cafe One,'C ONE',\"1 Road, London\",54000001,2026-06-02,Invoiced,,")


def test_ingest_is_idempotent(conn, tmp_path):
    path = write_daily(tmp_path, "by_customer_2026-06-02.csv", [LINE, DUP, DUP])
    assert ingest.ingest_orders_file(conn, path) == 3
    assert ingest.ingest_orders_file(conn, path) == 3  # re-ingest same file
    count = conn.execute(sa.select(sa.func.count()).select_from(db.orders)).scalar()
    assert count == 3  # not 6 — and the genuine dup pair survives as 2 rows


def test_duplicate_lines_within_file_are_preserved(conn, tmp_path):
    path = write_daily(tmp_path, "by_customer_2026-06-02.csv", [DUP, DUP])
    ingest.ingest_orders_file(conn, path)
    rows = conn.execute(
        sa.select(db.orders).where(db.orders.c.product_code == "4120-EA")).fetchall()
    assert len(rows) == 2  # identical real order lines, both kept


def test_codes_are_unquoted(conn, tmp_path):
    path = write_daily(tmp_path, "by_customer_2026-06-02.csv", [LINE])
    ingest.ingest_orders_file(conn, path)
    row = conn.execute(sa.select(db.orders)).fetchone()
    assert row.customer_code == "C ONE"
    assert row.product_code == "1030-KG"


def test_non_invoiced_states_are_skipped(conn, tmp_path):
    cancelled = LINE.replace("Invoiced", "Cancelled")
    path = write_daily(tmp_path, "by_customer_2026-06-02.csv", [LINE, cancelled])
    assert ingest.ingest_orders_file(conn, path) == 1


def test_order_state_casing_is_normalised(conn, tmp_path):
    """Fresho switched 'Invoiced' -> 'invoiced' in Aug 2026 exports. Lowercase
    must still count, and store canonically so downstream IN() filters match
    both old and new data. 'accepted' (not yet invoiced) is still excluded."""
    lower = LINE.replace("Invoiced", "invoiced")
    accepted = LINE.replace("Invoiced", "accepted")
    path = write_daily(tmp_path, "by_customer_2026-08-01.csv", [lower, accepted])
    assert ingest.ingest_orders_file(conn, path) == 1   # accepted skipped
    stored = conn.execute(sa.select(db.orders.c.order_state)).scalars().all()
    assert stored == ["Invoiced"]                       # canonical spelling


def test_product_master_tracks_delisted(conn, tmp_path):
    june = ("S050. Fruits - SPLIT,'3080-KG',Clementines,Kg,2.0,,,supplied,ROUTE 1,"
            "Cafe One,'C ONE',addr,54000002,2026-06-02,Invoiced,,")
    later = ("Z999. Delisted,'3080-KG',Clementines,Kg,1.0,,,supplied,ROUTE 1,"
             "Cafe One,'C ONE',addr,54000003,2026-08-01,invoiced,,")
    ingest.ingest_orders_file(
        conn, write_daily(tmp_path, "by_customer_2026-06-02.csv", [june]))
    ingest.ingest_orders_file(
        conn, write_daily(tmp_path, "by_customer_2026-08-01.csv", [later]))
    p = conn.execute(sa.select(db.products)
                     .where(db.products.c.product_code == "3080-KG")).fetchone()
    assert p.category == "Fruits"          # keeps last informative group
    assert p.delisted is True              # but knows it's parked in Z999 now


def test_product_master_tracks_out_of_season(conn, tmp_path):
    june = ("S050. Fruits - SPLIT,'3080-KG',Clementines,Kg,2.0,,,supplied,ROUTE 1,"
            "Cafe One,'C ONE',addr,54000002,2026-06-02,Invoiced,,")
    later = ("Z888. Out of Season,'3080-KG',Clementines,Kg,1.0,,,supplied,ROUTE 1,"
             "Cafe One,'C ONE',addr,54000003,2026-06-20,Invoiced,,")
    ingest.ingest_orders_file(
        conn, write_daily(tmp_path, "by_customer_2026-06-02.csv", [june]))
    ingest.ingest_orders_file(
        conn, write_daily(tmp_path, "by_customer_2026-06-20.csv", [later]))
    p = conn.execute(sa.select(db.products)
                     .where(db.products.c.product_code == "3080-KG")).fetchone()
    assert p.category == "Fruits"          # keeps last informative group
    assert p.out_of_season is True         # but knows it's parked in Z888 now


def test_unknown_customer_is_stubbed_and_flagged_new(conn, tmp_path):
    """An order for a customer absent from the master creates a stub flagged
    'new (auto)' so the team knows to look up and set its venue type."""
    path = write_daily(tmp_path, "by_customer_2026-06-02.csv", [LINE])
    ingest.ingest_orders_file(conn, path)
    c = conn.execute(sa.select(db.customers)
                     .where(db.customers.c.customer_code == "C ONE")).fetchone()
    assert c.account_stage == "new (auto)"
    assert c.venue_type is None            # unknown until a human reviews it


def test_tag_parsing():
    tags = ingest._parse_tags("1. restaurant | 2. group <5 | 3. | 4. new 26 | 5. forth hospitality")
    assert tags == {1: "restaurant", 2: "group <5", 3: None,
                    4: "new 26", 5: "forth hospitality"}
