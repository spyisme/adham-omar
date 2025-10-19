from re import T
from flask_login import UserMixin
from datetime import datetime
import pytz
from sqlalchemy.ext.associationproxy import association_proxy
from . import db

# Define the timezone
gmt_plus_2 = pytz.timezone('Etc/GMT-3')

# ============================================================
# Association Tables for Many-to-Many Scoping
# ============================================================

# Users can belong to multiple groups
user_groups = db.Table(
    'user_groups',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True),
)

# Assignments can target multiple groups
assignment_groups = db.Table(
    'assignment_groups',
    db.Column('assignment_id', db.Integer, db.ForeignKey('assignments.id', ondelete='CASCADE'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True),
)

# Quizzes can target multiple groups
quiz_groups = db.Table(
    'quiz_groups',
    db.Column('quiz_id', db.Integer, db.ForeignKey('quizzes.id', ondelete='CASCADE'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True),
)

# Announcements can target multiple groups
announcement_groups = db.Table(
    'announcement_groups',
    db.Column('announcement_id', db.Integer, db.ForeignKey('announcements.id', ondelete='CASCADE'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True),
)

# Materials Folders can target multiple groups
folder_groups = db.Table(
    'folder_groups',
    db.Column('folder_id', db.Integer, db.ForeignKey('materials_folder.id', ondelete='CASCADE'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True),
)

# Sessions can target multiple groups
session_groups = db.Table(
    'session_groups',
    db.Column('session_id', db.Integer, db.ForeignKey('sessions.id', ondelete='CASCADE'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True),
)

# NextQuiz can target multiple groups
next_quiz_groups = db.Table(
    'next_quiz_groups',
    db.Column('next_quiz_id', db.Integer, db.ForeignKey('next_quiz.id', ondelete='CASCADE'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True),
)

# Attendance session can target multiple groups
attendance_groups = db.Table(
    'attendance_groups',
    db.Column('attendance_session_id', db.Integer, db.ForeignKey('attendance_session.id', ondelete='CASCADE'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True),
)

# Zoom meetings can target multiple groups
zoom_meeting_groups = db.Table(
    'zoom_meeting_groups',
    db.Column('zoom_meeting_id', db.Integer, db.ForeignKey('zoom_meeting.id', ondelete='CASCADE'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True),
)

# ============================================================
# Association Tables for Assistant Management Scope
# ============================================================
assistant_managed_groups = db.Table(
    'assistant_managed_groups',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('groups.id'), primary_key=True)
)

# ============================================================
# Students & Admins
# ============================================================

class Users(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone_number = db.Column(db.String(15), unique=True, nullable=True)
    phone_number_country_code = db.Column(db.String(5), nullable=True, default="2")
    parent_phone_number = db.Column(db.String(15), unique=True, nullable=True)
    parent_phone_number_country_code = db.Column(db.String(5), nullable=True, default="2")
    password = db.Column(db.String(240), nullable=False)
    
    zoom_id = db.Column(db.String(120), nullable=True) 
    
    student_whatsapp = db.Column(db.String(240), nullable=True)
    parent_whatsapp = db.Column(db.String(240), nullable=True)

    login_count = db.Column(db.Integer, default=0)
    last_website_access = db.Column(db.DateTime, nullable=True)

    last_used_user_agent = db.Column(db.String(240), nullable=True)
    last_used_ip_address = db.Column(db.String(240), nullable=True)

    # Legacy groupid field (will be migrated to many-to-many, then can be removed later)
    groupid = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=True)

    role = db.Column(db.String(50), default="student", nullable=False)

    # OTP should be string to preserve leading zeros and codes like "A123".
    otp = db.Column(db.String(50), nullable=True)

    code = db.Column(db.String(50), nullable=False, default="Nth")
    points = db.Column(db.Integer, nullable=False, default=0)

    parent_type = db.Column(db.String(50), nullable=True)
    parent_email = db.Column(db.String(120), nullable=True)
    parent_name = db.Column(db.String(120), nullable=True)
    profile_picture = db.Column(db.String(120), nullable=True)

    # ============================================================
    # Relationships
    # ============================================================
    
    # Many-to-many: Users can belong to multiple groups
    groups = db.relationship('Groups', secondary=user_groups, back_populates='members', lazy='dynamic')
    
    # Legacy single group relationship (for migration purposes)
    group = db.relationship('Groups', foreign_keys=[groupid], back_populates='users')

    submissions = db.relationship('Submissions', back_populates='student', lazy='dynamic', cascade="all, delete-orphan",
                                  foreign_keys='Submissions.student_id')

    corrected_submissions = db.relationship('Submissions', 
                                           back_populates='corrector', 
                                           lazy='dynamic', 
                                           foreign_keys='Submissions.corrected_by_id')

    reviewed_submissions = db.relationship('Submissions', 
                                           back_populates='reviewer', 
                                           lazy='dynamic', 
                                           foreign_keys='Submissions.reviewed_by_id')

    quiz_grades = db.relationship('QuizGrades', back_populates='student', lazy='dynamic', cascade="all, delete-orphan", foreign_keys='QuizGrades.student_id')
    video_views = db.relationship('VideoViews', back_populates='student', lazy='dynamic', cascade="all, delete-orphan")
    corrected_quizzes = db.relationship('QuizGrades', back_populates='corrector', lazy='dynamic', foreign_keys='QuizGrades.corrector_id')
    sessions = db.relationship(
        'Sessions',
        back_populates='uploader',
        lazy='dynamic',
        foreign_keys='Sessions.added_by'
    )
    assistant_logs = db.relationship('AssistantLogs', back_populates='user')

    parent = db.relationship('Parent', back_populates='student', uselist=False)

    # ============================================================
    # Assistant Management Relationships
    # ============================================================
    managed_groups = db.relationship(
        'Groups', 
        secondary=assistant_managed_groups,
        back_populates='managers'
    )

    # ============================================================
    # Attendance Relationships
    # ============================================================
    attendance_student = db.relationship('Attendance_student', back_populates='student', lazy='dynamic', cascade="all, delete-orphan")

    # ============================================================
    # Zoom Meeting Relationships
    # ============================================================
    meeting_memberships = db.relationship(
        "ZoomMeetingMember", 
        back_populates="user", 
        cascade="all, delete-orphan"
    )
    
    participating_zoom_meetings = association_proxy("meeting_memberships", "meeting")


class Parent(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    phone_number = db.Column(db.String(15), unique=True, nullable=False)
    password = db.Column(db.String(240), nullable=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    login_count = db.Column(db.Integer, default=0)
    last_website_access = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))

    student = db.relationship('Users', back_populates='parent', passive_deletes=True)


# ============================================================
# Groups (Simplified - Only Taxonomy Now)
# ============================================================
class Groups(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)

    # Many-to-many: Groups can have multiple users
    members = db.relationship('Users', secondary=user_groups, back_populates='groups', lazy='dynamic')
    
    # Legacy single user relationship (for migration purposes)
    users = db.relationship('Users', foreign_keys=[Users.groupid], back_populates='group', lazy='dynamic')
    
    # Legacy one-to-many scopes (kept for backward compatibility during migration)
    assignments = db.relationship('Assignments', back_populates='group', lazy='dynamic')
    announcements = db.relationship('Announcements', back_populates='group', lazy='dynamic')
    sessions = db.relationship('Sessions', back_populates='group', lazy='dynamic')
    quizzes = db.relationship('Quizzes', back_populates='group', lazy='dynamic')
    materials_folder = db.relationship('Materials_folder', back_populates='group', lazy='dynamic')
    next_quiz = db.relationship('NextQuiz', back_populates='group', lazy='dynamic')
    attendance_session = db.relationship('Attendance_session', back_populates='group', lazy='dynamic')
    
    # Many-to-many scopes
    assignments_mm = db.relationship('Assignments', secondary=assignment_groups, back_populates='groups_mm', lazy='dynamic')
    quizzes_mm = db.relationship('Quizzes', secondary=quiz_groups, back_populates='groups_mm', lazy='dynamic')
    announcements_mm = db.relationship('Announcements', secondary=announcement_groups, back_populates='groups_mm', lazy='dynamic')
    folders_mm = db.relationship('Materials_folder', secondary=folder_groups, back_populates='groups_mm', lazy='dynamic')
    sessions_mm = db.relationship('Sessions', secondary=session_groups, back_populates='groups_mm', lazy='dynamic')
    next_quiz_mm = db.relationship('NextQuiz', secondary=next_quiz_groups, back_populates='groups_mm', lazy='dynamic')
    attendance_session_mm = db.relationship('Attendance_session', secondary=attendance_groups, back_populates='groups_mm', lazy='dynamic')
    
    # Assistant management
    managers = db.relationship('Users', secondary=assistant_managed_groups, back_populates='managed_groups')


# ============================================================
# Assignments / Submissions / Announcements
# ============================================================
class Assignments(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    deadline_date = db.Column(db.DateTime, nullable=False)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    type = db.Column(db.String(50), nullable=False, server_default="Assignment")
    out_of = db.Column(db.Integer, nullable=True, default=0)

    creation_date = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    last_edited_at = db.Column(db.DateTime, nullable=True)
    last_edited_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # Close after deadline
    close_after_deadline = db.Column(db.Boolean, default=False)

    # Legacy single-scope field (kept for backward compatibility during migration)
    groupid = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=True)

    attachments = db.Column(db.JSON, nullable=True)
    status = db.Column(db.String(50), nullable=False, server_default="Show")
    points = db.Column(db.Integer, nullable=False, default=0)

    # Legacy single group relationship
    group = db.relationship('Groups', back_populates='assignments')
       
    # Many-to-many groups
    groups_mm = db.relationship('Groups', secondary=assignment_groups, back_populates='assignments_mm')

    submissions = db.relationship('Submissions', back_populates='assignment', lazy='dynamic', cascade="all, delete-orphan")

    # Extra Fields
    student_whatsapp = db.Column(db.Boolean, default=True)
    parent_whatsapp = db.Column(db.Boolean, default=True)


class Submissions(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignments.id'), nullable=False)
    upload_time = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))
    file_url = db.Column(db.Text, nullable=False)
    mark = db.Column(db.Text, nullable=True)
    corrected = db.Column(db.Boolean, default=False)
    reviewed = db.Column(db.Boolean, default=False)
    correction_date = db.Column(db.DateTime, nullable=True)
    review_date = db.Column(db.DateTime, nullable=True)

    # --- Link 1: The Student ---
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    student = db.relationship('Users', 
                              back_populates='submissions', 
                              foreign_keys=[student_id])

    # --- Link 2: The Assistant who corrected ---
    corrected_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    corrector = db.relationship('Users', 
                               back_populates='corrected_submissions', 
                               foreign_keys=[corrected_by_id])

    # --- Link 3: The Admin/Super Admin who reviewed ---
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reviewer = db.relationship('Users', 
                               back_populates='reviewed_submissions', 
                               foreign_keys=[reviewed_by_id])

    # Other relationships
    assignment = db.relationship('Assignments', back_populates='submissions')


class Announcements(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False)
    creation_date = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))
    whatsapp_sent = db.Column(db.Boolean, default=False)

    # Legacy single-scope field
    groupid = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=True)

    group = db.relationship('Groups', back_populates='announcements')
    
    # Many-to-many groups
    groups_mm = db.relationship('Groups', secondary=announcement_groups, back_populates='announcements_mm')


# ============================================================
# Sessions / Videos / Views / NextQuiz
# ============================================================
class Sessions(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    creation_date = db.Column(
        db.DateTime,
        default=lambda: datetime.now(gmt_plus_2)
    )

    # Legacy single-scope FK
    groupid = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=True)

    # Added by user
    added_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete="SET NULL"),
        nullable=True
    )

    # Relationships
    group = db.relationship('Groups', back_populates='sessions')

    # Many-to-many groups
    groups_mm = db.relationship('Groups', secondary=session_groups, back_populates='sessions_mm')

    # Videos (1-to-many)
    videos = db.relationship(
        'Videos',
        back_populates='session',
        lazy='dynamic',
        cascade="all, delete-orphan"
    )

    # Link back to uploader
    uploader = db.relationship(
        'Users',
        back_populates='sessions',
        foreign_keys=[added_by],
        passive_deletes=True
    )


class Videos(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    video_url = db.Column(db.Text, nullable=False)
    creation_date = db.Column(
        db.DateTime,
        default=lambda: datetime.now(gmt_plus_2)
    )

    # Belongs to exactly one session
    session_id = db.Column(
        db.Integer,
        db.ForeignKey('sessions.id', ondelete="CASCADE"),
        nullable=False
    )

    session = db.relationship('Sessions', back_populates='videos')

    # Views (1-to-many)
    views = db.relationship(
        'VideoViews',
        back_populates='video',
        lazy='dynamic',
        cascade="all, delete-orphan"
    )


class VideoViews(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey('videos.id', ondelete='CASCADE'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    view_count = db.Column(db.Integer, default=1, nullable=False)
    view_date = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))

    __table_args__ = (
        db.UniqueConstraint('video_id', 'student_id', name='_video_student_uc'),
    )

    video = db.relationship('Videos', back_populates='views', passive_deletes=True)
    student = db.relationship('Users', back_populates='video_views', passive_deletes=True)


class NextQuiz(db.Model):
    __tablename__ = 'next_quiz'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    quiz_date = db.Column(db.DateTime, nullable=True)
    creation_date = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))

    # Legacy single-scope field
    groupid = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=True)

    group = db.relationship('Groups', back_populates='next_quiz')

    # Many-to-many groups
    groups_mm = db.relationship('Groups', secondary=next_quiz_groups, back_populates='next_quiz_mm')


