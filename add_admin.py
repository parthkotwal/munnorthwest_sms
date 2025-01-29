from app import app, db, User, generate_password_hash

with app.app_context():
    admin = User(username="parthk", password=generate_password_hash("parththepig"), role="Admin")
    db.session.add(admin)
    db.session.commit()
print("Admin created successfully!")