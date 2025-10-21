# Imports
from flask import Blueprint, redirect, render_template, request, url_for, flash, send_from_directory, abort, jsonify
from .models import (
    Users,   Assignments, AssignmentLateException, Submissions, Announcements, Videos, Sessions,
      Materials, Materials_folder,   NextQuiz, Attendance_student, Attendance_session , Upload_status , Groups, assignment_groups)
from sqlalchemy.sql import exists
from sqlalchemy import or_, false, exists, and_ , not_

from . import db
import os
import json
from flask_login import   current_user
from werkzeug.utils import secure_filename
from datetime import datetime
import mimetypes
import pytz
from sqlalchemy import func
from .website import storage, send_whatsapp_message
import time
from werkzeug.security import check_password_hash, generate_password_hash
student = Blueprint('student', __name__)

GMT_PLUS_2 = pytz.timezone('Africa/Cairo')


#=================================================================
#=================================================================
#Helper Functions
#=================================================================
#=================================================================


def get_all(model, student_id, *, add_status_filter=True, base_query=None):
    """
    Return a Query for ALL rows of `model` visible to the student,
    using only the user's legacy groupid against assignment's groups_mm/groupid.
    
    Logic:
    - User only has legacy groupid (NOT MM groups)
    - Assignment can have groups_mm (list) or legacy groupid
    - Row is visible if:
      1. User's legacy groupid is in assignment's groups_mm
      2. User's legacy groupid matches assignment's legacy groupid
      3. Assignment is global (no groups_mm AND no legacy groupid)
    """
    user = Users.query.get(student_id)
    if user is None:
        return (base_query or model.query).filter(false())

    q = base_query or model.query

    if add_status_filter and hasattr(model, "status"):
        q = q.filter(model.status == "Show")

    # Only check groups dimension
    if hasattr(model, "groups_mm") and hasattr(model, "groupid"):
        user_groupid = getattr(user, "groupid", None)
        
        if user_groupid is None:
            # User has no group: only show globally unspecified rows
            # (no MM groups AND legacy groupid is NULL)
            q = q.filter(
                and_(
                    ~model.groups_mm.any(),
                    model.groupid.is_(None)
                )
            )
        else:
            # User has a legacy groupid: show rows where user's group is targeted OR global
            conditions = []
            
            # 1. User's legacy groupid is in assignment's groups_mm
            conditions.append(model.groups_mm.any(Groups.id == user_groupid))
            
            # 2. User's legacy groupid matches assignment's legacy groupid
            conditions.append(model.groupid == user_groupid)
            
            # 3. Assignment is global: has no MM groups AND legacy groupid is NULL
            conditions.append(
                and_(
                    ~model.groups_mm.any(),
                    model.groupid.is_(None)
                )
            )
            
            q = q.filter(or_(*conditions))

    return q

def have_perms(model, record_id, student_id):
    """
    Boolean: does `student_id` have access to `model.id == record_id`?
    Uses user's legacy groupid against record's groups.
    
    Logic:
    - User only has legacy groupid (NOT MM groups)
    - Record can have groups_mm (list) or legacy groupid
    - Match if:
      1. User's legacy groupid is in record's groups_mm
      2. User's legacy groupid matches record's legacy groupid
      3. Record is global (no groups)
    """
    user = Users.query.get(student_id)
    if user is None:
        return False

    # Get the record
    record = model.query.filter(model.id == record_id).first()
    if not record:
        return False

    # Check status if applicable
    if hasattr(record, "status") and record.status != "Show":
        return False

    # If no group scoping on this model, allow access
    if not hasattr(model, "groups_mm") or not hasattr(model, "groupid"):
        return True

    # Get user's legacy groupid only
    user_groupid = getattr(user, "groupid", None)

    # Get record's groups
    record_group_ids = [g.id for g in record.groups_mm]
    record_groupid = getattr(record, "groupid", None)

    # If user has no group
    if user_groupid is None:
        # User can only see global records (no MM groups AND no legacy groupid)
        return len(record_group_ids) == 0 and record_groupid is None

    # User has a legacy groupid - check for match:
    # 1. User's legacy groupid is in record's groups_mm
    if user_groupid in record_group_ids:
        return True
    
    # 2. User's legacy groupid matches record's legacy groupid
    if record_groupid == user_groupid:
        return True
    
    # 3. Record is global (no groups at all)
    if len(record_group_ids) == 0 and record_groupid is None:
        return True

    return False


def evaluate_late_exception_state(exception, now=None):
    """
    Returns a tuple (is_active, aware_deadline) for a late submission exception.
    `aware_deadline` is timezone-aware in GMT+2 when present.
    """
    if not exception:
        return False, None

    now = now or datetime.now(GMT_PLUS_2)
    aware_deadline = None

    if exception.extended_deadline:
        try:
            aware_deadline = GMT_PLUS_2.localize(exception.extended_deadline)
        except ValueError:
            aware_deadline = exception.extended_deadline.astimezone(GMT_PLUS_2)
        return aware_deadline >= now, aware_deadline

    return True, None


def load_student_late_exceptions(student_id, assignment_ids, now=None):
    """
    Bulk-load late submission exceptions for the given `assignment_ids`.
    Returns a dict keyed by assignment_id with {exception, active, aware_deadline}.
    """
    now = now or datetime.now(GMT_PLUS_2)
    if not assignment_ids:
        return {}

    exceptions = AssignmentLateException.query.filter(
        AssignmentLateException.student_id == student_id,
        AssignmentLateException.assignment_id.in_(assignment_ids)
    ).all()

    result = {}
    for exception in exceptions:
        is_active, aware_deadline = evaluate_late_exception_state(exception, now)
        result[exception.assignment_id] = {
            "exception": exception,
            "active": is_active,
            "aware_deadline": aware_deadline,
        }
    return result


def to_cairo_aware(dt):
    """Ensure `dt` is timezone-aware in GMT+2."""
    if not dt:
        return None
    if dt.tzinfo is None:
        return GMT_PLUS_2.localize(dt)
    return dt.astimezone(GMT_PLUS_2)


def compute_effective_deadline(aware_deadline, exception_info):
    """
    Returns the effective deadline considering a late exception.
    If an exception is active and has an override deadline, it wins.
    If an exception is active without deadline, returns None (no deadline).
    """
    if exception_info and exception_info.get("active"):
        override_deadline = exception_info.get("aware_deadline")
        if override_deadline:
            return override_deadline
        return None
    return aware_deadline


def is_submission_on_time(submission_time, aware_deadline, exception_info):
    """
    Determine if `submission_time` should be treated as on-time with any active exception.
    """
    submission_aware = to_cairo_aware(submission_time)
    effective_deadline = compute_effective_deadline(aware_deadline, exception_info)
    if effective_deadline is None or submission_aware is None:
        return True
    return submission_aware <= effective_deadline



#=================================================================
#Dashboard
#=================================================================

@student.route("/dashboard")
def dashboard():
    if current_user.role in ["admin", "super_admin"]:
        return redirect(url_for("admin.dashboard"))
    else : 
        return redirect(url_for("student.new_home"))

