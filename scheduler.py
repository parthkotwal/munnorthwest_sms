from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from flask import Flask
from models import Message, MessageRecipient, Participant
from extensions import db
from app import create_app
from routes import send_messages_now

app = create_app()

def process_scheduled_messages():
    """Send messages that are due for delivery."""
    with app.app_context():
        now = datetime.now()
        scheduled_messages = Message.query.filter(
            Message.status == 'scheduled',
            Message.scheduled_at <= now
        ).all()

        for message in scheduled_messages:
            recipients = Participant.query.filter(
                Participant.conference_id == message.sender.conference_id,  # Fetch recipients correctly
                Participant.participant_type.in_([rec_type for rec_type in ["Delegate", "Advisor", "Staff", "Secretariat"]])
            ).all()

            send_messages_now(message, recipients)
            message.status = "sent"
            db.session.commit()

scheduler = BackgroundScheduler()
scheduler.add_job(process_scheduled_messages, 'interval', minutes=1)  # Runs every minute
scheduler.start()
