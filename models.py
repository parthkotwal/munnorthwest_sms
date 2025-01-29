from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from extensions import db
from enum import Enum

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(50), nullable=False)  # Admin, Secretariat, etc.
    
    # Relationships
    sent_messages = db.relationship('Message', backref='sender', lazy=True)
    created_templates = db.relationship('MessageTemplate', backref='creator', lazy=True)

class ConferenceEnum(str, Enum):
    EDUMUN = "EDUMUN"
    PACMUN = "PACMUN"
    SEATTLEMUN = "Seattle MUN"
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
    def init_conferences():
        """Initialize the four standard conferences if they don't exist"""
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

    def __repr__(self):
        return f'<Conference {self.name}>'

class Participant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conference_id = db.Column(db.Integer, db.ForeignKey('conference.id'), nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    participant_type = db.Column(db.String(50), nullable=False)  # Delegate, Advisor, Staff, Secretariat
    committee = db.Column(db.String(100))
    position = db.Column(db.String(100))
    school = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # Relationships
    received_messages = db.relationship('MessageRecipient', backref='participant', lazy=True)

    def __repr__(self):
        return f'<Participant {self.first_name} {self.last_name}>'

class MessageTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.now)
    is_default = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f'<MessageTemplate {self.name}>'

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    sent_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    sent_at = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(20), default='pending')  # pending, sent, failed
    recipient_count = db.Column(db.Integer)
    template_id = db.Column(db.Integer, db.ForeignKey('message_template.id'), nullable=True)
    
    # Relationships
    recipients = db.relationship('MessageRecipient', backref='message', lazy=True)
    template = db.relationship('MessageTemplate')

    def __repr__(self):
        return f'<Message {self.id} - {self.status}>'

class MessageRecipient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)
    participant_id = db.Column(db.Integer, db.ForeignKey('participant.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, sent, failed
    sent_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    
    def __repr__(self):
        return f'<MessageRecipient {self.id} - {self.status}>'

# Optional: Audit log for tracking important system events
class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.now())
    
    def __repr__(self):
        return f'<AuditLog {self.action}>'