@student.route("/home")
def new_home():
    student = current_user
    if current_user.role != "student":
        return redirect(url_for("admin.dashboard"))

    def get_initials(name):
        if not name:
            return "ST"
        parts = name.strip().split()
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()


    def format_relative_time(dt):
        if not dt:
            return "Never"
        
        now = datetime.now()
        diff = now - dt
        
        if diff.days > 0:
            if diff.days == 1:
                return "1 day ago"
            elif diff.days < 30:
                return f"{diff.days} days ago"
            elif diff.days < 365:
                months = diff.days // 30
                return f"{months} month{'s' if months > 1 else ''} ago"
            else:
                years = diff.days // 365
                return f"{years} year{'s' if years > 1 else ''} ago"
        
        hours = diff.seconds // 3600
        if hours > 0:
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        
        minutes = diff.seconds // 60
        if minutes > 0:
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
        
        return "Just now"


    profile_initials = get_initials(student.name)


    total_videos = 0
    last_video_time = "No videos"
    try:

        latest_video = get_all(Videos, current_user.id).order_by(Videos.creation_date.desc()).first()


        if latest_video and latest_video.creation_date:
            last_video_time = format_relative_time(latest_video.creation_date)
    except Exception:
        total_videos = 0


    try:


        student_assignments = get_all(Assignments, current_user.id).filter(Assignments.type == 'Assignment').order_by(Assignments.deadline_date.asc()).all()


        # Which assignments has the student already submitted?
        submitted_assignment_ids = {
            s.assignment_id
            for s in Submissions.query.with_entities(Submissions.assignment_id)
                                    .filter_by(student_id=current_user.id)
                                    .all()
        }

        # Pending (not yet submitted) assignments
        non_submitted_assignments = [
            a for a in student_assignments
            if a.id not in submitted_assignment_ids
        ]

        # Sort by nearest deadline first; place items without deadlines at the end
        non_submitted_assignments.sort(key=lambda a: a.deadline_date or datetime.max)

        pending_assignments = len(non_submitted_assignments)

        # Limit to the first 5 to display
        non_submitted_assignments = non_submitted_assignments[:5]

    except Exception:
        non_submitted_assignments = []
        pending_assignments = 0


    try:


        student_exams = get_all(Assignments, current_user.id).filter(Assignments.type == 'Exam').order_by(Assignments.deadline_date.asc()).all()


        # Which exams has the student already submitted?
        submitted_exam_ids = {
            s.assignment_id
            for s in Submissions.query.with_entities(Submissions.assignment_id)
                                    .filter_by(student_id=current_user.id)
                                    .all()
        }

        # Pending (not yet submitted) exams
        non_submitted_exams = [
            e for e in student_exams
            if e.id not in submitted_exam_ids
        ]

        # Sort by nearest deadline first; place items without deadlines at the end
        non_submitted_exams.sort(key=lambda e: e.deadline_date or datetime.max)

        pending_exams = len(non_submitted_exams)

        # Limit to the first 5 to display
        non_submitted_exams = non_submitted_exams[:5]

    except Exception:
        non_submitted_exams = []
        pending_exams = 0


    dashboard_announcements = get_all(Announcements, current_user.id).order_by(Announcements.creation_date.desc()).limit(4).all()


    next_quiz = get_all(NextQuiz, current_user.id).order_by(NextQuiz.quiz_date.asc()).first()
    

    # Get user's legacy groupid only (users don't have MM groups)
    user_groupid = getattr(current_user, "groupid", None)
    
    # Build classmates query based on legacy groupid only
    if user_groupid is not None:
        # User has a group: find classmates in the same legacy group
        classmates = Users.query.filter(Users.groupid == user_groupid).order_by(Users.points.desc()).all()
    else:
        # User has no group: no classmates
        classmates = []
    
    place_on_class = 1
    for idx, student in enumerate(classmates, start=1):
        if student.id == current_user.id:
            place_on_class = idx
            break   
    
    if student.student_whatsapp:
        student_whatsapp = current_user.student_whatsapp
    else:
        student_whatsapp = False

    
    
    return render_template(
        "student/new_home.html",
        current_user=student,
        profile_initials=profile_initials,
        total_videos=total_videos,
        last_video_time=last_video_time,
        pending_assignments=pending_assignments,
        non_submitted_assignments=non_submitted_assignments,
        pending_exams=pending_exams,
        non_submitted_exams=non_submitted_exams,
        dashboard_announcements=dashboard_announcements,
        next_quiz=next_quiz,
        place_on_class=place_on_class,
        student_whatsapp=student_whatsapp,
        classmates=classmates,
    )



#=================================================================
#Pending Account
#=================================================================

@student.route("/pending_account")
def pending_account():
    if current_user.role == "student":
        if current_user.code == "nth" or current_user.code == "Nth":
            return render_template("used_pages/account_not_activated.html")
    return redirect(url_for("student.new_home"))



#=================================================================
#Assignments
#=================================================================


@student.route("/assignments")
def assignments():

    base_query = db.session.query(Assignments).outerjoin(
        Submissions,
        (Assignments.id == Submissions.assignment_id) & (Submissions.student_id == current_user.id)
    ).add_columns(Submissions)


    assignment_query = get_all(
        Assignments,
        current_user.id,
        base_query=base_query
    )


    all_assignments_with_submissions = assignment_query.order_by(Assignments.id.desc()).filter(Assignments.type == "Assignment").all()

    current_date = datetime.now(GMT_PLUS_2)
    completed_count = 0
    processed_assignments = []

    assignment_ids = [assignment.id for assignment, _ in all_assignments_with_submissions]
    late_exception_map = load_student_late_exceptions(current_user.id, assignment_ids, current_date)

    for assignment, submission in all_assignments_with_submissions:
        assignment.submission = submission
        assignment.done = submission is not None
        assignment.submitted_late = False 
        assignment.past_deadline = False

        if assignment.done:
            completed_count += 1

        aware_deadline = None
        if assignment.deadline_date:
            try:
                aware_deadline = GMT_PLUS_2.localize(assignment.deadline_date)
            except ValueError:
                aware_deadline = assignment.deadline_date.astimezone(GMT_PLUS_2)
            assignment.past_deadline = aware_deadline < current_date

        exception_info = late_exception_map.get(
            assignment.id,
            {"exception": None, "active": False, "aware_deadline": None}
        )
        assignment.late_exception = exception_info["exception"]
        assignment.late_exception_active = exception_info["active"]
        assignment.late_exception_deadline = exception_info["aware_deadline"]
        assignment.expired_for_student = (
            assignment.past_deadline
            and assignment.close_after_deadline
            and not assignment.late_exception_active
        )

        assignment.effective_deadline_for_student = compute_effective_deadline(
            aware_deadline,
            exception_info
        )

        if assignment.done and submission:
            assignment.submitted_late = not is_submission_on_time(
                submission.upload_time,
                aware_deadline,
                exception_info
            )

        if submission:
            # Only show mark if reviewed by super admin
            if not submission.reviewed:
                submission.mark = "Being reviewed"
            elif not submission.mark:
                submission.mark = "Not marked yet"

        processed_assignments.append(assignment)

    return render_template(
        "student/assignments/assignments.html",
        assignments=processed_assignments,
        completed_count=completed_count,
        total_count=len(processed_assignments)
    )
    