class Attendance_session(db.Model):
    __tablename__ = 'attendance_session'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=True)
    session_date = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))
    points = db.Column(db.Integer, nullable=False, default=0)

    # Legacy single-scope field (kept for backward compatibility during migration)
    groupid = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=True)

    group = db.relationship('Groups', back_populates='attendance_session')

    # Many-to-many groups
    groups_mm = db.relationship('Groups', secondary=attendance_groups, back_populates='attendance_session_mm')

    attendance_student = db.relationship('Attendance_student', back_populates='attendance_session', lazy='dynamic', cascade="all, delete-orphan")


class Attendance_student(db.Model):
    __tablename__ = 'attendance_student'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    attendance_session_id = db.Column(db.Integer, db.ForeignKey('attendance_session.id'), nullable=False)
    attendance_status = db.Column(db.String(200), nullable=False)
    creation_date = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))

    student = db.relationship('Users', back_populates='attendance_student')
    attendance_session = db.relationship('Attendance_session', back_populates='attendance_student')


# ============================================================
# Quizzes / Grades (actual graded quizzes, separate from NextQuiz)
# ============================================================
class Quizzes(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    full_mark = db.Column(db.Integer, nullable=False, default=0)

    # Legacy single-scope field
    groupid = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=True)

    points = db.Column(db.Integer, nullable=False, default=0)

    group = db.relationship('Groups', back_populates='quizzes')

    # Many-to-many groups
    groups_mm = db.relationship('Groups', secondary=quiz_groups, back_populates='quizzes_mm')

    student_grades = db.relationship('QuizGrades', back_populates='quiz', lazy='dynamic', cascade="all, delete-orphan")


