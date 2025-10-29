from app import create_app, db

app = create_app()
with app.app_context():
    print('OK tables:', sorted(db.metadata.tables.keys()))