@student.route("/assignments/<int:assignment_id>", methods=["GET", "POST"])
def view_assignment(assignment_id):
    current_date = datetime.now(GMT_PLUS_2)


    assignment = get_all(Assignments, current_user.id).filter(Assignments.id == assignment_id).filter(Assignments.type == "Assignment").first()

    if not assignment:
        flash("Assignment not found", "danger")
        return redirect(url_for("student.assignments"))

    submission = Submissions.query.filter_by(
        assignment_id=assignment_id,
        student_id=current_user.id
    ).first()

    assignment.submitted_late = False


    attachments = []
    if assignment.attachments:
        try:
            attachments = json.loads(assignment.attachments)
        except Exception:
            attachments = []


    aware_deadline = None
    try:
        if assignment.deadline_date:
            aware_deadline = pytz.timezone('Africa/Cairo').localize(assignment.deadline_date)
            assignment.past_deadline = aware_deadline < current_date
        else:
            assignment.past_deadline = False
    except Exception:
        assignment.past_deadline = False

    exception_info = load_student_late_exceptions(
        current_user.id,
        [assignment.id],
        current_date
    ).get(assignment.id, {"exception": None, "active": False, "aware_deadline": None})
    assignment.late_exception = exception_info["exception"]
    assignment.late_exception_active = exception_info["active"]
    assignment.late_exception_deadline = exception_info["aware_deadline"]
    assignment.effective_deadline_for_student = compute_effective_deadline(
        aware_deadline,
        exception_info
    )
    assignment.expired_for_student = (
        assignment.past_deadline
        and assignment.close_after_deadline
        and not assignment.late_exception_active
    )
    if submission:
        assignment.submitted_late = not is_submission_on_time(
            submission.upload_time,
            aware_deadline,
            exception_info
        )


    if submission:
        # Only show mark and corrected PDF if reviewed by super admin
        if not submission.reviewed:
            submission.mark = "Being reviewed"
            submission.show_corrected = False
        else:
            submission.show_corrected = submission.corrected
            if submission.mark is None or submission.mark == "":
                submission.mark = "Not marked yet"
    
    assignment.done = submission is not None

    return render_template(
        "student/assignments/view_assignment.html",
        assignment=assignment,
        attachments=attachments,
        submission=submission,
        current_date=current_date
    )

#New uploaded file for assignments

def cleanup_old_temp_files():
    temp_base_dir = os.path.join("website", "submissions", "uploads")
    current_time = time.time()
    cutoff_time = current_time - 3600  # 1 hour ago
    
    try:
        for root, dirs, files in os.walk(temp_base_dir):
            if "temp" in root:
                for file in files:
                    if file.endswith('.part'):
                        file_path = os.path.join(root, file)
                        if os.path.getmtime(file_path) < cutoff_time:
                            os.remove(file_path)
                            # print(f"DEBUG: Cleaned up old temp file: {file_path}")
    except Exception as e:
        # print(f"DEBUG: Error cleaning up old temp files: {e}") 
        pass