class QuizGrades(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # mark is string to allow textual feedback as requested
    mark = db.Column(db.String(50), nullable=True)
    comment = db.Column(db.Text, nullable=True)
    place = db.Column(db.Integer, nullable=False, default=0)

    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quizzes.id'), nullable=False)
    corrector_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    student = db.relationship('Users', back_populates='quiz_grades', foreign_keys=[student_id])
    corrector = db.relationship('Users', back_populates='corrected_quizzes', foreign_keys=[corrector_id])
    quiz = db.relationship('Quizzes', back_populates='student_grades')


# ============================================================
# Materials
# ============================================================
class Materials(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    url = db.Column(db.Text, nullable=False)
    creation_date = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))

    folderid = db.Column(db.Integer, db.ForeignKey('materials_folder.id'), nullable=True)

    folder = db.relationship('Materials_folder', back_populates='materials')


class Materials_folder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    creation_date = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))

    # Legacy single-scope field
    groupid = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=True)
    category = db.Column(db.String(50), nullable=True)

    group = db.relationship('Groups', back_populates='materials_folder')
    
    # Many-to-many groups
    groups_mm = db.relationship('Groups', secondary=folder_groups, back_populates='folders_mm')

    materials = db.relationship('Materials', back_populates='folder', lazy='dynamic')


