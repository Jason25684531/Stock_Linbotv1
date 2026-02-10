"""
Migrate financial_statements.year to AD (Gregorian) format.
"""
import argparse
from datetime import datetime

from sqlalchemy import text
from tool.db_helper import get_db_engine


def migrate(dry_run: bool, backup: bool) -> bool:
    engine = get_db_engine()

    with engine.begin() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM financial_statements")).scalar() or 0
        roc_count = conn.execute(
            text("SELECT COUNT(*) FROM financial_statements WHERE year < 1911")
        ).scalar() or 0
        ad_count = total - roc_count

        print(f"total_rows={total}, ad_rows={ad_count}, roc_rows={roc_count}")

        dup_count = conn.execute(text("""
            SELECT COUNT(*) FROM financial_statements t1
            JOIN financial_statements t2
              ON t1.stock_id = t2.stock_id
             AND t1.quarter = t2.quarter
             AND t2.year = t1.year + 1911
            WHERE t1.year < 1911
        """)).scalar() or 0
        print(f"duplicate_roc_rows={dup_count}")

        if dry_run:
            print("dry_run=true; no changes applied")
            return True

        backup_table = None
        if backup:
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            backup_table = f"financial_statements_bak_{ts}"
            conn.execute(
                text(f"CREATE TABLE {backup_table} AS SELECT * FROM financial_statements")
            )
            print(f"backup_table={backup_table}")

        delete_result = conn.execute(text("""
            DELETE t1 FROM financial_statements t1
            JOIN financial_statements t2
              ON t1.stock_id = t2.stock_id
             AND t1.quarter = t2.quarter
             AND t2.year = t1.year + 1911
            WHERE t1.year < 1911
        """))
        print(f"deleted_duplicates={delete_result.rowcount}")

        update_result = conn.execute(text("""
            UPDATE financial_statements
            SET year = year + 1911
            WHERE year < 1911
        """))
        print(f"updated_rows={update_result.rowcount}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Migrate financial_statements.year to AD format."
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument(
        "--no-backup", action="store_true", help="Skip backup table creation"
    )
    args = parser.parse_args()

    migrate(dry_run=args.dry_run, backup=not args.no_backup)


if __name__ == "__main__":
    main()
