from flask import Flask, redirect, url_for, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from extensions import db
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
import os

csrf = CSRFProtect()
load_dotenv(".env")

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get("FLASK_SECRET_KEY")
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///munnw_sms.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = 'uploads'

    # Initialize db with app here
    db.init_app(app)
    csrf.init_app(app)
    
    # Initialize the login manager
    login_manager = LoginManager()
    login_manager.init_app(app)
    # Specify login route from blueprint
    login_manager.login_view = 'routes.login'  
    
    @login_manager.user_loader
    def load_user(user_id):
        from models import Admin  # NO circular imports
        return db.session.get(Admin, int(user_id))
    
    @app.route('/')
    def index():
        return redirect(url_for('routes.login'))

    migrate = Migrate(app, db)

    from routes import routes
    app.register_blueprint(routes, url_prefix='/')

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)