from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, Response
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash
from models import Admin, Conference, Participant, Message, MessageRecipient
from forms import LoginForm
from extensions import db
from io import TextIOWrapper
from datetime import datetime
from twilio.rest import Client
from dotenv import load_dotenv
import os
import csv
import re

routes = Blueprint('routes', __name__)

load_dotenv(".env")
twilio_client = Client(os.environ.get('TWILIO_ACCOUNT_SID'), os.environ.get('TWILIO_AUTH_TOKEN'))
twilio_number:str = os.environ.get('TWILIO_PHONE_NUMBER')

# Initialize the database with default conferences
@routes.before_app_request
def initialize_conferences():
    Conference.init_default_conferences()

@routes.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    
    if form.validate_on_submit():  # Ensures CSRF token is checked
        username = form.username.data
        password = form.password.data
        admin = Admin.query.filter_by(username=username).first()
        
        if admin and check_password_hash(admin.password, password):
            login_user(admin)
            if not admin.conference_id:
                return redirect(url_for('routes.select_conference'))
            return redirect(url_for('routes.dashboard'))

        flash('Invalid credentials', 'danger')
    
    conference = None
    return render_template('login.html', form=form, conference=conference)

@routes.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('routes.login'))

@routes.app_context_processor
def inject_conference():
    from models import Conference  # avoid circular import issues if needed
    if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
        if current_user.conference_id:
            conference = Conference.query.get(current_user.conference_id)
            return {'conference': conference}
    return {'conference': None}

@routes.route('/select_conference', methods=['GET', 'POST'])
@login_required
def select_conference():
    if request.method == 'POST':
        conference_id = request.form.get('conference')
        if conference_id:
            current_user.conference_id = conference_id
            db.session.commit()
            return redirect(url_for('routes.dashboard'))

    conferences = Conference.query.all()
    
    # Provide a default conference (first in the list) to avoid errors in base.html
    default_conference = conferences[0] if conferences else None
    
    return render_template('select_conference.html', conferences=conferences, conference=default_conference)

@routes.route('/dashboard')
@login_required
def dashboard():
    if not current_user.conference_id:
        return redirect(url_for('routes.select_conference'))
    
    conference = Conference.query.get(current_user.conference_id)
    participant_counts = {
        'delegates': Participant.query.filter_by(
            conference_id=current_user.conference_id,
            participant_type='Delegate'
        ).count(),
        'advisors': Participant.query.filter_by(
            conference_id=current_user.conference_id,
            participant_type='Advisor'
        ).count(),
        'staff': Participant.query.filter_by(
            conference_id=current_user.conference_id,
            participant_type='Staff'
        ).count(),
        'secretariat': Participant.query.filter_by(
            conference_id=current_user.conference_id,
            participant_type='Secretariat'
        ).count()
    }
    
    recent_messages = Message.query.filter_by(
        sent_by=current_user.id
    ).order_by(Message.sent_at.desc()).limit(5).all()
    
    return render_template('dashboard.html',
                         conference=conference,
                         participant_counts=participant_counts,
                         recent_messages=recent_messages)

@routes.route('/upload_participants', methods=['GET', 'POST'])
@login_required
def upload_participants():
    if not current_user.conference_id:
        return jsonify({
            'success': False,
            'message': 'No conference selected'
        }), 400

    if request.method == 'POST':
        try:
            if 'file' not in request.files:
                return jsonify({
                    'success': False,
                    'message': 'No file uploaded'
                }), 400

            file = request.files['file']
            if file.filename == '':
                return jsonify({
                    'success': False,
                    'message': 'No file selected'
                }), 400

            if not file.filename.endswith('.csv'):
                return jsonify({
                    'success': False,
                    'message': 'Invalid file type. Please upload a CSV file.'
                }), 400

            # Check file size (10MB limit)
            if file.content_length and file.content_length > 10 * 1024 * 1024:
                return jsonify({
                    'success': False,
                    'message': 'File too large. Maximum size is 10MB'
                }), 400

            try:
                csv_file = TextIOWrapper(file, encoding='utf-8')
                csv_reader = csv.DictReader(csv_file)
                
                # Validate CSV structure
                required_fields = {'first_name', 'last_name', 'phone', 'participant_type'}
                header_fields = set(csv_reader.fieldnames or [])
                
                if not required_fields.issubset(header_fields):
                    missing_fields = required_fields - header_fields
                    return jsonify({
                        'success': False,
                        'message': f'Missing required columns: {", ".join(missing_fields)}'
                    }), 400

                # Clear existing participants if checkbox is checked
                if request.form.get('clear_existing') == 'yes':
                    Participant.query.filter_by(conference_id=current_user.conference_id).delete()

                results = process_participant_upload(csv_reader, current_user.conference_id)
                db.session.commit()

                return jsonify({
                    'success': True,
                    'message': f'Successfully imported {results["success"]} participants. {results["errors"]} errors occurred.',
                    'errors': results['error_messages']
                })

            except Exception as e:
                db.session.rollback()
                return jsonify({
                    'success': False,
                    'message': f'Error processing CSV file: {str(e)}'
                }), 400

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500

    # GET request - render template
    conference = Conference.query.get(current_user.conference_id)
    return render_template('upload_participants.html', conference=conference)

