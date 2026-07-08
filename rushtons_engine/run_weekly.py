"""Weekly orchestrator — two-step flow, drafting done by Claude Code.

Step 1 (selection):
    python run_weekly.py [--data-dir DIR]
      ingest -> metrics -> classify -> deterministic top-10 ->
      writes output/drafting_brief_<date>.json + tracker (drafts pending)

Step 2 (after Claude Code writes output/drafts_<date>.json):
    python run_weekly.py --drafts output/drafts_<date>.json
      validates drafts against the locked selection, stores them in comms,
      regenerates the tracker with drafts filled in.

Every step is idempotent; re-running a week replaces that week's outputs.
The drafting step can never change WHO was selected — apply_drafts rejects
any account outside the locked ten.
"""

import argparse
import logging
import sys
from pathlib import Path

import sqlalchemy as sa

import classify
import config
import db
import draft
import export
import ingest
import metrics
import selector as selection

log = logging.getLogger("run_weekly")


def _attach_bands(conn, recs):
    bands = {r.customer_code: r.size_band
             for r in conn.execute(sa.select(db.customers))}
    for rec in recs:
        rec["size_band"] = bands.get(rec["customer_code"])


def run(data_dir: Path, drafts_file: Path | None = None,
        skip_ingest: bool = False) -> Path:
    engine = db.init_db()
    with engine.begin() as conn:
        if not skip_ingest and drafts_file is None:
            customer_files = sorted(data_dir.glob(config.CUSTOMERS_FILE_GLOB))
            order_files = sorted(data_dir.glob(config.ORDERS_FILE_GLOB))
            if not order_files:
                sys.exit(f"No order exports matching {config.ORDERS_FILE_GLOB} in {data_dir}")
            if customer_files:
                ingest.ingest_customers_file(conn, customer_files[-1])  # newest master
            else:
                log.warning("no customer master export in %s - using existing customers", data_dir)
            total = sum(ingest.ingest_orders_file(conn, f) for f in order_files)
            log.info("ingested %d order lines from %d files", total, len(order_files))

        as_of = selection.as_of_date(conn)
        metrics.recompute(conn)
        classify.classify_all(conn, as_of)
        recs = selection.select_top(conn, run_date=as_of)
        if not recs:
            sys.exit("No candidates selected - check thresholds in config.py")
        _attach_bands(conn, recs)

        if drafts_file is not None:
            draft.apply_drafts(conn, drafts_file, recs)
        else:
            draft.export_brief(recs, as_of)
            for rec in recs:
                rec["drafts"] = draft.pending_placeholder()

        path = export.write_tracker(recs, as_of)

    print(f"\nRun date (from data): {as_of}")
    print(f"Selected {len(recs)} accounts:")
    for rec in recs:
        print(f"  {rec['rank']:>2}. {rec['customer_name']:<40} "
              f"score {rec['score']:<8} pitch: {', '.join(rec['gap_categories'])}")
    if drafts_file is None:
        print(f"\nNext: draft messages per output/drafting_brief_{as_of}.json, "
              f"save as output/drafts_{as_of}.json, then re-run with "
              f"--drafts output/drafts_{as_of}.json")
    print(f"Tracker: {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description="Rushton's weekly upsell engine")
    parser.add_argument("--data-dir", type=Path, default=config.DATA_DIR,
                        help="folder holding the weekly Fresho exports")
    parser.add_argument("--drafts", type=Path, default=None,
                        help="apply a drafts JSON written by Claude Code and "
                             "regenerate the tracker (skips ingest)")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="run selection on already-ingested data")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run(args.data_dir, drafts_file=args.drafts, skip_ingest=args.skip_ingest)


if __name__ == "__main__":
    main()