@student.route("/assignments/<int:assignment_id>/upload", methods=["POST"])
def upload_chunk(assignment_id):
    """
    Handles uploading a file in chunks.
    When the last chunk is received, it finalizes the submission.
    """
    # Periodic cleanup of old temp files
    cleanup_old_temp_files()
    
    # Check authentication
    if not current_user.is_authenticated:
        return jsonify({
            "status": "error",
            "error": "Authentication required",
            "action": "redirect_login"
        }), 401
    
    # Get assignment
    assignment = Assignments.query.get_or_404(assignment_id)

    #if assignment is pastdeadline and assignment.close_after_deadline is true return expired 
    current_date = datetime.now(GMT_PLUS_2)

    aware_deadline = None
    try:
        if assignment.deadline_date:
            aware_deadline = pytz.timezone('Africa/Cairo').localize(assignment.deadline_date)
            assignment.past_deadline = aware_deadline < current_date
        else:
            assignment.past_deadline = False
    except Exception:
        assignment.past_deadline = False

    exception_info = load_student_late_exceptions(
        current_user.id,
        [assignment.id],
        current_date
    ).get(assignment.id, {"active": False, "aware_deadline": None})
    can_submit_past_deadline = exception_info.get("active", False)
    extended_deadline = exception_info.get("aware_deadline")

    if assignment.past_deadline and assignment.close_after_deadline and not can_submit_past_deadline:
        return jsonify({
            "status": "error",
            "error": "Assignment expired",
            "action": "expired"
        }), 400



    # Update last seen info
    current_user.last_used_user_agent = request.user_agent.string
    current_user.last_used_ip_address = request.headers.get('CF-Connecting-IP', request.remote_addr)
    db.session.commit()
    
    # Check if a submission already exists
    submission_exists = Submissions.query.filter_by(
        assignment_id=assignment_id,
        student_id=current_user.id
    ).first()
    if submission_exists:
        return jsonify({
            "status": "error",
            "error": "You have already submitted this assignment.",
            "action": "already_submitted"
        }), 400

    # Extract form data
    file_chunk = request.files.get("file_chunk")
    filename_from_form = request.form.get("filename")
    if not filename_from_form:
        return jsonify({
            "status": "error",
            "error": "Missing filename.",
            "action": "restart_upload"
        }), 400

    original_filename = secure_filename(filename_from_form)
    offset = int(float(request.form.get("offset", 0)))
    total_size = int(float(request.form.get("total_size", 0)))
    chunk_number = int(float(request.form.get("chunk_number", 0)))
    total_chunks = int(float(request.form.get("total_chunks", 0)))

    if not file_chunk or not original_filename:
        return jsonify({
            "status": "error",
            "error": "Missing file data.",
            "action": "restart_upload"
        }), 400

    # Prepare paths
    upload_folder = os.path.join("website", "submissions", "uploads", f"student_{current_user.id}", "temp")
    os.makedirs(upload_folder, exist_ok=True)
    
    temp_filename = f"assignment_{assignment.id}_{original_filename}.part"
    temp_file_path = os.path.join(upload_folder, temp_filename)
    
    # Read the chunk
    chunk_data = file_chunk.read()
    chunk_size = len(chunk_data)
    
    # Retrieve or create Upload_status
    upload_status = Upload_status.query.filter_by(
        assignment_id=assignment_id,
        user_id=current_user.id,
        file_name=original_filename
    ).first()
    
    if not upload_status:
        upload_status = Upload_status(
            assignment_id=assignment_id,
            user_id=current_user.id,
            upload_status="pending",
            upload_type="assignment",
            file_name=original_filename,
            total_chunks=total_chunks,
            current_chunk=0,
            last_chunk_size=0,
            total_size=total_size,
            bytes_uploaded=0,
            progress_percent=0.0
        )
        db.session.add(upload_status)
        db.session.commit()
    
    # Check offset integrity
    current_file_size = os.path.getsize(temp_file_path) if os.path.exists(temp_file_path) else 0
    if current_file_size != offset:
        if current_file_size > offset:
            with open(temp_file_path, "r+b") as f:
                f.truncate(offset)
            upload_status.failure_reason = f"Chunk overlap detected; truncated to {offset}"
            db.session.commit()
            return jsonify({
                "status": "warning",
                "message": "Chunk overlap fixed; retry current chunk",
                "action": "retry_chunk",
                "retry_offset": offset
            }), 200
        else:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Missing chunks (expected {offset}, got {current_file_size})"
            db.session.commit()
            return jsonify({
                "status": "error",
                "error": "Missing chunks detected; upload corrupted",
                "action": "restart_upload"
            }), 400

    # Write the chunk
    try:
        with open(temp_file_path, "ab") as f:
            f.seek(offset)
            f.write(chunk_data)
        new_file_size = os.path.getsize(temp_file_path)
        expected_size = offset + chunk_size

        if new_file_size != expected_size:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Write verification failed (expected {expected_size}, got {new_file_size})"
            db.session.commit()
            return jsonify({
                "status": "error",
                "error": "Chunk write verification failed",
                "action": "retry_chunk",
                "retry_offset": offset
            }), 500

        # Update upload progress
        uploaded_size = new_file_size
        progress_percent = round((uploaded_size / total_size) * 100, 2)

        upload_status.current_chunk = chunk_number
        upload_status.total_chunks = total_chunks
        upload_status.bytes_uploaded = uploaded_size
        upload_status.progress_percent = progress_percent
        upload_status.last_chunk_size = chunk_size
        upload_status.last_chunk_date = datetime.now(GMT_PLUS_2)
        upload_status.upload_status = "in_progress"
        upload_status.failure_reason = None
        db.session.commit()

    except IOError as e:
        upload_status.upload_status = "failed"
        upload_status.failure_reason = f"IO Error writing chunk: {str(e)}"
        db.session.commit()
        return jsonify({
            "status": "error",
            "error": f"IO Error writing chunk: {str(e)}",
            "action": "restart_upload"
        }), 500

    # Final chunk
    if offset + chunk_size >= total_size:
        final_temp_size = os.path.getsize(temp_file_path)
        if final_temp_size != total_size:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Final size mismatch (expected {total_size}, got {final_temp_size})"
            db.session.commit()
            os.remove(temp_file_path)
            return jsonify({
                "status": "error",
                "error": "Final size mismatch",
                "action": "restart_upload"
            }), 400

        # Validate file type
        valid_extensions = {"jpg", "jpeg", "png", "pdf", "xlsx", "docx"}
        ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
        if ext not in valid_extensions:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Invalid extension {ext}"
            db.session.commit()
            os.remove(temp_file_path)
            return jsonify({
                "status": "error",
                "error": "Invalid file type",
                "action": "invalid_file_type"
            }), 400

        mime_type, _ = mimetypes.guess_type(original_filename)
        valid_mime_types = {
            "image/jpeg", "image/png", "application/pdf",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        }
        if mime_type not in valid_mime_types:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Invalid MIME type {mime_type}"
            db.session.commit()
            os.remove(temp_file_path)
            return jsonify({
                "status": "error",
                "error": f"Invalid MIME type {mime_type}",
                "action": "invalid_file_type"
            }), 400

        # Move final file
        final_folder = os.path.join("website", "submissions", "uploads", f"student_{current_user.id}")
        os.makedirs(final_folder, exist_ok=True)
        final_filename = f"assignment_{assignment.id}.{ext}"
        final_file_path = os.path.join(final_folder, final_filename)

        try:
            os.rename(temp_file_path, final_file_path)
        except Exception as e:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Rename failed: {str(e)}"
            db.session.commit()
            return jsonify({
                "status": "error",
                "error": "Failed to finalize upload",
                "action": "restart_upload"
            }), 500

        # Clean up temp file immediately after successful rename
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as e:
                pass

        # Upload to external storage
        try:
            with open(final_file_path, "rb") as data:
                storage.upload_file(data, f"submissions/uploads/student_{current_user.id}", final_filename)
        except Exception as e:
            try:
                os.remove(final_file_path)
            except:
                pass
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Failed to upload to external storage: {str(e)}"
            db.session.commit()
            return jsonify({
                "status": "error",
                "error": "Failed to upload to storage",
                "action": "restart_upload",
                "details": str(e)
            }), 500




        current_date = datetime.now(GMT_PLUS_2)
        cairo_tz = pytz.timezone('Africa/Cairo')
        aware_local_time = datetime.now(cairo_tz)
        naive_local_time = aware_local_time.replace(tzinfo=None)
        submission_on_time = is_submission_on_time(
            naive_local_time,
            aware_deadline,
            exception_info
        )

        # Award points (respecting late exceptions)
        if assignment.points:
            if submission_on_time:
                current_user.points = (current_user.points or 0) + assignment.points
            else:
                current_user.points = (current_user.points or 0) + (assignment.points / 2)

        # Upload success
        upload_status.upload_status = "completed"
        upload_status.bytes_uploaded = total_size
        upload_status.progress_percent = 100.0
        upload_status.last_chunk_date = datetime.now(GMT_PLUS_2)
        db.session.commit()

        # Record submission
        new_submission = Submissions(
            assignment_id=assignment_id,
            student_id=current_user.id,
            student=current_user,
            file_url=final_filename,
            upload_time=naive_local_time
        )
        db.session.add(new_submission)
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            # Clean up uploaded file since DB failed
            try:
                os.remove(final_file_path)
            except:
                pass
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Database error - submission not saved: {str(e)}"
            upload_status.last_chunk_date = datetime.now(GMT_PLUS_2)
            db.session.commit()
            return jsonify({
                "status": "error",
                "error": "Database error - submission not saved",
                "action": "restart_upload",
                "details": str(e)
            }), 500

        # Send notifications 
        try:
            is_on_time = submission_on_time
            
            if is_on_time:
                # On-time submission messages
                if assignment.student_whatsapp and current_user.phone_number:
                    send_whatsapp_message(
                        current_user.phone_number,
                        f"Hi {current_user.name} 👋,\n\n"
                        f"*{assignment.title}*\n"
                        "We have received your homework submission ✅\n\n"
                        "Thank you for your dedication! ☺"
                    )
                
                if assignment.parent_whatsapp and current_user.parent_phone_number:
                    send_whatsapp_message(
                        current_user.parent_phone_number,
                        f"Dear Parent,\n\n"
                        f"We wanted to inform you that we have received your child's homework submission for *{assignment.title}* ✅\n\n"
                        "Thank you for your continuous support and encouragement 🙏☺"
                    )
            else:
                # Late submission messages
                submission_time = current_date.strftime('%d/%m/%Y at %H:%M')
                
                if assignment.student_whatsapp and current_user.phone_number:
                    send_whatsapp_message(
                        current_user.phone_number,
                        f"Hi {current_user.name} 👋,\n\n"
                        f"Your assignment *{assignment.title}* has been submitted successfully ✅\n\n"
                        f"⚠️ Note: This is a late submission (submitted on {submission_time})\n"
                        "Points awarded will be reduced accordingly."
                    )
                
                if assignment.parent_whatsapp and current_user.parent_phone_number:
                    send_whatsapp_message(
                        current_user.parent_phone_number,
                        f"Dear Parent,\n\n"
                        f"Your child {current_user.name} has submitted their assignment *{assignment.title}* ✅\n\n"
                        f"⚠️ Note: This is a late submission (submitted on {submission_time})"
                    )

        except Exception as e:
            # Don't fail the submission if notifications fail
            pass

        return jsonify({
            "status": "success",
            "message": "Upload complete",
            "action": "upload_complete",
            "progress": 100.0,
            "submission_id": new_submission.id,
            "upload_time": naive_local_time.strftime('%Y-%m-%d %H:%M:%S'),
            "points_awarded": assignment.points if submission_on_time else (assignment.points / 2 if assignment.points else 0)
        }), 200

    # Not the last chunk
    return jsonify({
        "status": "success",
        "message": "Chunk received",
        "action": "continue_upload",
        "next_offset": offset + chunk_size,
        "progress": progress_percent,
        "chunks_completed": chunk_number,
        "total_chunks": total_chunks
    }), 200


#View the admin uploaded file for assignments (attachments)
@student.route("/assignments/uploads/<filename>")
def assignment_media(filename):
    filename = secure_filename(filename)
    file_path = os.path.join("website/assignments/uploads", filename)
    if os.path.isfile(file_path):
        return send_from_directory("assignments/uploads", filename)
    else :
        try :
            storage.download_file(folder="assignments/uploads", file_name=filename, local_path=file_path)
        except Exception:
            return abort(404)
        return send_from_directory("assignments/uploads", filename)


#View the student uploaded file for assignments (submission) (Important)
@student.route("/assignments/<int:assignment_id>/upload")
def student_submission_media(assignment_id):
    submission = Submissions.query.filter_by(assignment_id=assignment_id, student_id=current_user.id).first()
    if not submission:
        return abort(404)


    folder = f"student_{submission.student_id}"
    filename = submission.file_url
    file_path = os.path.join("website/submissions/uploads", folder, filename)

    if os.path.isfile(file_path):
        return send_from_directory(f"submissions/uploads/{folder}", filename)
    else :
        try :
            storage.download_file(f"submissions/uploads/student_{submission.student_id}", filename, file_path)
        except Exception as e:
            return abort(404)
        return send_from_directory(f"submissions/uploads/{folder}", filename)

