from flask import Flask, render_template, redirect, url_for, request, flash, Blueprint
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from models import User, Conference, Participant, MessageTemplate, Message, MessageRecipient
from extensions import db
import os
import pandas as pd
from datetime import datetime
import re
import csv
from io import TextIOWrapper
from functools import wraps

routes = Blueprint('routes', __name__)

# Utility function for role checking
def role_required(role):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if current_user.role != role:
                flash('Access denied. Required role: {}'.format(role), 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

admin_required = role_required('Admin')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@routes.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            if not user.conference_id:
                return redirect(url_for('select_conference'))
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials', 'danger')
    return render_template('login.html')


@routes.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('routes.login'))

@routes.route('/dashboard')
@login_required
def dashboard():
    if not current_user.conference_id:
        return redirect(url_for('select_conference'))
    
    conference = Conference.query.get(current_user.conference_id)
    participant_count = Participant.query.filter_by(conference_id=current_user.conference_id).count()
    recent_messages = Message.query.filter_by(
        sent_by=current_user.id
    ).order_by(Message.sent_at.desc()).limit(5).all()
    
    return render_template('dashboard.html',
                         conference=conference,
                         participant_count=participant_count,
                         recent_messages=recent_messages)

@routes.route('/manage_users', methods=['GET', 'POST'])
@admin_required
def manage_users():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
        else:
            hashed_password = generate_password_hash(password)
            new_user = User(username=username, password=hashed_password, role=role)
            db.session.add(new_user)
            db.session.commit()
            flash('User created successfully!', 'success')
    
    users = User.query.all()
    return render_template('manage_users.html', users=users)

@routes.route('/select_conference', methods=['GET', 'POST'])
@login_required
def select_conference():
    if request.method == 'POST':
        conference_id = request.form.get('conference')
        if conference_id:
            current_user.conference_id = conference_id
            db.session.commit()
            return redirect(url_for('dashboard'))
    conferences = Conference.query.all()
    return render_template('select_conference.html', conferences=conferences)

@app.route('/upload_participants', methods=['GET', 'POST'])
@login_required
def upload_participants():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file uploaded', 'danger')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'danger')
            return redirect(request.url)
        
        if file and file.filename.endswith('.csv'):
            try:
                csv_file = TextIOWrapper(file, encoding='utf-8')
                csv_reader = csv.DictReader(csv_file)
                
                success_count = 0
                error_count = 0
                errors = []
                
                # Clear existing participants for this conference if requested
                if request.form.get('clear_existing') == 'yes':
                    Participant.query.filter_by(
                        conference_id=current_user.conference_id
                    ).delete()
                
                for row in csv_reader:
                    try:
                        phone = clean_phone_number(row.get('phone', ''))
                        if phone:
                            participant = Participant(
                                conference_id=current_user.conference_id,
                                first_name=row.get('first_name', '').strip(),
                                last_name=row.get('last_name', '').strip(),
                                email=row.get('email', '').strip(),
                                phone=phone,
                                participant_type=row.get('participant_type', '').strip(),
                                committee=row.get('committee', '').strip(),
                                position=row.get('position', '').strip(),
                                school=row.get('school', '').strip()
                            )
                            db.session.add(participant)
                            success_count += 1
                        else:
                            error_count += 1
                            errors.append(f"Invalid phone number for {row.get('first_name')} {row.get('last_name')}")
                    except Exception as e:
                        error_count += 1
                        errors.append(f"Error processing {row.get('first_name')} {row.get('last_name')}: {str(e)}")
                
                db.session.commit()
                flash(f'Imported {success_count} participants successfully. {error_count} errors.', 'success')
                if errors:
                    for error in errors[:5]:  # Show first 5 errors
                        flash(error, 'warning')
                return redirect(url_for('manage_participants'))
            except Exception as e:
                flash(f'Error processing CSV file: {str(e)}', 'danger')
                return redirect(request.url)
    
    return render_template('upload_participants.html')


@routes.route('/manage_participants')
@login_required
def manage_participants():
    search = request.args.get('search', '')
    participant_type = request.args.get('type', '')
    
    query = Participant.query.filter_by(conference_id=current_user.conference_id)
    
    if search:
        query = query.filter(
            db.or_(
                Participant.first_name.ilike(f'%{search}%'),
                Participant.last_name.ilike(f'%{search}%'),
                Participant.email.ilike(f'%{search}%')
            )
        )
    
    if participant_type:
        query = query.filter_by(participant_type=participant_type)
    
    participants = query.order_by(Participant.last_name).all()
    conference = Conference.query.get(current_user.conference_id)
    
    return render_template('manage_participants.html',
                         participants=participants,
                         conference=conference,
                         search=search,
                         participant_type=participant_type)

# Initialize the database with default conferences
@routes.before_first_request
def initialize_conferences():
    Conference.init_default_conferences()

# Clean phone number utility function
def clean_phone_number(phone):
    phone = re.sub(r'\D', '', phone)
    if len(phone) == 10:
        return '+1' + phone  # Assuming US numbers, adjust as needed
    elif len(phone) == 11 and phone.startswith('1'):
        return '+1' + phone[1:]
    elif len(phone) == 12 and phone.startswith('+1'):
        return phone
    return None  # Invalid number