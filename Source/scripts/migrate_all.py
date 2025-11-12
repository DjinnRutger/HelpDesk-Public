from app import create_app, db
from app.utils.db_migrate import (
    ensure_ticket_columns,
    ensure_user_columns,
    ensure_ticket_process_item_columns,
    ensure_ticket_note_columns,
    ensure_po_note_table,
    ensure_project_table,
    ensure_ticket_task_table,
    ensure_order_tables,
)


def main():
    app = create_app()
    with app.app_context():
        ensure_ticket_columns(db.engine)
        ensure_user_columns(db.engine)
        ensure_ticket_process_item_columns(db.engine)
        ensure_ticket_note_columns(db.engine)
        ensure_po_note_table(db.engine)
        ensure_project_table(db.engine)
        ensure_ticket_task_table(db.engine)
        ensure_order_tables(db.engine)
        print("DB migrations applied.")


if __name__ == "__main__":
    main()