#Delete the student uploaded file for assignments (submission)
@student.route("/assignments/<int:assignment_id>/delete_submission", methods=["POST"])
def delete_submission(assignment_id):
    # Only allow deletion if the assignment status is "Show"
    assignment = Assignments.query.filter_by(id=assignment_id, status="Show").first()

    if not assignment:
        flash("Assignment not found", "danger")
        return redirect(url_for("student.assignments", assignment_id=assignment_id))

    #if assignment is pastdeadline and assignment.close_after_deadline is true return expired 
    current_date = datetime.now(GMT_PLUS_2)





    if assignment.type == "Exam":
        flash("You can't delete a submission for an exam.", "danger")
        return redirect(url_for("student.view_exam", exam_id=assignment_id))


    submission = Submissions.query.filter_by(assignment_id=assignment_id, student_id=current_user.id).first()
    if not submission:
        flash("No submission found to delete.", "danger")
        return redirect(url_for("student.view_assignment", assignment_id=assignment_id))

    try:

        aware_deadline = None
        try:
            if assignment.deadline_date:
                aware_deadline = pytz.timezone('Africa/Cairo').localize(assignment.deadline_date)
                assignment.past_deadline = aware_deadline < current_date
            else:
                assignment.past_deadline = False
        except Exception:
            assignment.past_deadline = False

        exception_info = load_student_late_exceptions(
            current_user.id,
            [assignment.id],
            current_date
        ).get(assignment.id, {"active": False, "aware_deadline": None})
        if assignment.past_deadline and assignment.close_after_deadline and not exception_info.get("active", False):
          flash("Assignment expired you can't delete your submission", "danger")
          return redirect(url_for("student.view_assignment", assignment_id=assignment_id))









        submission_on_time = is_submission_on_time(
            submission.upload_time,
            aware_deadline,
            exception_info
        )

        if assignment.points:
            if submission_on_time:
                current_user.points = (current_user.points or 0) - assignment.points
            else:
                current_user.points = (current_user.points or 0) - (assignment.points / 2)

        local_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url)
        try :
            filename2 = submission.file_url.replace(".pdf", "_annotated.pdf")
            local_path2 = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", filename2)
            if os.path.exists(local_path2):
                os.remove(local_path2)
            storage.delete_file(f"submissions/uploads/student_{submission.student_id}", filename2)

        except Exception:
            pass
        if os.path.exists(local_path):
            os.remove(local_path)
        try:
            storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url)
        except Exception:
            return "error deleting file from storage"
        db.session.delete(submission)
        db.session.commit()
        flash("Submission deleted successfully!", "success")
    except Exception as e:
        flash(f"An error occurred while deleting the submission: {str(e)}", "danger")



    return redirect(url_for("student.view_assignment", assignment_id=assignment_id))



#View annotated pdf for student submission (Edited by admin)
@student.route("/correction/<int:submission_id>")
def corrected_pdf(submission_id):

    submission = Submissions.query.get_or_404(submission_id)

    if submission.student_id != current_user.id:
        flash("You do not have access to this submission!", "danger")
        return redirect(url_for("student.view_assignment", assignment_id=submission.assignment_id))

    folder = f"student_{submission.student_id}"
    filename = submission.file_url # This return file.pdf

    filename = filename.replace(".pdf", "_annotated.pdf")



    return send_from_directory(os.path.join("submissions/uploads", folder), filename)




#=================================================================
#Attendance
#=================================================================

@student.route("/attendance")
def attendance():
    # Get the student's attendance records where they have either present or absent status
    student_attendance_records = (
        db.session.query(Attendance_student, Attendance_session)
        .join(Attendance_session, Attendance_student.attendance_session_id == Attendance_session.id)
        .filter(Attendance_student.student_id == current_user.id)
        .order_by(Attendance_session.session_date.desc())
        .all()
    )
    
    # Prepare the attendance data for the template
    attendance_data = []
    for attendance_record, session in student_attendance_records:
        attendance_data.append({
            'session': session,
            'status': attendance_record.attendance_status,
            'date_recorded': attendance_record.creation_date
        })
    
    return render_template("student/attendance/attendance.html", attendance_data=attendance_data)


#=================================================================
#Exams
#=================================================================
@student.route("/exams")
def exams():

    base_query = db.session.query(Assignments).outerjoin(
        Submissions,
        (Assignments.id == Submissions.assignment_id) & (Submissions.student_id == current_user.id)
    ).add_columns(Submissions)

    exam_query = get_all(
        Assignments,
        current_user.id,
        base_query=base_query
    )


    all_exams_with_submissions = exam_query.order_by(Assignments.id.desc()).filter(Assignments.type == "Exam").all()


    current_date = datetime.now(GMT_PLUS_2)
    completed_count = 0
    processed_exams = []

    exam_ids = [exam.id for exam, _ in all_exams_with_submissions]
    late_exception_map = load_student_late_exceptions(current_user.id, exam_ids, current_date)


    for exam, submission in all_exams_with_submissions:
        exam.submission = submission
        exam.done = submission is not None
        exam.submitted_late = False  #
        exam.past_deadline = False

        if exam.done:
            completed_count += 1

        aware_deadline = None
        if exam.deadline_date:
            try:
                aware_deadline = GMT_PLUS_2.localize(exam.deadline_date)
            except ValueError:
                aware_deadline = exam.deadline_date.astimezone(GMT_PLUS_2)
            exam.past_deadline = aware_deadline < current_date


        exam.effective_deadline_for_student = compute_effective_deadline(
            aware_deadline,
            exception_info
        )

        if exam.done and submission:
            exam.submitted_late = not is_submission_on_time(
                submission.upload_time,
                aware_deadline,
                exception_info
            )

        if submission:
            # Only show mark if reviewed by super admin
            if not submission.reviewed:
                submission.mark = "Being reviewed"
            elif not submission.mark:
                submission.mark = "Not marked yet"

        exception_info = late_exception_map.get(
            exam.id,
            {"exception": None, "active": False, "aware_deadline": None}
        )
        exam.late_exception = exception_info["exception"]
        exam.late_exception_active = exception_info["active"]
        exam.late_exception_deadline = exception_info["aware_deadline"]
        exam.expired_for_student = (
            exam.past_deadline
            and exam.close_after_deadline
            and not exam.late_exception_active
        )

        processed_exams.append(exam)

    return render_template(
        "student/exams/exams.html",
        exams=processed_exams,
        completed_count=completed_count,
        total_count=len(processed_exams)
    )


