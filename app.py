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
import socket
from contextlib import contextmanager
from sqlalchemy import create_engine, text
import time

csrf = CSRFProtect()
load_dotenv()

def get_lock(session, lock_name):
    """Try to obtain a PostgreSQL advisory lock"""
    # Convert lock_name to a 64-bit integer
    lock_id = hash(lock_name) % 2**63
    
    # Try to get the lock
    result = session.execute(
        text('SELECT pg_try_advisory_lock(:lock_id)'),
        {'lock_id': lock_id}
    ).scalar()
    
    return result

def release_lock(session, lock_name):
    """Release a PostgreSQL advisory lock"""
    lock_id = hash(lock_name) % 2**63
    session.execute(
        text('SELECT pg_advisory_unlock(:lock_id)'),
        {'lock_id': lock_id}
    )

def init_scheduler(app):
    """Initialize the scheduler with worker-level locking"""
    def process_scheduled_messages():
        """Send messages that are due for delivery with lock protection"""
        with app.app_context():
            # Get a database session
            session = db.session()
            
            try:
                # Try to acquire the lock
                if not get_lock(session, 'scheduler_lock'):
                    app.logger.info("Another worker is processing messages, skipping this run")
                    return

                app.logger.info("Acquired scheduler lock, processing messages")
                
                now = datetime.now()
                scheduled_messages = Message.query.filter(
                    Message.status == 'scheduled',
                    Message.scheduled_at <= now
                ).all()

                for message in scheduled_messages:
                    try:
                        recipient_entries = MessageRecipient.query.filter_by(message_id=message.id).all()
                        recipients = [entry.participant for entry in recipient_entries]

                        if not recipients:
                            continue

                        if not send_messages_now(message, recipients):
                            send_messages_now_backup(message, recipients)
                            
                        message.status = "sent"
                        message.sent_at = datetime.now()
                        db.session.commit()
                        
                    except Exception as e:
                        app.logger.error(f"Error processing message {message.id}: {str(e)}")
                        message.status = "error"
                        db.session.commit()
                        continue

            except Exception as e:
                app.logger.error(f"Scheduler error: {str(e)}")
                
            finally:
                # Always release the lock
                release_lock(session, 'scheduler_lock')
                session.close()

    # Only initialize scheduler on the first worker
    is_first_worker = False
    try:
        # Try to bind to the port - only first worker will succeed
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('localhost', 7999))
        is_first_worker = True
        sock.close()
    except:
        pass

    if is_first_worker:
        app.logger.info("Initializing scheduler on primary worker")
        scheduler = BackgroundScheduler()
        scheduler.add_job(process_scheduled_messages, 'interval', minutes=1)
        scheduler.start()
        
        # Shut down scheduler when exiting app
        atexit.register(lambda: scheduler.shutdown())
    else:
        app.logger.info("Secondary worker - skipping scheduler initialization")

def create_app(config_class=None):
    app = Flask(__name__)
    
    if config_class:
        app.config.from_object(config_class)

    app.config['SECRET_KEY'] = os.environ.get("FLASK_SECRET_KEY")
    # app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///munnw_sms.db'
    # Convert Railway's DATABASE_URL to a format SQLAlchemy accepts
    
    # Choose database based on environment
    database_url = os.getenv("DATABASE_URL")
    
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    else:
        # Default to SQLite if no DATABASE_URL is set
        app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///munnw_sms.db"

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = 'uploads'

    # app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    # print(f"SQLAlchemy URI: {app.config['SQLALCHEMY_DATABASE_URI']}")

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