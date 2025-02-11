from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from extensions import db
from enum import Enum
from werkzeug.security import generate_password_hash, check_password_hash

class Admin(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)
    conference_id = db.Column(db.Integer, db.ForeignKey('conference.id'))
    
    # Relationships
    sent_messages = db.relationship('Message', backref='sender', lazy=True)
    conference = db.relationship('Conference', backref='admin')

    # Hash password before storing it
    def set_password(self, raw_password):
        """Hashes and stores the password securely."""
        self.password = generate_password_hash(raw_password)

    # Verify password
    def check_password(self, raw_password):
        """Checks if the provided password matches the stored hash."""
        return check_password_hash(self.password, raw_password)

class ConferenceEnum(str, Enum):
    EDUMUN = "EDUMUN"
    PACMUN = "PACMUN"
    SEATTLEMUN = "SeattleMUN"
    KINGMUN = "KINGMUN"

class Conference(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    theme_color = db.Column(db.String(7), nullable=False)  # Hex color code
    logo_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # Relationships
    participants = db.relationship('Participant', backref='conference', lazy=True)

    @staticmethod
    def init_default_conferences():
        """Initialize the standard conferences if they don't exist"""
        conferences = [
            {
                'name': ConferenceEnum.EDUMUN,
                'theme_color': '#2B8282',
                'logo_path': 'https://edumun.com/_next/image?url=https://files.munnorthwest.org/image/edumun/c7de31d6662517d2a8f2d1395aedc5b51ed506e82c38241a5e362750c4637d65/edumunWhite%20logo.png&w=3840&q=75'
            },
            {
                'name': ConferenceEnum.PACMUN,
                'theme_color': '#1792a7',
                'logo_path': 'https://pacificmun.com/_next/image?url=https://files.munnorthwest.org/image/pacmun/6f3d73067fa8fb20c7c428c7ccf81712cb96586c854673fd86a18fae12a669b9/pacmun_white_NoYear.png&w=1200&q=75'
            },
            {
                'name': ConferenceEnum.SEATTLEMUN,
                'theme_color': '#C8193E',
                'logo_path': 'https://seattlemun.org/_next/image?url=https://files.munnorthwest.org/image/seattlemun/78a9714891c226b100663a8e34204f325514ff8d85fc2574958f5829695befe7/SeattleMUN%20logo%20-%20white.png&w=1200&q=75'
            },
            {
                'name': ConferenceEnum.KINGMUN,
                'theme_color': '#2E4A20',
                'logo_path': 'https://kingmun.org/_next/image?url=https://files.munnorthwest.org/image/kingmun/9b852e368aceaf885c8e672aa83c8a2ac7ef2500a81335c7356c6732d175beda/whiteSmallLogo.png&w=3840&q=75'
            }
        ]
        
        for conf_data in conferences:
            if not Conference.query.filter_by(name=conf_data['name']).first():
                conference = Conference(**conf_data)
                db.session.add(conference)
        
        db.session.commit()

class Participant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conference_id = db.Column(db.Integer, db.ForeignKey('conference.id'), nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    participant_type = db.Column(db.String(50), nullable=False)  # Delegate, Advisor, Staff, Secretariat
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # Relationships
    received_messages = db.relationship('MessageRecipient', backref='participant', lazy=True, cascade="all, delete-orphan")

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    sent_by = db.Column(db.Integer, db.ForeignKey('admin.id'))
    sent_at = db.Column(db.DateTime, default=datetime.now)
    scheduled_at = db.Column(db.DateTime, nullable=True)  # NEW FIELD
    status = db.Column(db.String(20), default='pending')  # pending, sent, failed, scheduled
    recipient_count = db.Column(db.Integer)

    # Relationships
    recipients = db.relationship('MessageRecipient', backref='message', lazy=True)

class MessageRecipient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)
    participant_id = db.Column(db.Integer, db.ForeignKey('participant.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, sent, failed
    sent_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)