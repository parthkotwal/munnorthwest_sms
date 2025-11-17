from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, Response, current_app
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash
from models import Admin, Conference, Participant, Message, MessageRecipient
from forms import LoginForm
from extensions import db
from io import TextIOWrapper
from datetime import datetime
from twilio.rest import Client
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
import os
import csv
import re

routes = Blueprint('routes', __name__)

# env variables
load_dotenv(".env")
twilio_client = Client(os.environ.get('TWILIO_ACCOUNT_SID'), os.environ.get('TWILIO_AUTH_TOKEN'))
twilio_number:str = os.environ.get('TWILIO_PHONE_NUMBER')


################### INITIAL STUFF ###################
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
    
    recent_messages = Message.query.filter(
        Message.sent_by == current_user.id,
        Message.status == 'sent'
    ).order_by(Message.sent_at.desc()).limit(5).all()
    
    scheduled_messages = Message.query.filter(
        Message.sent_by == current_user.id,
        Message.status == 'scheduled'
    ).order_by(Message.scheduled_at.asc()).all()

    return render_template(
        'dashboard.html',
        conference=conference,
        participant_counts=participant_counts,
        recent_messages=recent_messages,
        scheduled_messages=scheduled_messages
    )


################### UPLOADING PARTICIPANTS ###################

def try_read_csv(file):
    """Try reading CSV with different encodings and handle BOM"""
    encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']
    
    for encoding in encodings:
        try:
            # Reset file pointer
            file.seek(0)
            csv_file = TextIOWrapper(file, encoding=encoding)
            reader = csv.DictReader(csv_file)

            if not reader.fieldnames:
                continue

            # Clean header fields - remove BOM and whitespace
            cleaned_headers = [
                field.strip().lstrip('\ufeff') for field in reader.fieldnames
            ]
            reader.fieldnames = cleaned_headers
            header_fields = set(cleaned_headers)
            
            return reader, header_fields
            
        except UnicodeDecodeError:
            continue
        except Exception as e:
            current_app.logger.error(f"Error reading CSV with {encoding}: {str(e)}")
            continue
            
    raise ValueError("Unable to read CSV file with any supported encoding")

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

            try:
                csv_reader, header_fields = try_read_csv(file)
                
                # Validate CSV structure
                required_fields = {'first_name', 'last_name', 'phone', 'participant_type'}
                
                # Log headers for debugging
                current_app.logger.info(f"CSV Headers found: {header_fields}")
                current_app.logger.info(f"Required fields: {required_fields}")
                
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

def process_participant_upload(csv_reader, conference_id):
    """Process CSV upload and return results summary"""
    success_count = 0
    error_count = 0
    error_messages = []
    
    for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 to account for header row
        try:
            # trim whitespace and normalize keys (handles BOM or stray spaces)
            row = {
                (k.strip().lstrip('\ufeff') if isinstance(k, str) else k):
                (v.strip() if isinstance(v, str) else v)
                for k, v in row.items()
            }

            # validate required fields
            if not all(row.get(field, '').strip() for field in ['first_name', 'last_name', 'phone']):
                raise ValueError('Missing required fields')
            
            # validate participant type
            participant_type = row.get('participant_type', '').strip()
            valid_types = {'Delegate', 'Advisor', 'Staff', 'Secretariat'}
            if participant_type not in valid_types:
                raise ValueError(f'Invalid participant type. Must be one of: {", ".join(valid_types)}')
            
            # clean and validate phone number
            phone = clean_phone_number(row.get('phone', ''))
            if not phone:
                raise ValueError('Invalid phone number format')
            
            # check for existing participant with same phone number
            existing = Participant.query.filter_by(
                conference_id=conference_id,
                phone=phone
            ).first()
            
            if existing:
                # update existing participant
                existing.first_name = row['first_name'].strip()
                existing.last_name = row['last_name'].strip()
                existing.participant_type = participant_type
            else:
                # create new participant
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

################### MANAGING CURRENT PARTICIPANTS ###################

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

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render_template('partials/participants_table.html', participants=participants, participant_types=participant_types)
 
    return render_template('manage_participants.html', participants=participants, participant_types=participant_types, search=search, current_type=participant_type)


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

################### MESSAGING ###################

@routes.route('/send_message', methods=['GET', 'POST'])
@login_required
def send_message():
    if not current_user.conference_id:
        return jsonify({'success': False, 'message': 'No conference selected'}), 400

    if request.method == 'GET':
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
    scheduled_at = data.get('scheduled_at')

    if not message_content or not recipient_types:
        return jsonify({'success': False, 'message': 'Message content and at least one recipient type are required'}), 400

    valid_types = {'Delegate', 'Advisor', 'Staff', 'Secretariat'}
    selected_types = set(recipient_types).intersection(valid_types)
    individual_secretariat_names = [r for r in recipient_types if r not in valid_types]
    
    if individual_secretariat_names:
        selected_types.discard('Secretariat')

    # query standard participant types
    recipients = db.session.execute(
        db.select(Participant).where(
            Participant.conference_id == current_user.conference_id,
            Participant.participant_type.in_(selected_types)
        )
    ).scalars().all()

    # query individually selected secretariat members
    if individual_secretariat_names:
        first_names = [name.split()[0] for name in individual_secretariat_names]
        last_names = [name.split()[1] for name in individual_secretariat_names if " " in name]

        secretariat_recipients = Participant.query.filter(
            Participant.conference_id == current_user.conference_id,
            Participant.participant_type == "Secretariat",
            Participant.first_name.in_(first_names),
            Participant.last_name.in_(last_names)
        ).all()
        recipients.extend(secretariat_recipients)

    if not recipients:
        return jsonify({'success': False, 'message': 'No recipients found for the selected categories'}), 400

    if scheduled_at:
        try:
            # first try ISO format (with T)
            scheduled_at = datetime.strptime(scheduled_at, "%Y-%m-%dT%H:%M")
        except ValueError:
            try:
                # fallback to space format
                scheduled_at = datetime.strptime(scheduled_at, "%Y-%m-%d %H:%M")
            except ValueError:
                return jsonify({
                    'success': False, 
                    'message': 'Invalid datetime format. Expected YYYY-MM-DD HH:MM'
                }), 400
            
    message_entry = Message(
        content=message_content,
        sent_by=current_user.id,
        recipient_count=len(recipients),
        status='scheduled' if scheduled_at else 'pending',
        scheduled_at=scheduled_at
    )
    db.session.add(message_entry)
    db.session.commit()

    message_recipients = [
        MessageRecipient(
            message_id=message_entry.id,
            participant_id=recipient.id,
            status='pending'
        )
        for recipient in recipients
    ]

    db.session.bulk_save_objects(message_recipients)
    db.session.commit()

    if not scheduled_at:
        if not send_messages_now(message_entry, recipients):
            # print("Falling back to backup sending method...")
            send_messages_now_backup(message_entry, recipients)

    return jsonify({
        'success': True,
        'message': 'Message scheduled successfully' if scheduled_at else 'Message sent successfully'
    })
 
