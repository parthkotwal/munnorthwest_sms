from app import create_app
from extensions import db
from models import Admin, Participant

# Initialize the app
app = create_app()

# Run the code within the app context
with app.app_context():
    admin = Admin(username="charliec", password="chenster25")
    admin.set_password('chenster25')
    db.session.add(admin)
    db.session.commit()

    # obj = Admin.query.filter_by(id=3).one()
    # db.session.delete(obj)
    # db.session.commit()


    # Participant.query.delete()
    # db.session.commit() 

print("Admin created successfully!")