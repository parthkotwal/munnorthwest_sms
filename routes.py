from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash
from models import Admin, Conference, Participant, Message, MessageRecipient
from extensions import db
import csv
from io import TextIOWrapper
import re
from datetime import datetime

routes = Blueprint('routes', __name__)

@routes.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        admin = Admin.query.filter_by(username=username).first()
        
        if admin and check_password_hash(admin.password, password):
            login_user(admin)
            if not admin.conference_id:
                return redirect(url_for('routes.select_conference'))
            return redirect(url_for('routes.dashboard'))
        
        flash('Invalid credentials', 'danger')
    
    conference = None
    return render_template('login.html', conference=conference)

@routes.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('routes.login'))

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
        return redirect(url_for('routes.select_conference'))

    conference = Conference.query.get(current_user.conference_id)

    if request.method == 'POST':
        if 'file' not in request.files:
            return handle_response('No file uploaded', success=False)

        file = request.files['file']
        if file.filename == '':
            return handle_response('No file selected', success=False)

        if file and file.filename.endswith('.csv'):
            try:
                csv_file = TextIOWrapper(file, encoding='utf-8')
                csv_reader = csv.DictReader(csv_file)
                
                # Clear existing participants if checkbox is checked
                if request.form.get('clear_existing') == 'yes':
                    Participant.query.filter_by(conference_id=current_user.conference_id).delete()
                
                results = process_participant_upload(csv_reader, current_user.conference_id)
                
                db.session.commit()
                
                message = (f'Successfully imported {results["success"]} participants. '
                           f'{results["errors"]} errors occurred.')

                # Handle AJAX request separately
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return handle_response(message, success=True, error_messages=results['error_messages'])

                flash(message, 'success')
                
                # Show up to 5 errors in Flask Flash messages
                if results['error_messages']:
                    for error in results['error_messages'][:5]:
                        flash(error, 'warning')

                return redirect(url_for('routes.manage_participants'))
            
            except Exception as e:
                return handle_response(f'Error processing CSV file: {str(e)}', success=False)

    return render_template('upload_participants.html', conference=conference)

def handle_response(message, success=True, error_messages=None):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': success, 'message': message, 'errors': error_messages or []}), (200 if success else 400)
    
    flash(message, 'success' if success else 'danger')
    return redirect(request.url)


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

def process_participant_upload(csv_reader, conference_id):
    """Process CSV upload and return results summary"""
    success_count = 0
    error_count = 0
    error_messages = []
    
    for row in csv_reader:
        try:
            phone = clean_phone_number(row.get('phone', ''))
            if not phone:
                raise ValueError('Invalid phone number')
            
            participant = Participant(
                conference_id=conference_id,
                first_name=row.get('first_name', '').strip(),
                last_name=row.get('last_name', '').strip(),
                phone=phone,
                participant_type=row.get('participant_type', '').strip(),
            )
            
            db.session.add(participant)
            success_count += 1
            
        except Exception as e:
            error_count += 1
            error_messages.append(
                f"Error processing {row.get('first_name', '')} {row.get('last_name', '')}: {str(e)}"
            )
    
    return {
        'success': success_count,
        'errors': error_count,
        'error_messages': error_messages
    }

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
    participant.first_name = data['first_name']
    participant.last_name = data['last_name']
    participant.phone = clean_phone_number(data['phone'])
    participant.participant

def clean_phone_number(phone):
    """Clean and validate phone number"""
    phone = re.sub(r'\D', '', phone)
    if len(phone) == 10:
        return '+1' + phone
    elif len(phone) == 11 and phone.startswith('1'):
        return '+' + phone
    elif len(phone) == 12 and phone.startswith('+1'):
        return phone
    return None

# Initialize the database with default conferences
@routes.before_app_request
def initialize_conferences():
    Conference.init_default_conferences()