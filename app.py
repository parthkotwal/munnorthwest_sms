from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from extensions import db

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'your_secret_key'
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///munnw_sms.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = 'uploads'

    # Initialize the db with the app here
    db.init_app(app)
    
    # Initialize the login manager
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'routes.login'  # Specify the login route from the blueprint
    
    @login_manager.user_loader
    def load_user(user_id):
        from models import User  # Avoid circular imports
        return User.query.get(int(user_id))
    
    migrate = Migrate(app, db)

    from routes import routes
    app.register_blueprint(routes, url_prefix='/')

    return app