@student.route("/exams/<int:exam_id>", methods=["GET", "POST"])
def view_exam(exam_id):
    current_date = datetime.now(GMT_PLUS_2)

    exam = get_all(Assignments, current_user.id).filter(Assignments.id == exam_id).filter(Assignments.type == "Exam").first()

    if not exam:
        flash("Exam not found", "danger")
        return redirect(url_for("student.exams"))

    submission = Submissions.query.filter_by(
        assignment_id=exam_id,
        student_id=current_user.id
    ).first()

    exam.submitted_late = False

    attachments = []
    if exam.attachments:
        try:
            attachments = json.loads(exam.attachments)
        except Exception:
            attachments = []

    try:
        if exam.deadline_date:
            deadline_date = pytz.timezone('Africa/Cairo').localize(exam.deadline_date)
            exam.past_deadline = deadline_date < current_date
        else:
            exam.past_deadline = False
    except Exception:
        exam.past_deadline = False

    exception_info = load_student_late_exceptions(
        current_user.id,
        [exam.id],
        current_date
    ).get(exam.id, {"exception": None, "active": False, "aware_deadline": None})
    exam.late_exception = exception_info["exception"]
    exam.late_exception_active = exception_info["active"]
    exam.late_exception_deadline = exception_info["aware_deadline"]
    exam.effective_deadline_for_student = compute_effective_deadline(
        deadline_date if 'deadline_date' in locals() else None,
        exception_info
    )
    exam.expired_for_student = (
        exam.past_deadline
        and exam.close_after_deadline
        and not exam.late_exception_active
    )

    if submission:
        # Only show mark and corrected PDF if reviewed by super admin
        if not submission.reviewed:
            submission.mark = "Being reviewed"
            submission.show_corrected = False
        else:
            submission.show_corrected = submission.corrected
            if submission.mark is None or submission.mark == "":
                submission.mark = "Not marked yet"
        exam.submitted_late = not is_submission_on_time(
            submission.upload_time,
            deadline_date if 'deadline_date' in locals() else None,
            exception_info
        )
    
    exam.done = submission is not None

    return render_template(
        "student/exams/view_exam.html",
        assignment=exam,
        attachments=attachments,
        submission=submission
    )


#New uploaded file for exams

@student.route("/exams/<int:exam_id>/upload", methods=["POST"])
def upload_exam_chunk(exam_id):
    """
    Handles uploading an exam file in chunks.
    When the last chunk is received, it finalizes the submission.
    """
    # Periodic cleanup of old temp files
    cleanup_old_temp_files()
    
    # Check authentication
    if not current_user.is_authenticated:
        return jsonify({
            "status": "error",
            "error": "Authentication required",
            "action": "redirect_login"
        }), 401
    
    # Get exam
    exam = Assignments.query.filter_by(id=exam_id, type="Exam").first()
    if not exam:
        return jsonify({
            "status": "error",
            "error": "Exam not found",
            "action": "redirect"
        }), 404

    # Check if exam is past deadline and closed
    current_date = datetime.now(GMT_PLUS_2)

    aware_deadline = None
    try:
        if exam.deadline_date:
            aware_deadline = pytz.timezone('Africa/Cairo').localize(exam.deadline_date)
            exam.past_deadline = aware_deadline < current_date
        else:
            exam.past_deadline = False
    except Exception:
        exam.past_deadline = False

    exception_info = load_student_late_exceptions(
        current_user.id,
        [exam.id],
        current_date
    ).get(exam.id, {"active": False, "aware_deadline": None})
    can_submit_past_deadline = exception_info.get("active", False)
    extended_deadline = exception_info.get("aware_deadline")

    if exam.past_deadline and exam.close_after_deadline and not can_submit_past_deadline:
        return jsonify({
            "status": "error",
            "error": "Exam expired",
            "action": "expired"
        }), 400

    # Update last seen info
    current_user.last_used_user_agent = request.user_agent.string
    current_user.last_used_ip_address = request.headers.get('CF-Connecting-IP', request.remote_addr)
    db.session.commit()
    
    # Check if a submission already exists
    submission_exists = Submissions.query.filter_by(
        assignment_id=exam_id,
        student_id=current_user.id
    ).first()
    if submission_exists:
        return jsonify({
            "status": "error",
            "error": "You have already submitted this exam.",
            "action": "already_submitted"
        }), 400

    # Extract form data
    file_chunk = request.files.get("file_chunk")
    filename_from_form = request.form.get("filename")
    if not filename_from_form:
        return jsonify({
            "status": "error",
            "error": "Missing filename.",
            "action": "restart_upload"
        }), 400

    original_filename = secure_filename(filename_from_form)
    offset = int(float(request.form.get("offset", 0)))
    total_size = int(float(request.form.get("total_size", 0)))
    chunk_number = int(float(request.form.get("chunk_number", 0)))
    total_chunks = int(float(request.form.get("total_chunks", 0)))

    if not file_chunk or not original_filename:
        return jsonify({
            "status": "error",
            "error": "Missing file data.",
            "action": "restart_upload"
        }), 400

    # Prepare paths
    upload_folder = os.path.join("website", "submissions", "uploads", f"student_{current_user.id}", "temp")
    os.makedirs(upload_folder, exist_ok=True)
    
    temp_filename = f"exam_{exam.id}_{original_filename}.part"
    temp_file_path = os.path.join(upload_folder, temp_filename)
    
    # Read the chunk
    chunk_data = file_chunk.read()
    chunk_size = len(chunk_data)
    
    # Retrieve or create Upload_status
    upload_status = Upload_status.query.filter_by(
        assignment_id=exam_id,
        user_id=current_user.id,
        file_name=original_filename
    ).first()
    
    if not upload_status:
        upload_status = Upload_status(
            assignment_id=exam_id,
            user_id=current_user.id,
            upload_status="pending",
            upload_type="exam",
            file_name=original_filename,
            total_chunks=total_chunks,
            current_chunk=0,
            last_chunk_size=0,
            total_size=total_size,
            bytes_uploaded=0,
            progress_percent=0.0
        )
        db.session.add(upload_status)
        db.session.commit()
    
    # Check offset integrity
    current_file_size = os.path.getsize(temp_file_path) if os.path.exists(temp_file_path) else 0
    if current_file_size != offset:
        if current_file_size > offset:
            with open(temp_file_path, "r+b") as f:
                f.truncate(offset)
            upload_status.failure_reason = f"Chunk overlap detected; truncated to {offset}"
            db.session.commit()
            return jsonify({
                "status": "warning",
                "message": "Chunk overlap fixed; retry current chunk",
                "action": "retry_chunk",
                "retry_offset": offset
            }), 200
        else:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Missing chunks (expected {offset}, got {current_file_size})"
            db.session.commit()
            return jsonify({
                "status": "error",
                "error": "Missing chunks detected; upload corrupted",
                "action": "restart_upload"
            }), 400

    # Write the chunk
    try:
        with open(temp_file_path, "ab") as f:
            f.seek(offset)
            f.write(chunk_data)
        new_file_size = os.path.getsize(temp_file_path)
        expected_size = offset + chunk_size

        if new_file_size != expected_size:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Write verification failed (expected {expected_size}, got {new_file_size})"
            db.session.commit()
            return jsonify({
                "status": "error",
                "error": "Chunk write verification failed",
                "action": "retry_chunk",
                "retry_offset": offset
            }), 500

        # Update upload progress
        uploaded_size = new_file_size
        progress_percent = round((uploaded_size / total_size) * 100, 2)

        upload_status.current_chunk = chunk_number
        upload_status.total_chunks = total_chunks
        upload_status.bytes_uploaded = uploaded_size
        upload_status.progress_percent = progress_percent
        upload_status.last_chunk_size = chunk_size
        upload_status.last_chunk_date = datetime.now(GMT_PLUS_2)
        upload_status.upload_status = "in_progress"
        upload_status.failure_reason = None
        db.session.commit()

    except IOError as e:
        upload_status.upload_status = "failed"
        upload_status.failure_reason = f"IO Error writing chunk: {str(e)}"
        db.session.commit()
        return jsonify({
            "status": "error",
            "error": f"IO Error writing chunk: {str(e)}",
            "action": "restart_upload"
        }), 500

    # Final chunk
    if offset + chunk_size >= total_size:
        final_temp_size = os.path.getsize(temp_file_path)
        if final_temp_size != total_size:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Final size mismatch (expected {total_size}, got {final_temp_size})"
            db.session.commit()
            os.remove(temp_file_path)
            return jsonify({
                "status": "error",
                "error": "Final size mismatch",
                "action": "restart_upload"
            }), 400

        # Validate file type
        valid_extensions = {"jpg", "jpeg", "png", "pdf", "xlsx", "docx"}
        ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
        if ext not in valid_extensions:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Invalid extension {ext}"
            db.session.commit()
            os.remove(temp_file_path)
            return jsonify({
                "status": "error",
                "error": "Invalid file type",
                "action": "invalid_file_type"
            }), 400

        mime_type, _ = mimetypes.guess_type(original_filename)
        valid_mime_types = {
            "image/jpeg", "image/png", "application/pdf",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        }
        if mime_type not in valid_mime_types:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Invalid MIME type {mime_type}"
            db.session.commit()
            os.remove(temp_file_path)
            return jsonify({
                "status": "error",
                "error": f"Invalid MIME type {mime_type}",
                "action": "invalid_file_type"
            }), 400

        # Move final file
        final_folder = os.path.join("website", "submissions", "uploads", f"student_{current_user.id}")
        os.makedirs(final_folder, exist_ok=True)
        final_filename = f"exam_{exam.id}.{ext}"
        final_file_path = os.path.join(final_folder, final_filename)

        # Remove existing file if it exists
        if os.path.exists(final_file_path):
            try:
                os.remove(final_file_path)
            except Exception as e:
                pass

        try:
            os.rename(temp_file_path, final_file_path)
        except Exception as e:
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Rename failed: {str(e)}"
            db.session.commit()
            return jsonify({
                "status": "error",
                "error": "Failed to finalize upload",
                "action": "restart_upload"
            }), 500

        # Clean up temp file immediately after successful rename
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as e:
                pass

        # Upload to external storage
        try:
            with open(final_file_path, "rb") as data:
                storage.upload_file(data, f"submissions/uploads/student_{current_user.id}", final_filename)
        except Exception as e:
            try:
                os.remove(final_file_path)
            except:
                pass
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Failed to upload to external storage: {str(e)}"
            db.session.commit()
            return jsonify({
                "status": "error",
                "error": "Failed to upload to storage",
                "action": "restart_upload",
                "details": str(e)
            }), 500

        current_date = datetime.now(GMT_PLUS_2)
        cairo_tz = pytz.timezone('Africa/Cairo')
        aware_local_time = datetime.now(cairo_tz)
        naive_local_time = aware_local_time.replace(tzinfo=None)
        submission_on_time = is_submission_on_time(
            naive_local_time,
            aware_deadline,
            exception_info
        )

        # Award points
        if exam.points:
            if submission_on_time:
                current_user.points = (current_user.points or 0) + exam.points
            else:
                current_user.points = (current_user.points or 0) + (exam.points / 2)

        # Upload success
        upload_status.upload_status = "completed"
        upload_status.bytes_uploaded = total_size
        upload_status.progress_percent = 100.0
        upload_status.last_chunk_date = datetime.now(GMT_PLUS_2)
        db.session.commit()

        # Record submission
        new_submission = Submissions(
            assignment_id=exam_id,
            student_id=current_user.id,
            student=current_user,
            file_url=final_filename,
            upload_time=naive_local_time
        )
        db.session.add(new_submission)
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            # Clean up uploaded file since DB failed
            try:
                os.remove(final_file_path)
            except:
                pass
            upload_status.upload_status = "failed"
            upload_status.failure_reason = f"Database error - submission not saved: {str(e)}"
            upload_status.last_chunk_date = datetime.now(GMT_PLUS_2)
            db.session.commit()
            return jsonify({
                "status": "error",
                "error": "Database error - submission not saved",
                "action": "restart_upload",
                "details": str(e)
            }), 500

        # Send notifications 
        try:
            is_on_time = submission_on_time
            
            if is_on_time:
                # On-time submission messages
                if exam.student_whatsapp and current_user.phone_number:
                    send_whatsapp_message(
                        current_user.phone_number,
                        f"Hi {current_user.name} 👋,\n\n"
                        f"*{exam.title}*\n"
                        "We have received your exam submission ✅\n\n"
                        "Thank you for your dedication! ☺"
                    )
                
                if exam.parent_whatsapp and current_user.parent_phone_number:
                    send_whatsapp_message(
                        current_user.parent_phone_number,
                        f"Dear Parent,\n\n"
                        f"We wanted to inform you that we have received your child's exam submission for *{exam.title}* ✅\n\n"
                        "Thank you for your continuous support and encouragement 🙏☺"
                    )
            else:
                # Late submission messages
                submission_time = current_date.strftime('%d/%m/%Y at %H:%M')
                
                if exam.student_whatsapp and current_user.phone_number:
                    send_whatsapp_message(
                        current_user.phone_number,
                        f"Hi {current_user.name} 👋,\n\n"
                        f"Your exam *{exam.title}* has been submitted successfully ✅\n\n"
                        f"⚠️ Note: This is a late submission (submitted on {submission_time})\n"
                        "Points awarded will be reduced accordingly."
                    )
                
                if exam.parent_whatsapp and current_user.parent_phone_number:
                    send_whatsapp_message(
                        current_user.parent_phone_number,
                        f"Dear Parent,\n\n"
                        f"Your child {current_user.name} has submitted their exam *{exam.title}* ✅\n\n"
                        f"⚠️ Note: This is a late submission (submitted on {submission_time})"
                    )

        except Exception as e:
            # Don't fail the submission if notifications fail
            pass

        return jsonify({
            "status": "success",
            "message": "Upload complete",
            "action": "upload_complete",
            "progress": 100.0,
            "submission_id": new_submission.id,
            "upload_time": naive_local_time.strftime('%Y-%m-%d %H:%M:%S'),
            "points_awarded": exam.points if submission_on_time else (exam.points / 2 if exam.points else 0)
        }), 200

    # Not the last chunk
    return jsonify({
        "status": "success",
        "message": "Chunk received",
        "action": "continue_upload",
        "next_offset": offset + chunk_size,
        "progress": progress_percent,
        "chunks_completed": chunk_number,
        "total_chunks": total_chunks
    }), 200