# ============================================================
# WhatsApp Messages
# ============================================================
class WhatsappMessages(db.Model):
    __tablename__ = 'whatsapp_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    to = db.Column(db.String(20), nullable=False)  # Phone number
    content = db.Column(db.Text, nullable=False)   # Message content
    date_added = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending, sent, failed
    
    # Optional: Add user relationship for tracking
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    user = db.relationship('Users', backref='whatsapp_messages')


# ============================================================
# Assistant logs
# ============================================================
class AssistantLogs(db.Model):
    tablename__ = 'assistant_logs'
    id = db.Column(db.Integer, primary_key=True)
    assistant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.String(120), nullable=False)  # Create, update, delete
    log = db.Column(db.JSON)

    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))

    user = db.relationship('Users', back_populates='assistant_logs')


# ============================================================
# Assignments WhatsApp Notifications
# ============================================================
class Assignments_whatsapp(db.Model):
    __tablename__ = 'assignments_whatsapp'
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignments.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    message_sent = db.Column(db.Boolean, default=False, nullable=False)
    sent_date = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))

    __table_args__ = (
        db.UniqueConstraint('assignment_id', 'user_id', name='_assignment_user_uc'),
    )

    assignment = db.relationship('Assignments', backref='whatsapp_notifications')
    user = db.relationship('Users', backref='assignment_whatsapp_notifications')


