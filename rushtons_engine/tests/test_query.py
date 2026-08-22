"""query.run_sql is the read-only gate for the Claude Code chat path. The DB
role is the real guard in production; these pin the defence-in-depth checks
(which are the ONLY guard on the local SQLite fallback)."""

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

import query


@pytest.fixture()
def seeded_engine():
    """A tiny in-memory DB wired into query as the read-only engine."""
    eng = sa.create_engine("sqlite://", poolclass=StaticPool)
    with eng.begin() as c:
        c.exec_driver_sql("create table t (n int, label text)")
        c.exec_driver_sql(
            "insert into t values (1,'a'),(2,'b'),(3,'c'),(4,'d'),(5,'e')")
    saved = query._engine
    query._engine = eng
    yield eng
    query._engine = saved


@pytest.mark.parametrize("bad", [
    "delete from orders",
    "update customers set prestige='VIP'",
    "drop table orders",
    "insert into orders (id) values (1)",
    "truncate orders",
    "select 1; select 2",              # stacked statements
    "select 1; drop table orders",
    "",
    "   ",
    "with x as (delete from orders returning 1) select * from x",
])
def test_run_sql_rejects_non_readonly(bad):
    with pytest.raises(ValueError):
        query.run_sql(bad)


def test_run_sql_allows_select_and_with(seeded_engine):
    assert query.run_sql("select n from t where n = 1")["rowcount"] == 1
    out = query.run_sql("with x as (select n from t) select n from x")
    assert out["rowcount"] == 5


def test_run_sql_applies_limit_and_flags_truncation(seeded_engine):
    out = query.run_sql("select n, label from t order by n", limit=3)
    assert out["rowcount"] == 3
    assert out["truncated"] is True
    assert out["columns"] == ["n", "label"]
    assert out["rows"][0] == {"n": 1, "label": "a"}


def test_run_sql_not_truncated_when_under_limit(seeded_engine):
    out = query.run_sql("select n from t order by n", limit=10)
    assert out["rowcount"] == 5
    assert out["truncated"] is False


def test_run_sql_tolerates_trailing_semicolon_and_comment(seeded_engine):
    assert query.run_sql("-- count them\nselect n from t;")["rowcount"] == 5
