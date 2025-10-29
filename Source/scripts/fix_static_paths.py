from pathlib import Path
import sqlite3
import sys

def main():
    # Locate DB: repo_root/instance/helpdesk.db
    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[1]
    db_path = repo_root / 'instance' / 'helpdesk.db'
    if not db_path.exists():
        print(f"Database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    def table_exists(name: str) -> bool:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return cur.fetchone() is not None

    candidates = ['ticket_attachment', 'ticketattachment']
    fixed_total = 0
    for tbl in candidates:
        if not table_exists(tbl):
            continue
        # Count rows needing fix
        cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE static_path LIKE ?", (r"%\%",))
        to_fix = cur.fetchone()[0]
        if to_fix:
            cur.execute(f"UPDATE {tbl} SET static_path = REPLACE(static_path, '\\', '/') WHERE static_path LIKE ?", (r"%\%",))
            fixed_total += to_fix
            print(f"Fixed {to_fix} rows in {tbl}")
    conn.commit()
    conn.close()
    if fixed_total == 0:
        print("No paths to fix.")
    else:
        print(f"Done. Total fixed: {fixed_total}")

if __name__ == '__main__':
    main()