# View the admin uploaded file for exams
@student.route("/exams/uploads/<filename>")
def exam_media(filename):
    filename = secure_filename(filename)
    file_path = os.path.join("website/assignments/uploads", filename)
    if os.path.isfile(file_path):
        return send_from_directory("assignments/uploads", filename)
    else:
        try:
            storage.download_file(folder="assignments/uploads", file_name=filename, local_path=file_path)
        except Exception:
            return abort(404)
        return send_from_directory("assignments/uploads", filename)

#View the student uploaded file for assignments (submission) (Important)
@student.route("/exams/<int:exam_id>/upload")
def student_submission_media_exam(exam_id):
    submission = Submissions.query.filter_by(assignment_id=exam_id, student_id=current_user.id).first()
    if not submission:
        return abort(404)

    assignment = Assignments.query.filter_by(id=exam_id, type="Exam").first()
    if not assignment.type == "Exam":
        return abort(404)

    folder = f"student_{submission.student_id}"
    filename = submission.file_url
    file_path = os.path.join("website/submissions/uploads", folder, filename)

    if os.path.isfile(file_path):
        return send_from_directory(f"submissions/uploads/{folder}", filename)
    else :
        try :
            storage.download_file(f"submissions/uploads/student_{submission.student_id}", filename, file_path)
        except Exception as e:
            return abort(404)
        return send_from_directory(f"submissions/uploads/{folder}", filename)

