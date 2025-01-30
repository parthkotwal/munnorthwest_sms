from app import create_app
from extensions import db
from models import Admin

# Initialize the app
app = create_app()

# Run the code within the app context
with app.app_context():
    admin = Admin(username="liams", password="liamtherat")
    admin.set_password('liamtherat')
    db.session.add(admin)
    db.session.commit()

print("Admin created successfully!")