def handle_response(message, success=True, error_messages=None):
    response_data = {
        'success': success,
        'message': message,
        'errors': error_messages or []
    }
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(response_data), (200 if success else 400)
    
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('routes.manage_participants') if success else request.url)

def process_participant_upload(csv_reader, conference_id):
    """Process CSV upload and return results summary"""
    success_count = 0
    error_count = 0
    error_messages = []
    
    for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 to account for header row
        try:
            # Validate required fields
            if not all(row.get(field, '').strip() for field in ['first_name', 'last_name', 'phone']):
                raise ValueError('Missing required fields')
            
            # Validate participant type
            participant_type = row.get('participant_type', '').strip()
            valid_types = {'Delegate', 'Advisor', 'Staff', 'Secretariat'}
            if participant_type not in valid_types:
                raise ValueError(f'Invalid participant type. Must be one of: {", ".join(valid_types)}')
            
            # Clean and validate phone number
            phone = clean_phone_number(row.get('phone', ''))
            if not phone:
                raise ValueError('Invalid phone number format')
            
            # Check for existing participant with same phone number
            existing = Participant.query.filter_by(
                conference_id=conference_id,
                phone=phone
            ).first()
            
            if existing:
                # Update existing participant
                existing.first_name = row['first_name'].strip()
                existing.last_name = row['last_name'].strip()
                existing.participant_type = participant_type
            else:
                # Create new participant
                participant = Participant(
                    conference_id=conference_id,
                    first_name=row['first_name'].strip(),
                    last_name=row['last_name'].strip(),
                    phone=phone,
                    participant_type=participant_type
                )
                db.session.add(participant)
            
            success_count += 1
            
        except Exception as e:
            error_count += 1
            error_messages.append(
                f"Row {row_num}: Error processing {row.get('first_name', '')} {row.get('last_name', '')}: {str(e)}"
            )
    
    return {
        'success': success_count,
        'errors': error_count,
        'error_messages': error_messages
    }

@routes.route('/manage_participants')
@login_required
def manage_participants():
    if not current_user.conference_id:
        return redirect(url_for('routes.select_conference'))
    
    search = request.args.get('search', '')
    participant_type = request.args.get('type', '')
    
    query = Participant.query.filter_by(conference_id=current_user.conference_id)
    
    if search:
        query = query.filter(
            db.or_(
                Participant.first_name.ilike(f'%{search}%'),
                Participant.last_name.ilike(f'%{search}%'),
                Participant.phone.ilike(f'%{search}%')
            )
        )
    
    if participant_type:
        query = query.filter_by(participant_type=participant_type)
    
    participants = query.order_by(Participant.last_name).all()
    participant_types = ['Delegate', 'Advisor', 'Staff', 'Secretariat']
    
    return render_template('manage_participants.html',
                         participants=participants,
                         participant_types=participant_types,
                         search=search,
                         current_type=participant_type)

@routes.route('/participant/<int:participant_id>', methods=['GET'])
@login_required
def get_participant(participant_id):
    participant = Participant.query.get_or_404(participant_id)
    return jsonify({
        'id': participant.id,
        'first_name': participant.first_name,
        'last_name': participant.last_name,
        'phone': participant.phone,
        'participant_type': participant.participant_type,
    })

@routes.route('/participant', methods=['POST'])
@login_required
def add_participant():
    data = request.get_json()
    participant = Participant(
        conference_id=current_user.conference_id,
        first_name=data['first_name'],
        last_name=data['last_name'],
        phone=clean_phone_number(data['phone']),
        participant_type=data['participant_type'],
    )
    db.session.add(participant)
    db.session.commit()
    return jsonify({'status': 'success'})