#Delete the student uploaded file for assignments (submission)
@student.route("/exams/<int:exam_id>/delete_submission", methods=["POST"])
def delete_submission_exam(exam_id):
    # Only allow deletion if the assignment status is "Show"
    assignment = Assignments.query.filter_by(id=exam_id, status="Show", type="Exam").first()
    if not assignment:
        flash("Assignment not found", "danger")
        return redirect(url_for("student.exams", exam_id=exam_id))

    if assignment.type != "Exam":
        flash("You can't delete a submission", "danger")
        return redirect(url_for("student.view_exam", exam_id=exam_id))


    submission = Submissions.query.filter_by(assignment_id=exam_id, student_id=current_user.id).first()
    if not submission:
        flash("No submission found to delete.", "danger")
        return redirect(url_for("student.view_exam", exam_id=exam_id))


    assignment = Assignments.query.filter_by(id=exam_id, type="Exam").first()
    if not assignment.type == "Exam":
        return abort(404)       

        aware_deadline = None
    try:
        current_date = datetime.now(GMT_PLUS_2)
        aware_deadline = None
        try:
            if assignment.deadline_date:
                aware_deadline = pytz.timezone('Africa/Cairo').localize(assignment.deadline_date)
                assignment.past_deadline = aware_deadline < current_date
            else:
                assignment.past_deadline = False
        except Exception:
            assignment.past_deadline = False

        exception_info = load_student_late_exceptions(
            current_user.id,
            [assignment.id],
            current_date
        ).get(assignment.id, {"active": False, "aware_deadline": None})
        if assignment.past_deadline and assignment.close_after_deadline and not exception_info.get("active", False):
            flash("Exam expired you can't delete your submission", "danger")
            return redirect(url_for("student.view_exam", exam_id=exam_id))

        submission_on_time = is_submission_on_time(
            submission.upload_time,
            aware_deadline,
            exception_info
        )

        if assignment.points:
            if submission_on_time:
                current_user.points = (current_user.points or 0) - assignment.points
            else:
                current_user.points = (current_user.points or 0) - (assignment.points / 2)

        local_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url)
        try :
            filename2 = submission.file_url.replace(".pdf", "_annotated.pdf")
            local_path2 = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", filename2)
            if os.path.exists(local_path2):
                os.remove(local_path2)
            storage.delete_file(f"submissions/uploads/student_{submission.student_id}", filename2)

        except Exception:
            pass
        if os.path.exists(local_path):
            os.remove(local_path)
        try:
            storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url)
        except Exception:
            return "error deleting file from storage"
        db.session.delete(submission)
        db.session.commit()
        flash("Submission deleted successfully!", "success")
    except Exception as e:
        flash(f"An error occurred while deleting the submission: {str(e)}", "danger")



    return redirect(url_for("student.view_exam", exam_id=exam_id))



#View annotated pdf for student submission (Edited by admin)
@student.route("/correction/exam/<int:submission_id>")
def corrected_pdf_exam(submission_id):

    submission = Submissions.query.get_or_404(submission_id)

    assignment = Assignments.query.filter_by(id=submission.assignment_id, type="Exam").first()



    if not assignment.type == "Exam":
        return abort(404)
    

    
    if submission.student_id != current_user.id:
        flash("You do not have access to this submission!", "danger")
        return redirect(url_for("student.view_exam", exam_id=submission.assignment_id))

    folder = f"student_{submission.student_id}"
    filename = submission.file_url # This return file.pdf

    filename = filename.replace(".pdf", "_annotated.pdf")



    return send_from_directory(os.path.join("submissions/uploads", folder), filename)




#=================================================================  
# STUDENT VIEW: LIST SESSIONS AND SEE VIDEOS INSIDE
#=================================================================
@student.route("/sessions", methods=["GET"])
def student_sessions():
    sessions = get_all(Sessions, current_user.id).order_by(Sessions.creation_date.desc()).all()
    return render_template("student/sessions/sessions.html", sessions=sessions)


@student.route("/sessions/<int:session_id>", methods=["GET"])
def student_session(session_id):
    session = get_all(Sessions, current_user.id).filter(Sessions.id == session_id).first()
    if not session:
        flash("Session not found", "danger")
        return redirect(url_for("student.student_sessions"))
    return render_template("student/sessions/session.html", session=session)

#=================================================================
#Materials
#=================================================================
@student.route("/folders", methods=["GET"])
def view_folder():


    folders = get_all(Materials_folder, current_user.id).order_by(Materials_folder.id.asc()).all()

    selected_category = request.args.get("category")
    selected_folder_id = request.args.get("folder_id", type=int)

    folder_ids = [f.id for f in folders]
    materials_counts = {}
    if folder_ids:
        counts = (
            db.session.query(Materials.folderid, func.count(Materials.id))
            .filter(Materials.folderid.in_(folder_ids))
            .group_by(Materials.folderid)
            .all()
        )
        materials_counts = {fid: cnt for fid, cnt in counts}

    selected_folder = None
    files = None
    if selected_folder_id:
        selected_folder = next((f for f in folders if f.id == selected_folder_id), None)
        if selected_folder:
            files = (
                Materials.query
                .filter_by(folderid=selected_folder.id)
                .order_by(Materials.id.asc())   
                .all()
            )

    return render_template(
        "student/materials/folder.html",
        folders=folders,
        selected_category=selected_category,
        selected_folder=selected_folder,
        files=files,
        materials_counts=materials_counts
    )


@student.route("/material/uploads/<int:material_id>")
def material_media(material_id):
    folders = get_all(Materials_folder, current_user.id).order_by(Materials_folder.id.asc()).all()
    accessible_folder_ids = {folder.id for folder in folders}

    # Now, get the material and check if its folder is accessible
    material = get_all(Materials, current_user.id).filter(Materials.id == material_id).first()
    if not material:
        flash("Material not found", "danger")
        return redirect(url_for("student.view_folder"))
    if not material.folderid or material.folderid not in accessible_folder_ids and current_user.role != "admin" and current_user.role != "super_admin": 
        flash("You do not have access to this file!", "danger")
        return redirect(url_for("student.view_folder"))

    filename = secure_filename(material.url)
    file_path = os.path.join("website/material/uploads", filename)

    if os.path.isfile(file_path):
        return send_from_directory("material/uploads", filename)
    else :
        try :
            storage.download_file(folder="material/uploads", file_name=filename, local_path=file_path)
        except Exception:
            flash("File not found", 'warning')
            return redirect(url_for("student.view_folder"))
        return send_from_directory("material/uploads", filename)

#=================================================================
#My account
#=================================================================
@student.route("/account")
def my_account():
    return render_template("student/account/account.html", current_user=current_user)


@student.route("/account/change_password", methods=["POST"])
def change_password():

    
    current_password = request.form.get("current_password")
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")
    
    if not current_password or not new_password or not confirm_password:
        flash("All fields are required", "danger")
        return redirect(url_for("student.my_account"))
    
    if not check_password_hash(current_user.password, current_password):
        flash("Current password is incorrect", "danger")
        return redirect(url_for("student.my_account"))
    
    if new_password != confirm_password:
        flash("New passwords do not match", "danger")
        return redirect(url_for("student.my_account"))
    
    if len(new_password) < 6:
        flash("Password must be at least 6 characters long", "danger")
        return redirect(url_for("student.my_account"))
    
    current_user.password = generate_password_hash(new_password)
    db.session.commit()
    
    flash("Password updated successfully", "success")
    return redirect(url_for("student.my_account"))

#===================================================
@student.route("/whatsapp")
def whatsapp():
    full_phone_number = current_user.phone_number_country_code + current_user.phone_number
    if current_user.student_whatsapp == full_phone_number:
        flash("You have already activated WhatsApp", "warning")
        return redirect(url_for("student.new_home"))
    return render_template("student/whatsapp/whatsapp.html")