# ============================================================
# Upload Status
# ============================================================
class Upload_status(db.Model):
    __tablename__ = 'upload_status'
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignments.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    upload_status = db.Column(db.String(50), nullable=False, default="pending")  # pending, in_progress, completed, failed
    upload_type = db.Column(db.String(50), nullable=False, default="assignment")
    failure_reason = db.Column(db.Text, nullable=True)

    total_size = db.Column(db.BigInteger, default=0, nullable=False)
    bytes_uploaded = db.Column(db.BigInteger, default=0, nullable=False)
    progress_percent = db.Column(db.Float, default=0.0, nullable=False)

    total_chunks = db.Column(db.Integer, default=0, nullable=False)
    current_chunk = db.Column(db.Integer, default=0, nullable=False)
    last_chunk_size = db.Column(db.Integer, default=0, nullable=False)
    last_chunk_date = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(gmt_plus_2))

    assignment = db.relationship('Assignments', backref='upload_status')
    user = db.relationship('Users', backref='upload_status')


# ============================================================
# Zoom Meeting Member Association Object
# ============================================================
class ZoomMeetingMember(db.Model):
    __tablename__ = 'zoom_meeting_members'
    
    # Foreign Keys (Composite Primary Key)
    zoom_meeting_id = db.Column(db.Integer, db.ForeignKey('zoom_meeting.id', ondelete='CASCADE'), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=True, primary_key=False)
    
    # Custom Data for the Relationship (The "Zoom Member" Details)
    zoom_id = db.Column(db.String(120), nullable=False, primary_key=True)
    zoom_display_name = db.Column(db.String(255), nullable=True)
    zoom_email = db.Column(db.String(255), nullable=True)
    
    # Relationships back to the parent tables
    meeting = db.relationship("Zoom_meeting", back_populates="memberships")
    user = db.relationship("Users", back_populates="meeting_memberships")


# ============================================================
# Zoom Meeting Model (Simplified)
# ============================================================
class Zoom_meeting(db.Model):
    __tablename__ = 'zoom_meeting'
    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.String(255), nullable=False, unique=True)
    
    # Foreign key for meeting creator
    creator_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # Many-to-many relationships with groups only
    groups = db.relationship('Groups', secondary=zoom_meeting_groups, backref='zoom_meetings')
    
    # Relationship to the ASSOCIATION OBJECT
    # This stores the ZoomMeetingMember records (the custom data)
    memberships = db.relationship(
        "ZoomMeetingMember", 
        back_populates="meeting", 
        cascade="all, delete-orphan"
    )
    
    # Association Proxy for direct access to the User object
    # meeting_obj.participants will return a list of User objects
    participants = association_proxy("memberships", "user")
    
    # Relationship to creator (MUST define foreign_keys)
    creator = db.relationship('Users', foreign_keys=[creator_id], backref='created_zoom_meetings')
