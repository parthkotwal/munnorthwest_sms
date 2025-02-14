from flask import Flask, redirect, url_for, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from extensions import db
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
import os
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from datetime import datetime
from models import Message, MessageRecipient, Participant
from routes import send_messages_now, send_messages_now_backup

csrf = CSRFProtect()
load_dotenv(".env")

def init_scheduler(app):
    """Initialize the scheduler with app context"""
    def process_scheduled_messages():
        """Send messages that are due for delivery."""
        with app.app_context():  
            now = datetime.now()
            scheduled_messages = Message.query.filter(
                Message.status == 'scheduled',
                Message.scheduled_at <= now
            ).all()

            print(f"[SCHEDULER] Found {len(scheduled_messages)} messages to send.")

            for message in scheduled_messages:
                recipient_entries = MessageRecipient.query.filter_by(message_id=message.id).all()
                recipients = [entry.participant for entry in recipient_entries]

                if recipients:
                    print(f"[SCHEDULER] Sending to {len(recipients)} recipients for message {message.id}")
                    if not send_messages_now(message, recipients):
                        print("Falling back to backup sending method...")
                        send_messages_now_backup(message, recipients)
                        
                    message.status = "sent"
                    message.sent_at = datetime.now()
                    db.session.commit()
                else:
                    print(f"[SCHEDULER] No recipients found for message {message.id}, skipping.")

    scheduler = BackgroundScheduler()
    scheduler.add_job(process_scheduled_messages, 'interval', minutes=1)
    scheduler.start()
    print("[SCHEDULER] AP Scheduler initialized")

    # Shut down scheduler when exiting app
    atexit.register(lambda: scheduler.shutdown())


def create_app(config_class=None):
    app = Flask(__name__)
    
    if config_class:
        app.config.from_object(config_class)

    app.config['SECRET_KEY'] = os.environ.get("FLASK_SECRET_KEY")
    # app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///munnw_sms.db'
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL", "sqlite:///munnw_sms.db")

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = 'uploads'

    # Initialize db with app here
    db.init_app(app)
    csrf.init_app(app)

    # Initialize the login manager
    login_manager = LoginManager()
    login_manager.init_app(app)
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

    # Initialize scheduler only in production
    if not app.debug:
        init_scheduler(app)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)