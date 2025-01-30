from models import Conference
from app import create_app
from extensions import db
from models import Admin

# Initialize the app
app = create_app()
with app.app_context():
    conf = Conference.query.filter_by(name="Seattle MUN").first()
    db.session.delete(conf)
    db.session.commit()