@routes.route('/participant/<int:participant_id>', methods=['PUT'])
@login_required
def update_participant(participant_id):
    participant = Participant.query.get_or_404(participant_id)
    data = request.get_json()

    if not data:
        return jsonify({'status': 'error', 'message': 'Invalid data'}), 400

    participant.first_name = data['first_name']
    participant.last_name = data['last_name']
    participant.phone = clean_phone_number(data['phone'])
    participant.participant_type = data['participant_type']

    db.session.commit()

    return jsonify({'status': 'success'})

@routes.route('/participant/<int:participant_id>', methods=['DELETE'])
@login_required
def delete_participant(participant_id):
    participant = Participant.query.get_or_404(participant_id)
    db.session.delete(participant)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Participant deleted'})

def clean_phone_number(phone:str) -> str:
    """Clean and validate phone number"""
    phone = re.sub(r'\D', '', phone)
    if len(phone) == 10:
        return '+1' + phone
    elif len(phone) == 11 and phone.startswith('1'):
        return '+' + phone
    elif len(phone) == 12 and phone.startswith('+1'):
        return phone
    return None

@routes.route('/send_message', methods=['GET', 'POST'])
@login_required
def send_message():
    if not current_user.conference_id:
        return jsonify({'success': False, 'message': 'No conference selected'}), 400

    if request.method == 'GET':
        # Fetch all secretariat members for the conference
        secretariat_members = Participant.query.filter_by(
            conference_id=current_user.conference_id,
            participant_type="Secretariat"
        ).all()

        return render_template(
            'send_message.html',
            conference=current_user.conference,
            secretariat_members=secretariat_members
        )

    data = request.get_json()
    message_content = data.get('message', '').strip()
    recipient_types = data.get('recipient_types', [])

    if not message_content or not recipient_types:
        return jsonify({'success': False, 'message': 'Message content and at least one recipient type are required'}), 400

    valid_types = {'Delegate', 'Advisor', 'Staff', 'Secretariat'}
    selected_types = set(recipient_types).intersection(valid_types)

    # Collect individual secretariat selections separately
    secretariat_selected = "Secretariat" in recipient_types
    individual_secretariat_names = [r for r in recipient_types if r not in valid_types]

    # Query standard participant types
    recipients = Participant.query.filter(
        Participant.conference_id == current_user.conference_id,
        Participant.participant_type.in_(list(selected_types))
    ).all()

    # Query individually selected secretariat members
    if individual_secretariat_names:
        secretariat_recipients = Participant.query.filter(
            Participant.conference_id == current_user.conference_id,
            Participant.participant_type == "Secretariat",
            Participant.first_name.in_([name.split()[0] for name in individual_secretariat_names]),
            Participant.last_name.in_([name.split()[1] for name in individual_secretariat_names if " " in name])
        ).all()
        recipients.extend(secretariat_recipients)

    if not recipients:
        return jsonify({'success': False, 'message': 'No recipients found for the selected categories'}), 400

    sent_count = 0
    failed_count = 0
    errors = []

    message_entry = Message(
        content=message_content,
        sent_by=current_user.id,
        recipient_count=len(recipients),
        status='sent'
    )
    db.session.add(message_entry)
    db.session.commit()

    for recipient in recipients:
        try:
            personalized_message = message_content.format(
                first_name=recipient.first_name,
                last_name=recipient.last_name,
                phone=recipient.phone,
                participant_type=recipient.participant_type
            )

            response = send_sms_twilio(recipient.phone, personalized_message)

            if response['status'] == 'sent':
                message_recipient = MessageRecipient(
                    message_id=message_entry.id,
                    participant_id=recipient.id,
                    status='sent',
                    sent_at=datetime.now()
                )
                db.session.add(message_recipient)
                sent_count += 1
            else:
                failed_count += 1
                errors.append(f"Failed to send message to {recipient.first_name} {recipient.last_name}: {response['error']}")

        except Exception as e:
            failed_count += 1
            errors.append(f"Error processing {recipient.first_name} {recipient.last_name}: {str(e)}")

    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'Messages sent: {sent_count}, Failed: {failed_count}',
        'errors': errors
    })


def send_sms_twilio(to:str, message:str):
    """Send SMS using Twilio API."""
    try:
        message = twilio_client.messages.create(
            from_=twilio_number,
            to=to,
            body=message
        )
        return {'status': 'sent', 'sid': message.sid}
    except Exception as e:
        return {'status': 'failed', 'error': str(e)}
    