def send_messages_now(message_entry: Message, recipients):
    # print(f"Sending Message ID: {message_entry.id} to {len(recipients)} recipients")
    
    # store the current application context
    ctx = current_app._get_current_object()

    def send_to_recipient(recipient):
        # push a new application context for this thread
        with ctx.app_context():
            try:
                personalized_message = message_entry.content.format(
                    first_name=recipient.first_name,
                    last_name=recipient.last_name,
                    phone=recipient.phone,
                    participant_type=recipient.participant_type
                )
                
                response = send_sms_twilio(recipient.phone, personalized_message)
                
                if response['status'] == 'sent':
                    # Create recipient entry
                    message_recipient = MessageRecipient(
                        message_id=message_entry.id,
                        participant_id=recipient.id,
                        status='sent',
                        sent_at=datetime.now()
                    )
                    # Return the recipient entry to be added in the main thread
                    return {
                        "success": True,
                        "recipient": message_recipient,
                        "error": None
                    }
                else:
                    return {
                        "success": False,
                        "recipient": None,
                        "error": response.get('error', 'Unknown error')
                    }
            
            except Exception as e:
                return {
                    "success": False,
                    "recipient": None,
                    "error": str(e)
                }
    try:
        # Execute in thread pool
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(send_to_recipient, recipient) for recipient in recipients]
            results = [future.result() for future in futures]

        # Process results in main thread where we already have app context
        sent_count = 0
        failed_count = 0
        
        # Batch add successful message recipients
        successful_recipients = [
            result["recipient"] for result in results 
            if result["success"] and result["recipient"] is not None
        ]
        
        if successful_recipients:
            db.session.bulk_save_objects(successful_recipients)
            sent_count = len(successful_recipients)
        
        failed_count = len(results) - sent_count
        
        # Log failures
        for result in results:
            if not result["success"]:
                # print(f"Message sending failed: {result['error']}")
                continue
        
        # Update message status
        message_entry.status = "sent"
        db.session.commit()
        # print(f"Bulk Message Sent: {sent_count}, Failed: {failed_count}")

        # flash("Message sent successfully!", "success")
        return True

    except Exception as e:
        # print(f"Bulk sending failed with error: {str(e)}. Falling back to backup method.")
        return False 
    
def send_messages_now_backup(message_entry:Message, recipients):
    # print(f"[BACKUP] Sending Message ID: {message_entry.id} to {len(recipients)} recipients")

    sent_count = 0
    failed_count = 0
    errors = []

    for recipient in recipients:
        try:
            personalized_message = message_entry.content.format(
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

    message_entry.status = "sent"
    db.session.commit()
    # print(f"[BACKUP] Message {message_entry.id} Sent: {sent_count}, Failed: {failed_count}, Errors: {errors}")

    # flash("Message sent successfully!", "success")

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


################### SCHEDULING ###################

@routes.route('/cancel_scheduled_message/<int:message_id>', methods=['POST'])
@login_required
def cancel_scheduled_message(message_id):
    """Cancel a scheduled message"""
    message = Message.query.get_or_404(message_id)
    
    if message.sent_by != current_user.id:
        flash('You do not have permission to cancel this message', 'danger')
        return redirect(url_for('routes.dashboard'))
    
    # Check if message is actually scheduled
    if message.status != 'scheduled':
        flash('This message cannot be cancelled', 'danger')
        return redirect(url_for('routes.dashboard'))
    
    try:
        # Delete any message recipients
        MessageRecipient.query.filter_by(message_id=message.id).delete()
        db.session.delete(message)
        db.session.commit()
        flash('Scheduled message has been cancelled', 'success')

    except Exception as e:
        db.session.rollback()
        flash('Error cancelling message', 'danger')
    
    return redirect(url_for('routes.dashboard'))

@routes.route("/check-scheduled-messages", methods=["GET"])
def check_scheduled_messages():
    # Fetch all scheduled messages and their status
    scheduled_messages = Message.query.filter_by(status="scheduled").all()
    messages_data = []
    for message in scheduled_messages:
        messages_data.append({
            "id": message.id,
            "status": message.status,
            "sent_at": message.sent_at.strftime('%Y-%m-%d %H:%M') if message.sent_at else None,
            "content": message.content,
            "recipient_count": message.recipient_count
        })
    
    return jsonify({"messages": messages_data})
