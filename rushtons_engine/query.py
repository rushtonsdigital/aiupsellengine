"""Read-only SQL access for the Claude Code chat path (BI questions).

One safe entry point, `run_sql`, that a Claude Code session uses to answer
ad-hoc data questions against the reporting layer (the v_* views and the
summary tables). See the rushtons-analytics skill for how to drive it.

The REAL guarantee is the database role: REPORTING_DATABASE_URL should point at
the SELECT-only `rushtons_readonly` Supabase login, which cannot write even if
it tried. The checks here are defence-in-depth and, more usefully, give clear
errors before a query ever reaches the server. On the local SQLite fallback
(where roles don't exist) these checks are the only guard, so they matter there.

Values go INLINE in the SQL you pass (there is no bound-parameter path) — this
keeps execution on exec_driver_sql, which is what lets Postgres `::type` casts
in the reporting views work without being mistaken for bind parameters.
"""

import logging
import re

import sqlalchemy as sa

import config

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 1000

# Whole-word tokens that must never appear in a read-only query.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|"
    r"vacuum|merge|call|do|attach|detach|pragma|replace|reindex|analyze)\b",
    re.IGNORECASE)

_engine = None


def _get_engine() -> sa.Engine:
    """A dedicated read-only engine, kept separate from db._engine so using the
    chat path never clobbers the writer pipeline's cached engine."""
    global _engine
    if _engine is None:
        _engine = sa.create_engine(config.REPORTING_DATABASE_URL)
    return _engine


def _strip_leading_comments(sql: str) -> str:
    s = sql.strip()
    while True:
        if s.startswith("--"):
            nl = s.find("\n")
            s = s[nl + 1:].strip() if nl != -1 else ""
        elif s.startswith("/*"):
            end = s.find("*/")
            s = s[end + 2:].strip() if end != -1 else ""
        else:
            return s


def _validate(sql: str) -> str:
    """Return the cleaned single SELECT statement, or raise ValueError."""
    if not sql or not sql.strip():
        raise ValueError("empty query")
    body = _strip_leading_comments(sql).rstrip().rstrip(";").strip()
    if not body:
        raise ValueError("empty query")
    # Single statement only — no stacked statements.
    if ";" in body:
        raise ValueError("only a single statement is allowed (found ';')")
    head = body.split(None, 1)[0].lower()
    if head not in ("select", "with"):
        raise ValueError("only SELECT / WITH queries are allowed")
    hit = _FORBIDDEN.search(body)
    if hit:
        raise ValueError(f"forbidden keyword '{hit.group(0)}' — read-only access")
    return body


def run_sql(sql: str, limit: int = DEFAULT_LIMIT) -> dict:
    """Execute one read-only SELECT against the reporting layer.

    Returns {'columns': [...], 'rows': [{col: val, ...}, ...],
             'truncated': bool, 'rowcount': int}. `truncated` is True when the
    result hit `limit` and more rows exist.
    """
    body = _validate(sql)
    limit = max(1, int(limit))
    wrapped = f"select * from (\n{body}\n) _rushtons_q limit {limit + 1}"

    engine = _get_engine()
    with engine.connect() as conn:
        if engine.dialect.name == "postgresql":
            conn.exec_driver_sql("set transaction read only")
        result = conn.exec_driver_sql(wrapped)
        columns = list(result.keys())
        fetched = result.fetchall()

    truncated = len(fetched) > limit
    rows = [dict(zip(columns, r)) for r in fetched[:limit]]
    return {"columns": columns, "rows": rows,
            "truncated": truncated, "rowcount": len(rows)}


def describe() -> dict:
    """List the reporting views and summary tables with their columns, so the
    chat path can self-orient. Returns {name: [column, ...]}."""
    engine = _get_engine()
    inspector = sa.inspect(engine)
    names = set(inspector.get_table_names())
    try:
        names |= set(inspector.get_view_names())
    except NotImplementedError:  # some dialects don't separate views
        pass
    wanted = [n for n in sorted(names)
              if n.startswith("v_")
              or n in ("customer_category_metrics", "product_metrics",
                       "recommendation_category_facts", "customer_week_metrics")]
    return {n: [c["name"] for c in inspector.get_columns(n)] for n in wanted}
