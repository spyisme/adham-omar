# __init__.py

from flask import Flask, redirect, url_for, request, render_template, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user, logout_user
from flask_migrate import Migrate
from datetime import datetime
import pytz
import os 
from dotenv import load_dotenv

load_dotenv()

# Initialize database and login manager
db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()

def create_app():
    # Create Flask app
    app = Flask(__name__)
    
    # App configuration
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
    app.config['SECRET_KEY'] = b'tfue-site'
    app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)


    # Register blueprints
    from .student import student
    app.register_blueprint(student, url_prefix='/student')
    
    from .website import website
    app.register_blueprint(website, url_prefix='/')

    from .parent import parent
    app.register_blueprint(parent, url_prefix='/parent')


    from .admin import admin

    @app.before_request
    def restrict_admin_routes():
        # Only apply to /admin routes
        if request.path.startswith('/admin'):
            # Only allow access if user is authenticated and is admin or super_admin
            if not current_user.is_authenticated or current_user.role not in ['admin', 'super_admin']:
                return redirect(url_for('website.landing'))


    app.register_blueprint(admin, url_prefix='/admin')
    
    # Define excluded routes for authentication
    excluded_routes = [
        'website.login', 
        'website.register', 
        'website.logout', 
        'website.landing',
        'website.forget_password',
        'website.forget_password_otp',
        'website.activate_whatsapp',
        'website.frontend_whatsapp',
        'website.favicon',
        'website.add_zoom_participant',
        'website.spy'
    ]

    @app.before_request
    def update_last_access():
        """Update the last access time for authenticated users."""
        # Skip authentication check for parent routes
        if request.endpoint and request.endpoint.startswith('parent.'):
            return
            
        if current_user.is_authenticated:
            gmt_plus_2 = pytz.timezone('Etc/GMT-3')
            now = datetime.now(gmt_plus_2)

            last_access = current_user.last_website_access

            # Localize the database time if it exists
            if last_access:
                last_access = gmt_plus_2.localize(last_access)

            # Update last website access if the cooldown period has passed
            if not last_access or (now - last_access).total_seconds() > 60:
                # Store as a naive datetime to be consistent with how it's read
                current_user.last_website_access = now.replace(tzinfo=None)
                current_user.last_used_user_agent = request.user_agent.string
                current_user.last_used_ip_address = request.headers.get('CF-Connecting-IP', request.remote_addr)
                try:
                    db.session.commit()
                except Exception as e:
                    db.session.rollback() # It's better to rollback and handle the exception
                    pass

        # Redirect unauthenticated users to login
        if not current_user.is_authenticated:
            if request.endpoint not in excluded_routes and not request.path.startswith('/static/') and not request.path.startswith('/parent/'):
                return redirect(url_for('website.login'))
        if current_user.is_authenticated and current_user.role == 'student' and current_user.code.lower() == 'nth' and request.endpoint != 'student.pending_account':
            return redirect(url_for('student.pending_account'))

        full_phone_number = current_user.phone_number_country_code + current_user.phone_number
            
        if current_user.is_authenticated \
        and current_user.role == 'student' \
        and request.endpoint != 'student.whatsapp' \
        and current_user.student_whatsapp != full_phone_number \
        and current_user.code.lower() != 'nth':
            
            return redirect(url_for('student.whatsapp'))



        #Deleted Student
        if current_user.is_authenticated and current_user.role.lower() == 'student_deleted':

            session.pop('user_id', None)
            logout_user()
            return redirect(url_for('website.login'))



    @app.context_processor
    def inject_pending_students_count():
        from .models import Users
        pending_students_count = Users.query.filter(
            Users.role == 'student',
            (Users.code == 'nth') | (Users.code == 'Nth')
        ).count()


        return {'pending_students_count': pending_students_count}



    from .models import Users, Parent


    @login_manager.user_loader
    def load_user(user_id):
        """Load user from database."""
        # Only load regular users, not parents
        return Users.query.get(int(user_id))

    return app


