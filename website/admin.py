from flask import Blueprint, render_template , request, redirect, url_for, flash, send_from_directory , abort , jsonify
from .models import (
    Users, Groups, Assignments, AssignmentLateException, Submissions, Announcements, Videos, WhatsappMessages, Zoom_meeting, ZoomMeetingMember,
    Quizzes, QuizGrades, Materials, Upload_status, Materials_folder, VideoViews, NextQuiz,Assignments_whatsapp, AssistantLogs, Sessions , Attendance_session , Attendance_student)
from datetime import datetime
from . import db
import pytz
import random,string
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import current_user
import os 
from werkzeug.utils import secure_filename
import json , re
from datetime import datetime as datetime_obj
from sqlalchemy import func
from datetime import datetime, timezone, timedelta, date
import uuid
from sqlalchemy import and_ , or_ , not_
from .student import get_all
from dotenv import load_dotenv
from .website import storage , send_whatsapp_message 
from sqlalchemy import and_, cast
from sqlalchemy.types import Float
load_dotenv()


admin = Blueprint('admin', __name__)
GMT_PLUS_2 = pytz.timezone('Etc/GMT-3')


#=================================================================
#=================================================================
#Helper Functions
#=================================================================
#=================================================================


def scope_match_mm_legacy(mm_rel, legacy_col, user_value):
    """
    Returns a SQLAlchemy filter for groups:
    - If user_value is not None: either the item includes it (MM or legacy),
      or the item is globally unspecified (no MM & legacy is NULL).
    - If user_value is None: only pass if the item is globally unspecified
      (no MM & legacy is NULL) — i.e., not targeting any specific group.
    """
    mm_has_any   = mm_rel.any()     
    mm_has_user  = mm_rel.any() if user_value is None else mm_rel.any(id=user_value)

    if user_value is None:
        return and_(not_(mm_has_any), legacy_col.is_(None))
    else:
        return or_(
            mm_has_user,
            legacy_col == user_value,
            and_(not_(mm_has_any), legacy_col.is_(None))
        )


def get_user_scope_ids():
    """Returns the IDs of groups managed by the current admin user."""
    managed_groups = getattr(current_user, "managed_groups", [])
    group_ids = [g.id for g in managed_groups]
    return group_ids


def can_manage(selected_ids, managed_ids):
    try :
        selected_ids = [int(id) for id in selected_ids]
    except ValueError:
        return False
    try :
        managed_ids = [int(id) for id in managed_ids]
    except ValueError:
        return False

    if not selected_ids:
        return True  
    if not managed_ids:
        return False  
    return set(selected_ids).issubset(set(managed_ids))


SCOPE_REGISTRY = {
    Announcements: {
        'groups': {'m2m': 'groups_mm', 'fk': 'groupid'},
    },
    Assignments: {
        'groups': {'m2m': 'groups_mm', 'fk': 'groupid'},
    },
    Quizzes: {
        'groups': {'m2m': 'groups_mm', 'fk': 'groupid'},
    },
    Sessions: { 
        'groups': {'m2m': 'groups_mm', 'fk': 'groupid'},
    },
    Materials_folder: {
        'groups': {'m2m': 'groups_mm', 'fk': 'groupid'},
    },
    NextQuiz: {
        'groups': {'m2m': 'groups_mm', 'fk': 'groupid'},
    },
    Attendance_session: {
        'groups': {'m2m': 'groups_mm', 'fk': 'groupid'},
    },
}

def get_visible_to_admin_query(model, admin_user, base_query=None):
    """
    Returns a SQLAlchemy query for all records of a model visible to an admin.
    Only checks group scope (simplified from multi-dimension system).
    """
    scope_config = SCOPE_REGISTRY.get(model)
    if not scope_config:
        return base_query or model.query

    admin_groups = [g.id for g in getattr(admin_user, 'managed_groups', [])]
    
    q = base_query or model.query
    
    # Only check groups scope
    group_config = scope_config.get('groups')
    if not group_config:
        return q
    
    m2m_attr_name = group_config.get('m2m')
    fk_attr_name = group_config.get('fk')
    
    has_m2m = m2m_attr_name and hasattr(model, m2m_attr_name)
    has_fk = fk_attr_name and hasattr(model, fk_attr_name)
    
    if not has_m2m and not has_fk:
        return q
    
    group_filters = []
    
    # Handle many-to-many scope
    if has_m2m:
        m2m_rel = getattr(model, m2m_attr_name)
        if admin_groups:
            group_filters.append(m2m_rel.any(m2m_rel.entity.class_.id.in_(admin_groups)))
    
    # Handle foreign key scope
    if has_fk:
        fk_col = getattr(model, fk_attr_name)
        if admin_groups:
            group_filters.append(fk_col.in_(admin_groups))
    
    # If admin manages no groups, exclude all items with group assignments
    if not admin_groups:
        group_filters.append(False)
    
    if group_filters:
        q = q.filter(or_(*group_filters))
    
    return q


def get_item_if_admin_can_manage(model, item_id, admin_user):
    """
    Fetches a single item by its ID, but only if the admin has permission to see it.
    """
    query = get_visible_to_admin_query(model, admin_user)
    return query.filter(model.id == item_id).first()



#=================================================================
#Dashboard
#=================================================================

@admin.route('/dashboard')
def dashboard():
    student_count = None
    groups_with_counts = []
    
    try:
        if current_user.role == "admin":
            group_ids = get_user_scope_ids()
            groups = Groups.query.filter(Groups.id.in_(group_ids)).all()
            
            # Add student count for each group
            for group in groups:
                # Count students using both legacy groupid and new many-to-many relationship
                student_count_for_group = Users.query.filter(
                    Users.role == 'student',
                    or_(
                        Users.groupid == group.id,  # Legacy single group
                        Users.groups.any(Groups.id == group.id)  # New many-to-many
                    )
                ).count()
                groups_with_counts.append({
                    'id': group.id,
                    'name': group.name,
                    'student_count': student_count_for_group
                })

        elif current_user.role == "super_admin":
            students = Users.query.filter_by(role='student').all()
            student_count = len(students)
            groups = Groups.query.all()
            
            # Add student count and admin count for each group
            for group in groups:
                # Count students using both legacy groupid and new many-to-many relationship
                student_count_for_group = Users.query.filter(
                    Users.role == 'student',
                    or_(
                        Users.groupid == group.id,  # Legacy single group
                        Users.groups.any(Groups.id == group.id)  # New many-to-many
                    )
                ).count()
                
                # Count admins managing this group
                admin_count_for_group = Users.query.filter(
                    Users.role == 'admin',
                    Users.managed_groups.any(Groups.id == group.id)
                ).count()
                
                groups_with_counts.append({
                    'id': group.id,
                    'name': group.name,
                    'student_count': student_count_for_group,
                    'admin_count': admin_count_for_group
                })
    except Exception as e:
        return f"{e}"
  
    return render_template('admin/dashboard.html', student_count=student_count, groups=groups_with_counts)


#Filter for all admin routes
@admin.route('/api/filters')
def api_admin_filters():
    """Returns filter data for the admin (groups only)."""
    groups = Groups.query.filter(Groups.id.in_([g.id for g in current_user.managed_groups])).all()

    filter_data = {
        "groups": [{"id": g.id, "name": g.name} for g in groups]
    }
    return jsonify(filter_data)







#=================================================================
#Announcements
#=================================================================

@admin.route('/api/announcements-data', methods=["GET"])
def announcements_data():
    announcements_query = get_visible_to_admin_query(Announcements, current_user)
    announcements = announcements_query.order_by(Announcements.creation_date.desc()).all()

    announcements_list = []
    for ann in announcements:
        groups_names = [g.name for g in getattr(ann, 'groups_mm', [])] if getattr(ann, 'groups_mm', None) else []

        # Format for display: "Item1, Item2" or "All" if empty
        groups_display = ', '.join(groups_names) if groups_names else 'All Groups'

        announcements_list.append({
            "id": ann.id,
            "title": ann.title,
            "content": ann.content,
            "creation_date": ann.creation_date.strftime('%Y-%m-%d %H:%M'),
            "groups": groups_display
        })
    
    return jsonify(announcements_list)


@admin.route('/api/announcement/<int:announcement_id>', methods=["GET"])
def get_announcement_data(announcement_id):
    """API endpoint to fetch single announcement data for editing"""
    announcement = get_item_if_admin_can_manage(Announcements, announcement_id, current_user)
    if not announcement:
        return jsonify({"success": False, "message": "Announcement not found or you do not have permission to view it."}), 404

    groups_mm = [{"id": g.id, "name": g.name} for g in getattr(announcement, 'groups_mm', [])] if getattr(announcement, 'groups_mm', None) else []

    announcement_data = {
        "id": announcement.id,
        "title": announcement.title,
        "content": announcement.content,
        "groups_mm": groups_mm,
    }

    return jsonify({"success": True, "announcement": announcement_data})


@admin.route('/announcements', methods=["GET", "POST"])
def announcements():

    if request.method == "POST":
        group_ids_user = get_user_scope_ids()

        title = (request.form.get("title") or "").strip()
        content = (request.form.get("content") or "").strip()
        
        if not title or not content:
            flash("Title and content are required.", "danger")
            return redirect(url_for("admin.announcements"))

        group_ids_mm = parse_multi_ids("groups_mm[]")

        # If no groups selected, default to all managed groups
        if not group_ids_mm:
            group_ids_mm = group_ids_user[:] if group_ids_user else [g.id for g in Groups.query.all()]

        # Verify admin has permission to post to selected groups
        if group_ids_mm:
            if not can_manage(group_ids_mm, group_ids_user):
                flash("You are not allowed to post to one or more selected groups.", "danger")
                return redirect(url_for("admin.announcements"))

        cairo_tz = pytz.timezone('Africa/Cairo')
        aware_local_time = datetime.now(cairo_tz)
        naive_local_time = aware_local_time.replace(tzinfo=None)

        new_announcement = Announcements(
            title=title,
            content=content,
            creation_date=naive_local_time
        )

        db.session.add(new_announcement)

        # Assign groups
        if group_ids_mm:
            new_announcement.groups_mm = Groups.query.filter(Groups.id.in_(group_ids_mm)).all()

        db.session.commit()
        
        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Create',
            log={
                "action_name": "Create",
                "resource_type": "announcement",
                "action_details": {
                    "id": new_announcement.id,
                    "title": new_announcement.title,
                    "summary": f"Announcement '{new_announcement.title}' was created."
                },
                "data": {
                    "content": new_announcement.content,
                    "groups_mm": [g.id for g in new_announcement.groups_mm]
                },
                "before": None,
                "after": None
            }
        )
        db.session.add(new_log)
        db.session.commit()

        flash("Announcement created successfully!", "success")
        return redirect(url_for("admin.announcements"))

    return render_template("admin/announcements/announcements.html")




@admin.route("/announcements/delete/<int:announcement_id>", methods=["POST"])
def delete_announcement(announcement_id):

    announcement = get_item_if_admin_can_manage(Announcements, announcement_id, current_user)

    if not announcement:
        flash("Announcement not found or you do not have permission to delete it.", "danger")
        return redirect(url_for("admin.announcements"))


    new_log = AssistantLogs(
        assistant_id=current_user.id,
        action='Delete',
        log={
            "action_name": "Delete",
            "resource_type": "announcement",
            "action_details": {
                "id": announcement.id,
                "title": announcement.title,
                "summary": f"Announcement '{announcement.title}' was deleted."
            },
            "data": None,
            "before": {
                "title": announcement.title,
                "content": announcement.content,
                "groups_mm": [g.id for g in announcement.groups_mm]
            },
            "after": None
        }
    )
    db.session.add(new_log)
    db.session.delete(announcement)
    db.session.commit()
    
    flash("Announcement deleted successfully!", "success")
    return redirect(url_for("admin.announcements"))


@admin.route("/announcements/edit/<int:announcement_id>", methods=["POST"])
def edit_announcement(announcement_id):
    announcement = get_item_if_admin_can_manage(Announcements, announcement_id, current_user)
    if not announcement:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Announcement not found or you do not have permission to edit it."}), 404
        flash("Announcement not found or you do not have permission to edit it.", "danger")
        return redirect(url_for("admin.announcements"))

    group_ids_user = get_user_scope_ids()

    # Store old values for logging
    old_announcement = {
        "title": announcement.title,
        "content": announcement.content,
        "groups_mm": [g.id for g in announcement.groups_mm]
    }

    title = (request.form.get("title") or "").strip()
    content = (request.form.get("content") or "").strip()
    
    if not title or not content:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Title and content are required."}), 400
        flash("Title and content are required.", "danger")
        return redirect(url_for("admin.announcements"))

    group_ids_mm = parse_multi_ids("groups_mm[]")

    if not group_ids_mm:
        group_ids_mm = group_ids_user[:] if group_ids_user else [g.id for g in Groups.query.all()]

    # Validation
    if group_ids_mm:
        if not can_manage(group_ids_mm, group_ids_user):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": False, "message": "You are not allowed to post to one or more selected groups."}), 403
            flash("You are not allowed to post to one or more selected groups.", "danger")
            return redirect(url_for("admin.announcements"))

    # Update announcement
    announcement.title = title
    announcement.content = content

    if group_ids_mm:
        announcement.groups_mm = Groups.query.filter(Groups.id.in_(group_ids_mm)).all()

    db.session.commit()

    # Log the edit action
    new_log = AssistantLogs(
        assistant_id=current_user.id,
        action='Edit',
        log={
            "action_name": "Edit",
            "resource_type": "announcement",
            "action_details": {
                "id": announcement.id,
                "title": announcement.title,
                "summary": f"Announcement '{announcement.title}' was edited."
            },
            "data": None,
            "before": old_announcement,
            "after": {
                "title": announcement.title,
                "content": announcement.content,
                "groups_mm": [g.id for g in announcement.groups_mm]
            }
        }
    )
    db.session.add(new_log)
    db.session.commit()

    # Return JSON for AJAX requests
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        groups_names = [g.name for g in getattr(announcement, 'groups_mm', [])] if getattr(announcement, 'groups_mm', None) else []
        groups_display = ', '.join(groups_names) if groups_names else 'All Groups'

        announcement_data = {
            "id": announcement.id,
            "title": announcement.title,
            "content": announcement.content,
            "creation_date": announcement.creation_date.strftime('%Y-%m-%d %H:%M'),
            "groups": groups_display
        }
        return jsonify({"success": True, "message": "Announcement updated successfully!", "announcement": announcement_data})

    flash("Announcement updated successfully!", "success")
    return redirect(url_for("admin.announcements"))


#=================================================================
#Students
#=================================================================
@admin.route('/students', methods=['GET'])
def students():
    
    page = request.args.get('page', 1, type=int)
    per_page = 51

    search = request.args.get('search', '', type=str).strip()
    group = request.args.get('group', '', type=str).strip()

    # query = Users.query.filter(Users.role == 'student', Users.code != 'nth', Users.code != 'Nth')
    query = Users.query.filter(Users.role == 'student')


    # Apply admin scope restrictions (groups only)
    if current_user.role != "super_admin":
        group_ids = get_user_scope_ids()
        
        if group_ids:
            query = query.filter(Users.groupid.in_(group_ids))

    if search:
        search_like = f"%{search}%"
        query = query.filter(
            (Users.name.ilike(search_like)) |
            (Users.code.ilike(search_like)) |
            (Users.phone_number.ilike(search_like)) |
            (Users.email.ilike(search_like)) | 
            (Users.parent_phone_number.ilike(search_like))
        )

    if group:
        query = query.join(Groups).filter(Groups.name == group)

    query = query.distinct()

    # Order by code alphabetically (handles format like ABC-001, BRS-001, etc.)
    pagination = query.order_by(Users.code.asc()).paginate(page=page, per_page=per_page, error_out=False)
    users = pagination.items

    # Filter dropdown options based on admin scope
    if current_user.role == "super_admin":
        groups = Groups.query.all()
    else:
        group_ids = get_user_scope_ids()
        groups = Groups.query.filter(Groups.id.in_(group_ids)).all() if group_ids else []

    return render_template(
        'admin/students.html',
        users=users,
        groups=groups,
        pagination=pagination,
    )

#=================================================================
#Approve Students
#=================================================================
@admin.route('/approve/students', methods=['GET', 'POST'])
def approve_students():

    page = request.args.get('page', 1, type=int)
    per_page = 50

    # Base query for students needing approval
    query = Users.query.filter(
        Users.role == 'student',
        (Users.code == 'nth') | (Users.code == 'Nth')
    )

    # Apply admin scope restrictions (groups only)
    if current_user.role != "super_admin":
        group_ids = get_user_scope_ids()
        
        if group_ids:
            # Filter by many-to-many groups relationship
            query = query.join(Users.groups).filter(Groups.id.in_(group_ids))

    pagination = query.order_by(Users.id.asc()).paginate(page=page, per_page=per_page, error_out=False)
    users = pagination.items

    # Get groups for dropdown based on admin scope
    if current_user.role == "super_admin":
        groups = Groups.query.all()
    else:
        group_ids = get_user_scope_ids()
        groups = Groups.query.filter(Groups.id.in_(group_ids)).all() if group_ids else []

    # Calculate last index for each group for code generation
    GROUP_ABBREVIATIONS = {
        "Group A": "GPA",
        "Group B": "GPB",
        "Group C": "GPC",
        "Group D": "GPD",
        "Online": "ONL",
    }

    group_codes = {}
    for group in groups:
        abbr = GROUP_ABBREVIATIONS.get(group.name)
        if abbr:
            group_codes[group.id] = abbr
        else:
            group_codes[group.id] = group.name[:3].upper()

    last_group_indices = {}
    for group in groups:
        # Query to find the highest numeric code for each group using many-to-many relationship
        students_with_codes = Users.query.join(Users.groups).filter(
            Users.role == 'student',
            Groups.id == group.id,
            Users.code != 'nth',
            Users.code != 'Nth',
            Users.code.ilike(f"{group_codes[group.id]}-%")
        ).all()
        
        last_index = 0
        for student in students_with_codes:
            if student.code:
                try:
                    # Extract the numeric part after the hyphen
                    code_parts = student.code.split('-')
                    if len(code_parts) >= 2:
                        index = int(code_parts[-1])
                        if index > last_index:
                            last_index = index
                except Exception:
                    continue
        
        last_group_indices[group.id] = last_index

    return render_template(
        'admin/approve.html',
        users=users,
        group_codes=group_codes,
        last_group_indices=last_group_indices,
        groups=groups,
        pagination=pagination
    )


@admin.route('/approve/student/<int:user_id>', methods=['POST'])
def approve_student(user_id):
    user = Users.query.get_or_404(user_id)
    
    # Check if admin has permission to approve this student (groups only)
    if current_user.role != "super_admin":
        group_ids = get_user_scope_ids()
        
        # Verify the student is within admin's scope using many-to-many groups
        user_group_ids = [g.id for g in user.groups]
        if not (group_ids and any(gid in group_ids for gid in user_group_ids)):
            flash('You do not have permission to approve this student.', 'danger')
            return redirect(url_for('admin.approve_students'))
    
    new_code = request.form.get('code').strip()

    if new_code:
        code_exists = Users.query.filter(
            Users.code == new_code,
            Users.id != user.id
        ).first()

        if code_exists:
            flash('This code is already used by another user. Please choose a different code.', 'danger')
            return redirect(url_for('admin.approve_students'))

        user.code = new_code


        db.session.commit()

        # Get group IDs for logging
        user_group_ids = [g.id for g in user.groups]

        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Create',
            log={
                "action_name": "Create",
                "resource_type": "student",
                "action_details": {
                    "id": user.id,
                    "title": user.name,
                    "summary": f"Student '{user.name}' was approved and assigned a code."
                },
                "data": {
                    "name": user.name,
                    "email": user.email,
                    "phone_number": user.phone_number,
                    "code": user.code,
                    "group_ids": user_group_ids,
                    "role": user.role,
                    "student_whatsapp": user.student_whatsapp,
                    "parent_whatsapp": user.parent_whatsapp,
                },
                "before": None,
                "after": None
            }
        )
        db.session.add(new_log)
        db.session.commit()

        flash('Student approved successfully!', 'success')

        return redirect(url_for('admin.approve_students'))
    else:
        flash('No code provided. Please enter a code.', 'danger')
        return redirect(url_for('admin.approve_students'))


@admin.route('/approve/students/bulk', methods=['POST'])
def bulk_approve_students():
    try:
        data = request.get_json()
        students = data.get('students', [])

        if not students:
            return jsonify({'success': False, 'message': 'No students provided'}), 400

        # Check permissions
        if current_user.role != "super_admin":
            group_ids = get_user_scope_ids()
        else:
            group_ids = None

        approved_count = 0
        errors = []
        
        # Track codes being assigned in this batch to avoid duplicates
        codes_in_batch = set()
        
        # Helper function to find next available code
        def find_next_available_code(base_code):
            """
            Given a code like 'GPA-001', find the next available code.
            Returns the next available code (e.g., 'GPA-002', 'GPA-003', etc.)
            """
            parts = base_code.split('-')
            if len(parts) < 2:
                return base_code
            
            prefix = parts[0]
            try:
                current_num = int(parts[1])
            except ValueError:
                return base_code
            
            # Start checking from current number
            attempt_num = current_num
            max_attempts = 1000  # Safety limit
            
            for _ in range(max_attempts):
                candidate_code = f"{prefix}-{attempt_num:03d}"
                
                # Check if code exists in database OR in current batch
                code_exists = Users.query.filter(
                    Users.code == candidate_code
                ).first()
                
                if not code_exists and candidate_code not in codes_in_batch:
                    return candidate_code
                
                attempt_num += 1
            
            # Fallback if we somehow exhaust attempts
            return f"{prefix}-{attempt_num:03d}"

        for student_data in students:
            user_id = student_data.get('id')
            suggested_code = student_data.get('code', '').strip()

            if not user_id or not suggested_code:
                errors.append(f"Invalid data for student ID {user_id}")
                continue

            user = Users.query.get(user_id)
            if not user:
                errors.append(f"Student with ID {user_id} not found")
                continue

            # Check if admin has permission (groups only)
            if group_ids is not None:
                user_group_ids = [g.id for g in user.groups]
                if not any(gid in group_ids for gid in user_group_ids):
                    errors.append(f"No permission to approve {user.name}")
                    continue

            # Find next available code (handling conflicts)
            new_code = find_next_available_code(suggested_code)
            
            # Add to batch tracker
            codes_in_batch.add(new_code)

            # Approve the student
            user.code = new_code
            db.session.flush()  # Flush to make code available for conflict checking

            # Log the action
            user_group_ids = [g.id for g in user.groups]
            new_log = AssistantLogs(
                assistant_id=current_user.id,
                action='Create',
                log={
                    "action_name": "Create",
                    "resource_type": "student",
                    "action_details": {
                        "id": user.id,
                        "title": user.name,
                        "summary": f"Student '{user.name}' was approved and assigned code {new_code} (bulk action)."
                    },
                    "data": {
                        "name": user.name,
                        "email": user.email,
                        "phone_number": user.phone_number,
                        "code": user.code,
                        "group_ids": user_group_ids,
                        "role": user.role,
                        "student_whatsapp": user.student_whatsapp,
                        "parent_whatsapp": user.parent_whatsapp,
                    },
                    "before": None,
                    "after": None
                }
            )
            db.session.add(new_log)
            approved_count += 1

        db.session.commit()

        if errors and approved_count == 0:
            return jsonify({
                'success': False,
                'approved_count': 0,
                'errors': errors,
                'message': f'Failed to approve students: {"; ".join(errors)}'
            }), 400

        return jsonify({
            'success': True,
            'approved_count': approved_count,
            'errors': errors if errors else None,
            'message': f'Successfully approved {approved_count} student(s)' + (f' with {len(errors)} error(s)' if errors else '')
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500



#=================================================================
# Student Info (Student_data.html)
#=================================================================
@admin.route("/student/<int:user_id>", methods=["GET"])
def student(user_id):
    student_obj = Users.query.filter_by(id=user_id, role="student").first()
    if not student_obj:
        flash("Student not found!", "danger")
        return redirect(url_for("admin.students"))

    # Check if admin has permission to view this student (groups only)
    if current_user.role != "super_admin":
        group_ids = get_user_scope_ids()
        
        # Verify the student is within admin's scope
        if not (group_ids and student_obj.groupid in group_ids):
            flash('You do not have permission to view this student.', 'danger')
            return redirect(url_for('admin.students'))

    quiz_grades = student_obj.quiz_grades
    submissions = student_obj.submissions

    assignments = get_all(Assignments, student_obj.id).filter(Assignments.type == "Assignment")
    
    # Get exam assignments for quiz display
    exam_assignments = get_all(Assignments, student_obj.id).filter(Assignments.type == "Exam")

    videos = get_all(Videos, student_obj.id)

    submission_ids = {sub.assignment_id for sub in submissions}
    submission_marks = {sub.assignment_id: sub.mark for sub in submissions}
    watched_videos = {view.video_id: view.view_count for view in VideoViews.query.filter_by(student_id=student_obj.id).all()}

    corrector_names = {}
    for grade in quiz_grades:
        corrector = Users.query.filter_by(id=grade.corrector_id).first()
        corrector_names[grade.id] = corrector.name if corrector else "N/A"
    


    cairo_tz = pytz.timezone('Africa/Cairo')
    now_cairo = datetime.now(cairo_tz)
    late_exception_map = {}
    for exception in AssignmentLateException.query.filter_by(student_id=student_obj.id).all():
        aware_deadline = None
        if exception.extended_deadline:
            try:
                aware_deadline = cairo_tz.localize(exception.extended_deadline)
            except ValueError:
                aware_deadline = exception.extended_deadline.astimezone(cairo_tz)
        is_active = aware_deadline is None or aware_deadline >= now_cairo
        late_exception_map[exception.assignment_id] = {
            "exception": exception,
            "aware_deadline": aware_deadline,
            "is_active": is_active,
        }

    return render_template(
        "admin/student_data.html",
        student=student_obj,
        quiz_grades=exam_assignments,
        submission_ids=submission_ids,
        submission_marks=submission_marks,
        assignments=assignments,
        videos=videos,
        watched_videos=watched_videos,
        corrector_names=corrector_names,
        late_exception_map=late_exception_map
    )


@admin.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    user = Users.query.get_or_404(user_id)

    if user.role == "admin" or user.role == "super_admin":
        flash('User is an admin. Use the "Edit Admin" page.', 'error')
        return redirect(url_for('admin.edit_assistant', user_id=user_id))

    # Check if admin has permission to edit this user (groups only)
    if current_user.role != "super_admin":
        group_ids = get_user_scope_ids()
        
        # Verify the user is within admin's scope
        if not (group_ids and user.groupid in group_ids):
            flash('You do not have permission to edit this user.', 'danger')
            return redirect(url_for('admin.students'))

    groups = Groups.query.all()

    if request.method == 'POST':
        if 'delete_user' in request.form:
            try:


                # Save old user data for logging
                old_user_data = {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "phone_number": user.phone_number,
                    "parent_phone_number": user.parent_phone_number,
                    "code": user.code,
                    "groupid": user.groupid,
                    "profile_picture": user.profile_picture,
                    "role": user.role,
                    "parent_email": user.parent_email,
                    "parent_name": user.parent_name,
                    "parent_type": user.parent_type,
                    "points": user.points,
                    "otp": user.otp,
                    "login_count": user.login_count,
                    "last_website_access": str(user.last_website_access) if user.last_website_access else None,
                }

                for submission in Submissions.query.filter_by(student_id=user.id).all():
                    if submission.file_url:
                        local_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url)
                        if os.path.exists(local_path):
                            try:
                                os.remove(local_path)
                            except Exception:
                                pass  # Ignore file errors, continue deleting
                        try:
                            storage.delete_file(folder=f"submissions/uploads/student_{submission.student_id}", file_name=submission.file_url)
                        except Exception as e:
                            flash(f"Error deleting from s3: {e}", 'error')
                            pass  # Ignore S3 errors, continue deleting
                    db.session.delete(submission)

                QuizGrades.query.filter_by(student_id=user.id).delete(synchronize_session=False)
                VideoViews.query.filter_by(student_id=user.id).delete(synchronize_session=False)

                storage.delete_file(folder="profile_pictures", file_name=user.profile_picture)
                local_path = os.path.join("website", "static", "profile_pictures", user.profile_picture)
                if os.path.exists(local_path):
                    os.remove(local_path)


                # Generate random suffix for email and phone
                random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
                # Clear all user fields and assign random but unique values to avoid DB constraint errors
                user.role = "Student_Deleted"
                user.password = "Password"
                user.phone_number = "del_" + random_suffix
                user.parent_phone_number = "del_p_" + random_suffix # Use a different prefix to avoid unique constraint errors
                user.email = "deleted_" + random_suffix + "@example.com"
                user.parent_email = "deleted_p_" + random_suffix + "@example.com"
                user.parent_name = "Deleted_" + random_suffix
                user.parent_type = "Deleted" # This was also potentially too long
                user.profile_picture = "deleted_" + random_suffix + ".jpg"
                user.name = ""
                user.code = ""
                user.points = 0
                user.otp = None
                user.groupid = None
                user.login_count = 0
                user.last_website_access = None

                db.session.commit()

                # Log the user deletion
                new_log = AssistantLogs(
                    assistant_id=current_user.id,
                    action='Delete',
                    log={
                        "action_name": "Delete",
                        "resource_type": "user",
                        "action_details": {
                            "id": old_user_data['id'],
                            "title": old_user_data['name'],
                            "summary": f"User '{old_user_data['name']}' (id={old_user_data['id']}) was deleted."
                        },
                        "data": None,
                        "before": old_user_data,
                        "after": None
                    }
                )
                db.session.add(new_log)
                db.session.commit()

                flash('User and related data deleted successfully!', 'success')
                return redirect(url_for('admin.students'))
            except Exception as e:
                flash(f"Error occurred while deleting user: {e}", 'error')
                return redirect(url_for('admin.edit_user', user_id=user_id))
        try:
            # Save old user data for logging
            old_user_data = {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "phone_number": user.phone_number,
                "parent_phone_number": user.parent_phone_number,
                "code": user.code,
                "groupid": user.groupid,
                "profile_picture": user.profile_picture,
                "role": user.role,
                "parent_email": user.parent_email,
                "parent_name": user.parent_name,
                "parent_type": user.parent_type,
                "points": user.points,
                "otp": user.otp,
                "login_count": user.login_count,
                "last_website_access": str(user.last_website_access) if user.last_website_access else None,
            }

            user.name = request.form['name']
            user.email = request.form['email']
            user.phone_number = request.form['phone_number']
            user.phone_number_country_code = request.form['phone_number_country_code']

            user.parent_phone_number = request.form['parent_phone_number']
            user.parent_phone_number_country_code = request.form['parent_phone_number_country_code']


            # user.code = request.form['code']
            user.groupid = int(request.form['group']) if request.form['group'] else None
            if request.form['code'] != user.code :
                code_exists = Users.query.filter(
                    Users.code == request.form['code'].strip(),
                    Users.id != user.id
                ).first()
                if code_exists:
                    flash('This code is already used by another user. Please choose a different code.', 'danger')
                    return redirect(url_for('admin.edit_user', user_id=user_id))
                user.code = request.form['code']

            db.session.commit()

            # Log the user edit
            new_log = AssistantLogs(
                assistant_id=current_user.id,
                action='Edit',
                log={
                    "action_name": "Edit",
                    "resource_type": "user",
                    "action_details": {
                        "id": user.id,
                        "title": user.name,
                        "summary": f"User '{user.name}' (id={user.id}) was edited."
                    },
                    "data": None,
                    "before": old_user_data,
                    "after": {
                        "id": user.id,
                        "name": user.name,
                        "email": user.email,
                        "phone_number": user.phone_number,
                        "parent_phone_number": user.parent_phone_number,
                        "code": user.code,
                        "groupid": user.groupid,
                        "profile_picture": user.profile_picture,
                        "role": user.role,
                        "parent_email": user.parent_email,
                        "parent_name": user.parent_name,
                        "parent_type": user.parent_type,
                        "points": user.points,
                        "otp": user.otp,
                        "login_count": user.login_count,
                        "last_website_access": str(user.last_website_access) if user.last_website_access else None,
                    }
                }
            )
            db.session.add(new_log)
            db.session.commit()

            flash('Changes saved successfully!', 'success')
            return redirect(url_for('admin.students'))
        except Exception as e:
            flash(f"Error occurred: {e}", 'error')
            return redirect(url_for('admin.edit_user', user_id=user_id))
    return render_template('admin/edit_user.html', user=user, groups=groups)


#Reset password for a user (Admin route)
@admin.route('/reset_password/<int:user_id>')
def reset_password(user_id):
    user = Users.query.get_or_404(user_id)
    
    # Check if admin has permission to reset password for this user (groups only)
    if current_user.role != "super_admin":
        group_ids = get_user_scope_ids()
        
        # Verify the user is within admin's scope
        if not (group_ids and user.groupid in group_ids):
            flash('You do not have permission to reset password for this user.', 'danger')
            return redirect(url_for('admin.students'))
    
    random_password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        
    hashed_password = generate_password_hash(random_password, method="pbkdf2:sha256", salt_length=8)
    user.password = hashed_password
    db.session.commit()


    log_entry = AssistantLogs(
        assistant_id=current_user.id ,
        action="reset_password",
        log={
            "action_name": "Edit",
            "resource_type": "user_password",
            "action_details": {
                "id": user.id,
                "title": user.name,
                "summary": f"User '{user.name}' had their password reset."
            },
            "data": None,
            "before": None,
            "after": {
                "password_reset": True
            }
        }
    )
    db.session.add(log_entry)
    db.session.commit()

    flash(f'Password has been set to "{random_password}"! Save it!', 'success')
    try : 
        send_whatsapp_message(user.phone_number, f"Your password has been reset to \n{random_password}. \nPlease login to your account and change your password.")
    except :
        pass
    if user.role == 'student' :
        return redirect(url_for('admin.student', user_id=user.id))
    else :
        return redirect(url_for('admin.assistants'))



#=================================================================
#Assistants
#=================================================================
@admin.route('/assistants', methods=['GET'])
def assistants():
    if current_user.role != 'super_admin':
        flash('You are not authorized to edit an assistant.', 'danger')
        return redirect(url_for('admin.assistants'))
        
    users = Users.query.filter(Users.role.in_(['admin', 'super_admin'])).all()
    return render_template('admin/assistant/assistants.html', users=users)


@admin.route('/edit_assistant/<int:user_id>', methods=['GET', 'POST'])
def edit_assistant(user_id):
    if current_user.role != 'super_admin':
        flash('You are not authorized to edit an assistant.', 'danger')
        return redirect(url_for('admin.assistants'))
    user = Users.query.get_or_404(user_id)

    if user.role == "student":
        flash('User is a student. Use the "Edit Student" page.', 'error')
        return redirect(url_for('admin.edit_user', user_id=user_id))

    # Fetch groups to display in the form
    groups = Groups.query.all()

    if request.method == 'POST':
        if 'delete_user' in request.form:
            if user.role == 'admin' and current_user.role != 'super_admin':
                flash('You can\'t delete an assistant!', 'danger')
                return redirect(url_for('admin.assistants', user_id=user_id))
            if user.role == 'super_admin' and current_user.role != 'super_admin':
                flash('You can\'t delete a Head!', 'danger')
                return redirect(url_for('admin.assistants'))
            if user.id == current_user.id:
                flash('You can\'t delete yourself!', 'danger')
                return redirect(url_for('admin.assistants', user_id=user_id))
            
            # Log the action before deleting
            new_log = AssistantLogs(
                assistant_id=current_user.id,
                action='Delete',
                log={
                    "action_name": "Delete",
                    "resource_type": "assistant",
                    "action_details": {
                        "id": user.id,
                        "title": user.name,
                        "summary": f"Assistant '{user.name}' was deleted."
                    },
                    "data": None,
                    "before": {
                        "name": user.name,
                        "email": user.email,
                        "phone_number": user.phone_number,
                        "role": user.role
                    },
                    "after": None
                }
            )
            db.session.add(new_log)
            db.session.delete(user)
            db.session.commit()
            
            flash('Assistant and related data have been deleted successfully!', 'success')
            return redirect(url_for('admin.assistants'))
        try:
            # Capture the "before" state (groups only)
            old_data = {
                "name": user.name,
                "email": user.email,
                "phone_number": user.phone_number,
                "role": user.role,
                "managed_groups": [g.id for g in user.managed_groups]
            }
            
            # Update basic assistant info
            user.name = request.form['name']
            user.email = request.form['email']
            user.phone_number = request.form['phone_number']

            # Get the list of group IDs from the form
            selected_group_ids = request.form.getlist('group_ids', type=int)

            # If no groups selected, select all (assistant can manage all groups)
            if not selected_group_ids:
                selected_group_ids = [group.id for group in Groups.query.all()]
            
            # Clear existing groups and assign new ones
            user.managed_groups.clear()
            groups_to_assign = Groups.query.filter(Groups.id.in_(selected_group_ids)).all()
            user.managed_groups.extend(groups_to_assign)
            
            db.session.commit()

            # Create the log with the "before" and "after" data
            new_log = AssistantLogs(
                assistant_id=current_user.id,
                action='Edit',
                log={
                    "action_name": "Edit",
                    "resource_type": "assistant",
                    "action_details": {
                        "id": user.id,
                        "title": user.name,
                        "summary": f"Assistant '{user.name}' was edited."
                    },
                    "data": None,
                    "before": old_data,
                    "after": {
                        "name": user.name,
                        "email": user.email,
                        "phone_number": user.phone_number,
                        "role": user.role,
                        "managed_groups": selected_group_ids
                    }
                }
            )
            db.session.add(new_log)
            db.session.commit()

            flash('Changes saved successfully!', 'success')
            return redirect(url_for('admin.assistants'))
        except Exception as e:
            db.session.rollback()
            flash(f"(Check if the phone number or email is already in use) Error occurred: {e}", 'error')
            return redirect(url_for('admin.edit_assistant', user_id=user_id))
            
    # Pass groups to the template for the GET request
    return render_template('admin/assistant/edit_admin.html', user=user, groups=groups)


@admin.route('/add_assistant', methods=['GET', 'POST'])
def add_assistant():
    if current_user.role != 'super_admin':
        flash('You are not authorized to add an assistant.', 'danger')
        return redirect(url_for('admin.assistants'))
    
    # Fetch groups to display in the form
    groups = Groups.query.all()
    
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone_number = request.form.get('phone_number')
        role = request.form.get('role')
        password = request.form.get('password')

        existing_user = Users.query.filter_by(email=email).first()
        if existing_user:
            flash('A user with this email already exists.', 'error')
            return render_template('admin/assistant/add_assistant.html', name=name, email=email, phone_number=phone_number, role=role, groups=groups)

        new_admin = Users(
            name=name,
            email=email,
            phone_number=phone_number,
            role=role,
            password=generate_password_hash(password, method="pbkdf2:sha256", salt_length=8)
        )
        try:
            db.session.add(new_admin)
            db.session.flush()  # Flush to get the ID before committing
            
            # Get the list of group IDs from the form
            selected_group_ids = request.form.getlist('group_ids', type=int)

            # If no groups selected, select all (assistant can manage all groups)
            if not selected_group_ids:
                selected_group_ids = [group.id for group in Groups.query.all()]
            
            # Query for the groups to assign
            groups_to_assign = Groups.query.filter(Groups.id.in_(selected_group_ids)).all()
            
            # Assign the groups
            new_admin.managed_groups.extend(groups_to_assign)
            
            db.session.commit()

            new_log = AssistantLogs(
                assistant_id=current_user.id,
                action='Create',
                log={
                    "action_name": "Create",
                    "resource_type": "assistant",
                    "action_details": {
                        "id": new_admin.id,
                        "title": new_admin.name,
                        "summary": f"New assistant '{new_admin.name}' with role '{new_admin.role}' was created."
                    },
                    "data": {
                        "name": new_admin.name,
                        "email": new_admin.email,
                        "phone_number": new_admin.phone_number,
                        "role": new_admin.role,
                        "managed_groups": selected_group_ids
                    },
                    "before": None,
                    "after": None
                }
            )
            db.session.add(new_log)
            db.session.commit()

            flash('Assistant added successfully!', 'success')
            try:
                send_whatsapp_message(f"2{new_admin.phone_number}", f"Your assistant account has been created. \nYour Phone number is {new_admin.phone_number}. \nYour password is {password}. \nPlease login to your account and change your password." , bypass= True)
            except:
                pass
            return redirect(url_for('admin.assistants'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error occurred: {e}", 'error')
            return render_template('admin/assistant/add_assistant.html', name=name, email=email, phone_number=phone_number, role=role, groups=groups)
    
    # Pass groups to the template for the GET request
    return render_template('admin/assistant/add_assistant.html', groups=groups)


#=================================================================
#Assignments
#=================================================================


# --- helpers for assignments -------------------------------------------------

def int_or_none(x):
    try:
        return int(x) if x not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None

def parse_multi_ids(field_name):
    """Parse <select multiple> values safely into a list[int]."""
    return [int(v) for v in request.form.getlist(field_name) if str(v).isdigit()]

def parse_deadline(dt_str):
    """Parse HTML datetime-local input."""
    return datetime_obj.strptime(dt_str, "%Y-%m-%dT%H:%M")


def qualified_students_count_for_assignment(assignment):
    """
    Count students that qualify for this assignment (groups-only scope).
    - If MM groups exist, use them.
    - Else if legacy groupid exists, use it.
    - Else assignment is global (no filter).
    """
    # base filter: active students with valid code
    base_filters = [
        Users.role == "student",
        Users.code != 'nth',
        Users.code != 'Nth',
    ]

    # collect MM group ids safely (relationship may be empty)
    mm_group_ids = [g.id for g in getattr(assignment, "groups_mm", [])]

    filters = list(base_filters)

    # group filter
    if mm_group_ids:
        filters.append(Users.groupid.in_(mm_group_ids))
    elif assignment.groupid:
        filters.append(Users.groupid == assignment.groupid)

    return Users.query.filter(and_(*filters)).count()

def qualified_students_count_for_quiz(quiz):
    """
    Count students that qualify for this quiz (groups-only scope).
    - If MM groups exist, use them.
    - Else if legacy groupid exists, use it.
    - Else quiz is global (no filter).
    """
    # base filter: active students with valid code
    base_filters = [
        Users.role == "student",
        Users.code != 'nth',
        Users.code != 'Nth',
    ]

    # collect MM group ids safely (relationship may be empty)
    mm_group_ids = [g.id for g in getattr(quiz, "groups_mm", [])]

    filters = list(base_filters)

    # group filter
    if mm_group_ids:
        filters.append(Users.groupid.in_(mm_group_ids))
    elif quiz.groupid:
        filters.append(Users.groupid == quiz.groupid)

    return Users.query.filter(and_(*filters)).count()


def get_qualified_students_query(target_object, admin_id=None):
    """
    Builds a SQLAlchemy query for students qualified for a target object 
    (e.g., an assignment or quiz), optionally filtered by an admin's group scope.
    """
    # base filter: active students with valid code
    base_filters = [
        Users.role == "student",
        Users.code != 'nth',
        Users.code != 'Nth',
    ]

    # collect MM group ids safely (relationship may be empty)
    mm_group_ids = [g.id for g in getattr(target_object, "groups_mm", [])]

    filters = list(base_filters)

    # group filter
    if mm_group_ids:
        filters.append(Users.groupid.in_(mm_group_ids))
    elif target_object.groupid:
        filters.append(Users.groupid == target_object.groupid)

    # Apply admin scope if provided (groups only)
    if admin_id:
        admin = Users.query.get(admin_id)
        if admin:
            managed_group_ids = [g.id for g in admin.managed_groups]
            if managed_group_ids:
                filters.append(Users.groupid.in_(managed_group_ids))

    return Users.query.filter(and_(*filters))

# --- route ---------------------------------------------------

#-----------------------------------------------------------------
# Assignments API (JSON) similar to announcements-data
#-----------------------------------------------------------------
@admin.route('/api/assignments-data', methods=["GET"])
def assignments_data():
    # Get optional group_id filter from query params
    group_id = request.args.get('group_id', type=int)

    assignments_query = get_visible_to_admin_query(Assignments, current_user)
    assignments_query = assignments_query.filter(Assignments.type == "Assignment")
    
    # Filter by group if group_id is provided
    if group_id:
        assignments_query = assignments_query.filter(Assignments.groups_mm.any(Groups.id == group_id))
    
    assignments = assignments_query.order_by(Assignments.creation_date.desc()).all()

    assignments_list = []
    for a in assignments:
        # ✅ Qualified students scoped to admin
        qualified_students_subq = (
            get_qualified_students_query(a, current_user.id)
            .with_entities(Users.id)
            .subquery()
        )
        qualified_count = db.session.query(qualified_students_subq.c.id).count()

        # ✅ Submitted students, also scoped to admin
        submitted_count = (
            Submissions.query
            .with_entities(Submissions.student_id)
            .filter(Submissions.assignment_id == a.id)
            .filter(Submissions.student_id.in_(db.select(qualified_students_subq.c.id)))
            .distinct()
            .count()
        )

        groups_names = [g.name for g in getattr(a, 'groups_mm', [])] if getattr(a, 'groups_mm', None) else []

        assignments_list.append({
            "id": a.id,
            "title": a.title,
            "description": a.description,
            "creation_date": a.creation_date.strftime('%Y-%m-%d %I:%M %p') if a.creation_date else None,
            "deadline_date": a.deadline_date.strftime('%Y-%m-%d %I:%M %p') if a.deadline_date else None,
            "groups": groups_names,
            "status": a.status,
            "out_of": a.out_of,
            "submitted_students_count": submitted_count,
            "qualified_students_count": qualified_count,
            "student_whatsapp": a.student_whatsapp,
            "parent_whatsapp": a.parent_whatsapp,
            "close_after_deadline": a.close_after_deadline,
            "points": a.points,
            "created_by": a.created_by,
            "created_at": a.creation_date.strftime('%Y-%m-%d %I:%M %p') if a.creation_date else None,
            "last_edited_by": a.last_edited_by,
            "last_edited_at": a.last_edited_at.strftime('%Y-%m-%d %I:%M %p') if a.last_edited_at else None,
        })

    return jsonify(assignments_list)



@admin.route('/api/assignment/<int:assignment_id>', methods=["GET"])
def get_assignment_data(assignment_id):
    """API endpoint to fetch single assignment data for editing"""
    assignment = get_item_if_admin_can_manage(Assignments, assignment_id, current_user)
    if not assignment:
        return jsonify({"success": False, "message": "Assignment not found or you do not have permission to view it."}), 404

    if not assignment.type == "Assignment":
        return jsonify({"success": False, "message": "Assignment is not an assignment."}), 400

    existing_attachments = json.loads(assignment.attachments) if assignment.attachments else []
    
    groups_mm = [{"id": g.id, "name": g.name} for g in getattr(assignment, 'groups_mm', [])] if getattr(assignment, 'groups_mm', None) else []

    # Fetch user objects if created_by/last_edited_by are user IDs
    created_by_user = Users.query.get(assignment.created_by) if assignment.created_by else None
    last_edited_by_user = Users.query.get(assignment.last_edited_by) if assignment.last_edited_by else None
    
    assignment_data = {
        "id": assignment.id,
        "title": assignment.title,
        "description": assignment.description,
        "deadline_date": assignment.deadline_date.strftime('%Y-%m-%dT%H:%M') if assignment.deadline_date else None,
        "groups_mm": groups_mm,
        "attachments": existing_attachments,
        "student_whatsapp": assignment.student_whatsapp,
        "parent_whatsapp": assignment.parent_whatsapp,
        "out_of": assignment.out_of,
        "status": assignment.status,
        "points": assignment.points,
        "close_after_deadline": assignment.close_after_deadline,
        "created_by": created_by_user.name if created_by_user else None,
        "created_at": assignment.creation_date.strftime('%Y-%m-%d %I:%M %p') if assignment.creation_date else None,
        "last_edited_by": last_edited_by_user.name if last_edited_by_user else None,
        "last_edited_at": assignment.last_edited_at.strftime('%Y-%m-%d %I:%M %p') if assignment.last_edited_at else None,
    }

    return jsonify({"success": True, "assignment": assignment_data})


@admin.route('/api/exams-data', methods=["GET"])
def exams_data():
    # Get optional group_id filter from query params
    group_id = request.args.get('group_id', type=int)
    
    assignments_query = get_visible_to_admin_query(Assignments, current_user)
    assignments_query = assignments_query.filter(Assignments.type == "Exam")
    
    # Filter by group if group_id is provided
    if group_id:
        assignments_query = assignments_query.filter(Assignments.groups_mm.any(Groups.id == group_id))
    
    exams = assignments_query.order_by(Assignments.creation_date.desc()).all()

    exams_list = []
    for e in exams:
        # ✅ Subquery: only qualified students for this exam, scoped to current admin
        qualified_students_subq = (
            get_qualified_students_query(e, current_user.id)
            .with_entities(Users.id)
            .subquery()
        )
        qualified_count = db.session.query(qualified_students_subq.c.id).count()

        # ✅ Submitted count, scoped to admin-managed students
        submitted_count = (
            Submissions.query
            .with_entities(Submissions.student_id)
            .filter(Submissions.assignment_id == e.id)
            .filter(Submissions.student_id.in_(qualified_students_subq))
            .distinct()
            .count()
        )

        groups_names = [g.name for g in getattr(e, 'groups_mm', [])] if getattr(e, 'groups_mm', None) else []

        user = Users.query.get(e.created_by) if e.created_by else None
        last_edited_user = Users.query.get(e.last_edited_by) if e.last_edited_by else None

        # Process attachments
        attachments = json.loads(e.attachments) if e.attachments else []
        
        exams_list.append({
            "id": e.id,
            "title": e.title,
            "description": e.description,
            "creation_date": e.creation_date.strftime('%Y-%m-%d %I:%M %p') if e.creation_date else None,
            "deadline_date": e.deadline_date.strftime('%Y-%m-%d %I:%M %p') if e.deadline_date else None,
            "groups": groups_names,
            "attachments": attachments,
            "student_whatsapp": e.student_whatsapp,
            "parent_whatsapp": e.parent_whatsapp,
            "status": e.status,
            "close_after_deadline": e.close_after_deadline,
            "submitted_students_count": submitted_count,
            "qualified_students_count": qualified_count,
            "out_of": e.out_of,
            "points": e.points,
            "created_by": user.name if user else None,
            "created_at": e.creation_date.strftime('%Y-%m-%d %I:%M %p') if e.creation_date else None,
            "last_edited_by": last_edited_user.name if last_edited_user else None,
            "last_edited_at": e.last_edited_at.strftime('%Y-%m-%d %I:%M %p') if e.last_edited_at else None,
        })

    return jsonify(exams_list)



@admin.route('/api/exam/<int:exam_id>', methods=["GET"])
def get_exam_data(exam_id):
    """API endpoint to fetch single exam data for editing"""
    exam = get_item_if_admin_can_manage(Assignments, exam_id, current_user)
    if not exam or exam.type != "Exam":
        return jsonify({"success": False, "message": "Exam not found or you do not have permission to view it."}), 404

    existing_attachments = json.loads(exam.attachments) if exam.attachments else []

    user = Users.query.get(exam.created_by) if exam.created_by else None
    last_edited_user = Users.query.get(exam.last_edited_by) if exam.last_edited_by else None
    
    groups_mm = [{"id": g.id, "name": g.name} for g in getattr(exam, 'groups_mm', [])] if getattr(exam, 'groups_mm', None) else []

    exam_data = {
        "id": exam.id,
        "title": exam.title,
        "description": exam.description,
        "deadline_date": exam.deadline_date.strftime('%Y-%m-%dT%H:%M') if exam.deadline_date else None,
        "groups_mm": groups_mm,
        "attachments": existing_attachments,
        "student_whatsapp": exam.student_whatsapp,
        "parent_whatsapp": exam.parent_whatsapp,
        "close_after_deadline": exam.close_after_deadline,
        "status": exam.status,
        "out_of": exam.out_of,
        "points": exam.points,
        "created_by": user.name if user else None,
        "created_at": exam.creation_date.strftime('%Y-%m-%d %I:%M %p') if exam.creation_date else None,
        "last_edited_by": last_edited_user.name if last_edited_user else None,
        "last_edited_at": exam.last_edited_at.strftime('%Y-%m-%d %I:%M %p') if exam.last_edited_at else None,
    }

    return jsonify({"success": True, "exam": exam_data})


@admin.route('/assignments', methods=['GET', 'POST'])
def assignments():
    

    if request.method == "POST":
        if current_user.role != "super_admin":
            flash("You are not allowed to create assignments.", "danger")
            return redirect(url_for("admin.assignments"))
        groups = Groups.query.filter(Groups.id.in_([g.id for g in current_user.managed_groups])).all()

        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        student_whatsapp = request.form.get("student_whatsapp", False)
        if student_whatsapp == "true":
            student_whatsapp = True
        else:
            student_whatsapp = False
        parent_whatsapp = request.form.get("parent_whatsapp", False)
        if parent_whatsapp == "true":
            parent_whatsapp = True
        else:
            parent_whatsapp = False

        #Close after deadline
        close_after_deadline = request.form.get("close_after_deadline", False)
        if close_after_deadline == "true":
            close_after_deadline = True
        else:
            close_after_deadline = False




        
        # out of (full mark)
        out_of = request.form.get("out_of", 0)
        out_of = int(out_of) if str(out_of).isdigit() else 0

        # Check for locked_group_id (from group-specific assignment page)
        locked_group_id = request.form.get("locked_group_id")
        if locked_group_id:
            locked_group_id = int(locked_group_id) if str(locked_group_id).isdigit() else None

        group_ids  = parse_multi_ids("groups[]")

        # If locked_group_id is set, enforce it and ignore any other group selections
        if locked_group_id:
            # Validate that the user can manage this group
            if not can_manage([locked_group_id], [g.id for g in groups]):
                flash("You are not allowed to post to this group.", "danger")
                wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
                if wants_json:
                    return jsonify({"success": False, "message": "You are not allowed to post to this group."}), 403
                return redirect(url_for("admin.assignments"))
            group_ids = [locked_group_id]
        else:
            if not group_ids:
                group_ids = [g.id for g in groups]

            if group_ids:
                if not can_manage(group_ids, [g.id for g in groups]):
                    flash("You are not allowed to post to one or more selected groups.", "danger")
                    return redirect(url_for("admin.assignments"))

        # deadline
        try:
            deadline_date = parse_deadline(request.form.get("deadline_date", ""))
        except (TypeError, ValueError):
            flash("Invalid deadline date. Please use the datetime picker.", "error")
            return redirect(url_for("admin.assignments"))

        # points (int)
        points_raw = request.form.get("points", 0)
        points = int(points_raw) if str(points_raw).isdigit() else 0

        # Process new attachment format
        upload_dir = "website/assignments/uploads/"
        attachments = []
        os.makedirs(upload_dir, exist_ok=True)

        # Get all attachment indices
        attachment_indices = []
        for key in request.form.keys():
            if key.startswith('attachments[') and '][name]' in key:
                index = key.split('[')[1].split(']')[0]
                if index not in attachment_indices:
                    attachment_indices.append(index)
        
        # Process each attachment
        for idx in attachment_indices:
            attachment_name = request.form.get(f'attachments[{idx}][name]')
            attachment_type = request.form.get(f'attachments[{idx}][type]')
            
            if not attachment_name:
                continue
                
            attachment_obj = {
                'name': attachment_name,
                'type': attachment_type
            }
            
            if attachment_type == 'file':
                file = request.files.get(f'attachments[{idx}][file]')
                if file and file.filename:
                    original_filename = secure_filename(file.filename)
                    filename = f"{uuid.uuid4().hex}_{original_filename}"
                    file_path = os.path.join(upload_dir, filename)
                    file.save(file_path)
                    try:
                        with open(file_path, "rb") as f:
                            storage.upload_file(f, folder="assignments/uploads", file_name=filename)
                    except Exception as e:
                        flash(f"Error uploading file to storage: {str(e)}", "danger")
                        return redirect(url_for("admin.assignments"))
                    attachment_obj['url'] = f"/student/assignments/uploads/{filename}"
                    attachments.append(attachment_obj)
            elif attachment_type == 'link':
                attachment_url = request.form.get(f'attachments[{idx}][url]')
                if attachment_url:
                    attachment_obj['url'] = attachment_url
                    attachments.append(attachment_obj)


        # creation date
        cairo_tz = pytz.timezone('Africa/Cairo')
        aware_local_time = datetime.now(cairo_tz)
        naive_local_time = aware_local_time.replace(tzinfo=None)
        # ---- create and persist
        new_assignment = Assignments(
            title=title,
            description=description,
            deadline_date=deadline_date,
            attachments=json.dumps(attachments),
            points=points,
            created_by=current_user.id,
            creation_date=naive_local_time,
            student_whatsapp=student_whatsapp,
            parent_whatsapp=parent_whatsapp,
            out_of=out_of,
            close_after_deadline=close_after_deadline,
        )

        # IMPORTANT: add to session BEFORE assigning M2M relations
        db.session.add(new_assignment)


        new_assignment.groups_mm = Groups.query.filter(Groups.id.in_(group_ids)).all()


        db.session.commit()

        # --- LOGGING: Add log for assignment creation
        try:
            new_log = AssistantLogs(
                assistant_id=current_user.id,
                action='Create',
                log={
                    "action_name": "Create",
                    "resource_type": "assignment",
                    "action_details": {
                        "id": new_assignment.id,
                        "title": new_assignment.title,
                        "summary": f"Assignment '{new_assignment.title}' was created."
                    },
                    "data": {
                        "title": new_assignment.title,
                        "description": new_assignment.description,
                        "deadline_date": str(new_assignment.deadline_date) if new_assignment.deadline_date else None,
                        "groupid": new_assignment.groupid,
                        "groups": [g.id for g in getattr(new_assignment, "groups_mm", [])],
                        "attachments": json.loads(new_assignment.attachments) if new_assignment.attachments else [],
                        "points": new_assignment.points,
                        "out_of": new_assignment.out_of,
                    },
                    "before": None,
                    "after": None
                }
            )
            db.session.add(new_log)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash("Assignment added, but failed to log the action.", "warning")

        # If the client expects JSON (AJAX), return JSON without redirect
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            # Build light payload matching assignments-data schema
            submitted_count = 0
            qualified_count = qualified_students_count_for_assignment(new_assignment)
            response_payload = {
                "id": new_assignment.id,
                "title": new_assignment.title,
                "description": new_assignment.description,
                "creation_date": new_assignment.creation_date.strftime('%Y-%m-%d %H:%M') if new_assignment.creation_date else None,
                "deadline_date": new_assignment.deadline_date.strftime('%Y-%m-%d %H:%M') if new_assignment.deadline_date else None,
                "groups": [g.name for g in getattr(new_assignment, 'groups_mm', [])] if getattr(new_assignment, 'groups_mm', None) else [],
                "status": new_assignment.status,
                "points": new_assignment.points,
                "submitted_students_count": submitted_count,
                "qualified_students_count": qualified_count,
                "out_of": new_assignment.out_of,
                "student_whatsapp": new_assignment.student_whatsapp,
                "parent_whatsapp": new_assignment.parent_whatsapp,
                "close_after_deadline": new_assignment.close_after_deadline,
            }
            return jsonify({"success": True, "message": "Assignment added successfully!", "assignment": response_payload})

        flash("Assignment added successfully!", "success")
        return redirect(url_for("admin.assignments"))


    return render_template(
        'admin/assignments/assignments.html'
    )




@admin.route("/assignments/<int:assignment_id>/submissions", methods=["GET", "POST"])
def view_assignment_submissions(assignment_id):
    assignment = get_item_if_admin_can_manage(Assignments, assignment_id, current_user)
    
    # Get optional group_id from query params for back navigation
    group_id = request.args.get('group_id', type=int)
    group = None
    if group_id:
        group = Groups.query.get(group_id)
    else:
        # If assignment has exactly 1 group, use it; otherwise None
        assignment_groups = list(getattr(assignment, 'groups_mm', []))
        if len(assignment_groups) == 1:
            group_id = assignment_groups[0].id
        else:
            group_id = None


    if not assignment:
        flash("Assignment not found or you do not have permission to view its submissions.", "danger")
        return redirect(url_for("admin.assignments"))

    if not assignment.type == "Assignment":
        # flash("Assignment is not an assignment.", "danger")
        # return redirect(url_for("admin.assignments"))
        return redirect(url_for("admin.view_exam_submissions", exam_id=assignment_id))


    if request.method == "POST":
        submission_id = request.form.get("submission_id")
        mark = request.form.get("mark")
        submission = Submissions.query.get_or_404(submission_id)
        
        # Check if mark is being changed
        mark_changed = submission.mark != mark
        
        submission.mark = mark
        
        # If super_admin is updating, auto-approve and send notifications
        if current_user.role == "super_admin":
            if mark_changed:
                submission.corrected = True
                submission.corrected_by_id = current_user.id
                submission.correction_date = datetime.now(GMT_PLUS_2)
                submission.reviewed = True
                submission.reviewed_by_id = current_user.id
                submission.review_date = datetime.now(GMT_PLUS_2)
                
                # Send WhatsApp notifications immediately
                try:
                    send_whatsapp_message(submission.student.phone_number, 
                        f"Hi {submission.student.name}👋,\n\n"
                        f"*{assignment.title}*\n"
                        f"You're correction is returned please check your account\n\n" 
                        f"You scored : *{submission.mark if submission.mark else 'N/A'}*/{assignment.out_of}\n\n"
                        f"_For further inquiries send to Dr. Adham_"
                    )
                    
                    send_whatsapp_message(
                        submission.student.parent_phone_number, 
                        f"Dear Parent,\n"
                        f"*{submission.student.name}*\n\n"
                        f"Homework *{assignment.title}* due on *{assignment.deadline_date.strftime('%d/%m/%Y') if hasattr(assignment, 'deadline_date') and assignment.deadline_date else 'N/A'}* is returned on the student's account on website\n\n"
                        f"Score : *{submission.mark if submission.mark else 'N/A'}*/{assignment.out_of}\n\n"
                        f"_For further inquiries send to Dr. Adham_"
                    )
                
                
                except:
                    pass
                
                flash("Grade updated and notifications sent!", "success")
            else:
                flash("Grade updated successfully!", "success")
        # If assistant/admin is updating, require review
        else:
            if mark_changed and submission.reviewed:
                submission.reviewed = False
                submission.corrected_by_id = current_user.id
                submission.correction_date = datetime.now(GMT_PLUS_2)
                flash("Grade updated! Awaiting Head review before student notification.", "info")
            elif mark_changed:
                submission.corrected_by_id = current_user.id
                submission.correction_date = datetime.now(GMT_PLUS_2)
                flash("Grade updated! Awaiting Head review.", "success")
            else:
                flash("Grade updated successfully!", "success")
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating grade: {str(e)}", "danger")
        return redirect(url_for("admin.view_assignment_submissions", assignment_id=assignment_id))

    # ✅ Subquery of qualified student IDs (scoped to current admin)
    qualified_students_subq = (
        get_qualified_students_query(assignment, current_user.id)
        .with_entities(Users.id)
        .subquery()
    )

    # ✅ Build submissions query
    submissions_query = (
        Submissions.query
        .join(Users, Submissions.student_id == Users.id)
        .filter(Submissions.assignment_id == assignment_id)
        .filter(Submissions.student_id.in_(db.select(qualified_students_subq)))
    )
    
    # ✅ For assistants (not super_admin), only show assigned submissions
    if current_user.role != "super_admin":
        submissions_query = submissions_query.filter(
            Submissions.assigned_to_id == current_user.id
        )
    
    # ✅ Get submissions sorted alphabetically by student name
    submissions = submissions_query.order_by(Users.name).all()

    # ✅ All qualified students (for template use)
    all_qualified_students = (
        get_qualified_students_query(assignment, current_user.id).all()
    )

    # ✅ Students who have submitted (set of IDs)
    submitted_student_ids = {sub.student_id for sub in submissions}

    # ✅ Students who have NOT submitted (sorted alphabetically)
    not_submitted_students = sorted(
        [student for student in all_qualified_students if student.id not in submitted_student_ids],
        key=lambda s: s.name
    )



    

    whatsapp_notifications = Assignments_whatsapp.query.filter_by(
        assignment_id=assignment_id
    ).all()


    notification_status = {notif.user_id: notif.message_sent for notif in whatsapp_notifications}
    

    # Get assistants managing this group (if group_id is specified)
    assistants = []
    if group_id:
        assistants = Users.query.filter(
            Users.role.in_(['admin']),
            Users.managed_groups.any(Groups.id == group_id)
        ).order_by(Users.name).all()

    return render_template(
        "admin/assignments/assignment_submissions.html", 
        assignment=assignment, 
        submitted_student_ids = submitted_student_ids,
        submissions=submissions,
        not_submitted_students=not_submitted_students,
        notification_status=notification_status,
        group_id=group_id,
        group=group,
        assistants=assistants
    )


#=================================================================
# Assign Submissions to Assistants (Super Admin Only)
#=================================================================
@admin.route("/assignments/<int:assignment_id>/assign-submissions", methods=["POST"])
def assign_submissions_to_assistant(assignment_id):
    """Assign a range of submissions to a specific assistant"""
    if current_user.role != "super_admin":
        flash("Only super admins can assign submissions.", "danger")
        return redirect(url_for("admin.assignments"))
    
    assignment = Assignments.query.get_or_404(assignment_id)
    group_id = request.form.get('group_id', type=int)
    assistant_id = request.form.get('assistant_id', type=int)
    start_index = request.form.get('start_index', type=int)
    end_index = request.form.get('end_index', type=int)
    
    if not assistant_id or start_index is None or end_index is None:
        flash("Please provide all required fields.", "danger")
        return redirect(request.referrer or url_for("admin.view_assignment_submissions", assignment_id=assignment_id))
    
    # Validate assistant
    assistant = Users.query.get(assistant_id)
    if not assistant or assistant.role not in ['admin', 'super_admin']:
        flash("Invalid assistant selected.", "danger")
        return redirect(request.referrer or url_for("admin.view_assignment_submissions", assignment_id=assignment_id))
    
    # Get all submissions for this assignment (sorted by student name to match display order)
    qualified_students_subq = (
        get_qualified_students_query(assignment, current_user.id)
        .with_entities(Users.id)
        .subquery()
    )
    
    submissions = (
        Submissions.query
        .join(Users, Submissions.student_id == Users.id)
        .filter(Submissions.assignment_id == assignment_id)
        .filter(Submissions.student_id.in_(db.select(qualified_students_subq)))
        .order_by(Users.name)
        .all()
    )
    
    # Validate indices
    if start_index < 1 or end_index > len(submissions) or start_index > end_index:
        flash(f"Invalid index range. Must be between 1 and {len(submissions)}.", "danger")
        return redirect(request.referrer or url_for("admin.view_assignment_submissions", assignment_id=assignment_id))
    
    # Assign submissions (convert to 0-based index)
    assigned_count = 0
    for i in range(start_index - 1, end_index):
        submission = submissions[i]
        submission.assigned_to_id = assistant_id
        submission.assignment_date = datetime.now(GMT_PLUS_2)
        assigned_count += 1
    
    try:
        db.session.commit()
        flash(f"Successfully assigned {assigned_count} submissions to {assistant.name}!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error assigning submissions: {str(e)}", "danger")
    
    return redirect(url_for("admin.view_assignment_submissions", assignment_id=assignment_id, group_id=group_id))


@admin.route("/assignments/<int:assignment_id>/unassign-submissions", methods=["POST"])
def unassign_submissions(assignment_id):
    """Unassign submissions from an assistant"""
    if current_user.role != "super_admin":
        flash("Only super admins can unassign submissions.", "danger")
        return redirect(url_for("admin.assignments"))
    
    assistant_id = request.form.get('assistant_id', type=int)
    group_id = request.form.get('group_id', type=int)
    
    if not assistant_id:
        flash("Please select an assistant.", "danger")
        return redirect(request.referrer or url_for("admin.view_assignment_submissions", assignment_id=assignment_id))
    
    # Unassign all submissions for this assistant in this assignment
    updated = Submissions.query.filter_by(
        assignment_id=assignment_id,
        assigned_to_id=assistant_id
    ).update({
        'assigned_to_id': None,
        'assignment_date': None
    })
    
    try:
        db.session.commit()
        flash(f"Successfully unassigned {updated} submissions!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error unassigning submissions: {str(e)}", "danger")
    
    return redirect(url_for("admin.view_assignment_submissions", assignment_id=assignment_id, group_id=group_id))


#Get original Pdff
@admin.route("/assignments/annotate/<int:submission_id>")
def edit_pdf(submission_id):
    submission = Submissions.query.get_or_404(submission_id)
    pdfurl = f"/admin/getpdf/{submission_id}"
    filename = submission.file_url

    assignment = Assignments.query.get(submission.assignment_id)
    student_name = submission.student.name
    #take only first 2 names then truntcate 
    student_name = student_name.split(" ")[0] + " " + student_name.split(" ")[1]
    student_name = student_name[:20] + "..."

    if assignment.out_of > 0:
        show_grade = True
    else:
        show_grade = False
    return render_template("admin/editpdf.html", pdfurl=pdfurl, filename=filename , submission_id=submission_id, show_grade=show_grade, student_name=student_name)



@admin.route("/assignments/annotate2/<int:submission_id>")
def edit_pdf2(submission_id):
    submission = Submissions.query.get_or_404(submission_id)
    pdfurl = f"/admin/getpdf2/{submission_id}"
    filename = submission.file_url

    assignment = Assignments.query.get(submission.assignment_id)
    student_name = submission.student.name
    #take only first 2 names then truntcate 
    student_name = student_name.split(" ")[0] + " " + student_name.split(" ")[1]
    student_name = student_name[:20] + "..."
    if assignment.out_of > 0:
        show_grade = True
    else:
        show_grade = False
    return render_template("admin/editpdf.html", pdfurl=pdfurl, filename=filename , submission_id=submission_id, show_grade=show_grade, student_name=student_name)





#Save new pdf and grade
@admin.route("/assignments/annotate", methods=["POST"])
def save_pdf():
    submission_id = request.form.get("submission_id")
    grade = request.form.get("grade")
    
    # Handle chunked upload
    file_chunk = request.files.get("file_chunk")
    filename_from_form = request.form.get("filename")
    if not filename_from_form:
        return jsonify({"error": "Missing filename."}), 400
    
    original_filename = secure_filename(filename_from_form)
    # Convert to float first, then to int to handle decimal values from JavaScript
    offset = int(float(request.form.get("offset", 0)))
    total_size = int(float(request.form.get("total_size", 0)))

    if not file_chunk or not original_filename:
        return jsonify({"error": "Missing file data."}), 400

    submission = Submissions.query.get_or_404(submission_id)
    student_id = submission.student_id
    folder = f"student_{student_id}"
    
    # Create temp folder for chunked upload
    temp_folder = os.path.join("website", "submissions", "uploads", folder, "temp")
    os.makedirs(temp_folder, exist_ok=True)
    
    temp_filename = f"annotated_{submission_id}_{original_filename}.part"
    temp_file_path = os.path.join(temp_folder, temp_filename)
    
    chunk_data = file_chunk.read()
    chunk_size = len(chunk_data)

    try:
        with open(temp_file_path, "ab") as f:
            f.seek(offset)
            f.write(chunk_data)
    except IOError as e:
        return jsonify({"status": "error", "error": f"Could not write to file: {e}", "action": "restart_upload"}), 500

    # If this is the final chunk, finalize the upload
    if offset + chunk_size >= total_size:
        # Verify final file size
        final_temp_size = os.path.getsize(temp_file_path)
        if final_temp_size != total_size:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            return jsonify({"status": "error", "error": "Final size mismatch", "action": "restart_upload"}), 400
        
        # Validate file extension
        ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
        if ext != "pdf":
            os.remove(temp_file_path)
            return jsonify({"status": "error", "error": "Only PDF files are allowed for annotations", "action": "invalid_file_type"}), 400

        # Create final filename based on assignment type
        assignment = Assignments.query.get(submission.assignment_id)
        original_submission_filename = submission.file_url
        name, ext = os.path.splitext(original_submission_filename)
        
        annotated_filename = f"{name}_annotated{ext}"
        
        # Move temp file to final location
        final_folder = os.path.join("website", "submissions", "uploads", folder)
        os.makedirs(final_folder, exist_ok=True)
        final_file_path = os.path.join(final_folder, annotated_filename)
        
        # Remove existing file if it exists before renaming
        if os.path.exists(final_file_path):
            try:
                os.remove(final_file_path)
            except Exception as e:
                pass
        
        try:
            os.rename(temp_file_path, final_file_path)
        except Exception as e:
            return jsonify({"status": "error", "error": f"Failed to finalize upload: {str(e)}", "action": "restart_upload"}), 500
        
        # Clean up temp file if it still exists
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as e:
                pass

        # Upload to storage
        try:
            with open(final_file_path, "rb") as data:
                storage.upload_file(data, f"submissions/uploads/student_{student_id}", annotated_filename)
        except Exception as e:
            try:
                os.remove(final_file_path)
            except:
                pass
            return jsonify({"status": "error", "error": f"Error uploading to storage: {str(e)}", "action": "restart_upload"}), 500

        # Save grade and mark as corrected
        if grade is not None and grade != '':
            submission.mark = grade
        
        # Mark submission as corrected
        submission.corrected = True
        submission.corrected_by_id = current_user.id
        submission.correction_date = datetime.now(GMT_PLUS_2)
        
        # If super_admin is correcting, auto-approve
        if current_user.role == "super_admin":
            submission.reviewed = True
            submission.reviewed_by_id = current_user.id
            submission.review_date = datetime.now(GMT_PLUS_2)
        else:
            submission.reviewed = False  # Not reviewed yet, will be reviewed by Head
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({'status': 'error', 'error': f'Failed to save grade: {str(e)}', 'action': 'restart_upload'}), 500

        assignment = Assignments.query.get(submission.assignment_id)
        if assignment.type == "Exam":
            redirect_url = url_for("admin.view_exam_submissions", exam_id=submission.assignment_id)
        else:
            redirect_url = url_for("admin.view_assignment_submissions", assignment_id=submission.assignment_id)

        # If super_admin uploaded the correction, send notifications immediately
        if current_user.role == "super_admin":
            try:
                send_whatsapp_message(submission.student.phone_number, 
                    f"Hi *{submission.student.name}*👋,\n\n"
                    f"*{assignment.title}*\n"
                    f"You're correction is returned please check your account\n\n" 
                    f"You scored : *{submission.mark if submission.mark else 'N/A'}*/{assignment.out_of}\n\n"
                    f"_For further inquiries send to Dr. Adham_"
                )
                    
                send_whatsapp_message(
                    submission.student.parent_phone_number, 
                    f"Dear Parent,\n"
                    f"*{submission.student.name}*\n\n"
                    f"Homework *{assignment.title}* due on *{assignment.deadline_date.strftime('%d/%m/%Y') if hasattr(assignment, 'deadline_date') and assignment.deadline_date else 'N/A'}* is returned on the student's account on website\n\n"
                    f"Score : *{submission.mark if submission.mark else 'N/A'}*/{assignment.out_of}\n\n"
                    f"_For further inquiries send to Dr. Adham_"
                )
            
            
            except:
                pass
            return jsonify({'status': 'success', 'message': 'Corrected PDF uploaded and approved! Notifications sent.', 'action': 'upload_complete', 'redirect_url': redirect_url}), 200
        else:
            return jsonify({'status': 'success', 'message': 'Correction saved! Awaiting Head review.', 'action': 'upload_complete', 'redirect_url': redirect_url}), 200

    # Return success for chunk received (intermediate chunk)
    return jsonify({"status": "success", "message": "Chunk received", "action": "continue_upload"}), 200

#Get original pdf (backend route for annotation)
@admin.route("/getpdf/<int:submission_id>")
def get_pdf(submission_id):

    submission = Submissions.query.get_or_404(submission_id)
    folder = f"student_{submission.student_id}"
    filename = submission.file_url
    file_path = os.path.join("website/submissions/uploads", folder, filename)

    if not os.path.isfile(file_path):
        try:
            storage.download_file(
                folder=f"submissions/uploads/student_{submission.student_id}",
                file_name=filename,
                local_path=file_path
            )
        except Exception as e:
            flash(f'Error downloading file: {str(e)}', 'danger')
            flash('File not found!', 'danger')
            return redirect(url_for("admin.view_assignment_submissions", assignment_id=submission.assignment_id))

    return send_from_directory(os.path.join("submissions/uploads", folder), filename)


#Get annotated pdf
@admin.route("/getpdf2/<int:submission_id>")
def get_pdf2(submission_id):

    submission = Submissions.query.get_or_404(submission_id)
    folder = f"student_{submission.student_id}"
    filename = submission.file_url 

    filename = filename.replace(".pdf", "_annotated.pdf")



    return send_from_directory(os.path.join("submissions/uploads", folder), filename)


# Upload corrected PDF with chunked upload (new route)
@admin.route("/upload-corrected-pdf-chunk/<int:submission_id>", methods=["POST"])
def upload_corrected_pdf_chunk(submission_id):
    """
    Handles uploading a corrected PDF in chunks.
    When the last chunk is received, it finalizes the upload and saves the grade.
    """
    if not current_user.is_authenticated:
        return jsonify({
            "status": "error",
            "error": "Authentication required",
            "action": "redirect_login"
        }), 401
    
    submission = Submissions.query.get_or_404(submission_id)
    
    # Get chunk data from the request
    file_chunk = request.files.get("file_chunk")
    filename_from_form = request.form.get("filename")
    mark = request.form.get("mark")
    
    if not filename_from_form:
        return jsonify({
            "status": "error",
            "error": "Missing filename.",
            "action": "restart_upload"
        }), 400
    
    original_filename = secure_filename(filename_from_form)
    offset = int(float(request.form.get("offset", 0)))
    total_size = int(float(request.form.get("total_size", 0)))

    if not file_chunk or not original_filename:
        return jsonify({
            "status": "error",
            "error": "Missing file data.",
            "action": "restart_upload"
        }), 400
    
    # Validate PDF file type
    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if ext != "pdf":
        return jsonify({
            "status": "error",
            "error": "Only PDF files are allowed for corrected submissions.",
            "action": "invalid_file_type"
        }), 400

    # Define temp path for the file being assembled
    student_id = submission.student_id
    folder = f"student_{student_id}"
    temp_folder = os.path.join("website", "submissions", "uploads", folder, "temp")
    os.makedirs(temp_folder, exist_ok=True)
    
    temp_filename = f"corrected_{submission_id}_{original_filename}.part"
    temp_file_path = os.path.join(temp_folder, temp_filename)
    
    # Read the chunk
    chunk_data = file_chunk.read()
    chunk_size = len(chunk_data)
    
    # Verify chunk integrity
    current_file_size = 0
    if os.path.exists(temp_file_path):
        current_file_size = os.path.getsize(temp_file_path)
        
        if current_file_size != offset:
            if current_file_size < offset:
                return jsonify({
                    "status": "error",
                    "error": "Upload corrupted - missing chunks detected",
                    "action": "restart_upload",
                    "current_offset": current_file_size,
                    "expected_offset": offset
                }), 400
            
            elif current_file_size > offset:
                try:
                    with open(temp_file_path, "r+b") as f:
                        f.truncate(offset)
                    return jsonify({
                        "status": "warning",
                        "message": "Chunk overlap detected, file truncated",
                        "action": "retry_chunk",
                        "retry_offset": offset
                    }), 200
                except IOError as e:
                    try:
                        os.remove(temp_file_path)
                    except:
                        pass
                    return jsonify({
                        "status": "error",
                        "error": "Failed to recover from corruption",
                        "action": "restart_upload"
                    }), 500
    
    # Append the chunk to the temporary file
    try:
        with open(temp_file_path, "ab") as f:
            f.seek(offset)
            f.write(chunk_data)
        
        # Verify the write was successful
        new_file_size = os.path.getsize(temp_file_path)
        expected_size = offset + chunk_size
        
        if new_file_size != expected_size:
            return jsonify({
                "status": "error",
                "error": "Chunk write verification failed",
                "action": "retry_chunk",
                "retry_offset": offset
            }), 500
            
    except IOError as e:
        return jsonify({
            "status": "error",
            "error": f"Could not write to file: {str(e)}",
            "action": "restart_upload",
            "details": str(e)
        }), 500
    
    # Finalization Logic - Check if this was the last chunk
    if offset + chunk_size >= total_size:
        # Final file size verification
        final_temp_size = os.path.getsize(temp_file_path)
        if final_temp_size != total_size:
            try:
                os.remove(temp_file_path)
            except:
                pass
            return jsonify({
                "status": "error",
                "error": "Upload corrupted - final size mismatch",
                "action": "restart_upload",
                "expected_size": total_size,
                "actual_size": final_temp_size
            }), 400
        
        # Create final filename based on original submission
        original_submission_filename = submission.file_url
        name, ext = os.path.splitext(original_submission_filename)
        annotated_filename = f"{name}_annotated{ext}"
        
        # Move temp file to final location
        final_folder = os.path.join("website", "submissions", "uploads", folder)
        os.makedirs(final_folder, exist_ok=True)
        final_file_path = os.path.join(final_folder, annotated_filename)
        
        # Remove existing corrected file if it exists
        if os.path.exists(final_file_path):
            try:
                os.remove(final_file_path)
            except Exception as e:
                pass
        
        try:
            os.rename(temp_file_path, final_file_path)
        except Exception as e:
            try:
                os.remove(temp_file_path)
            except:
                pass
            return jsonify({
                "status": "error",
                "error": "Failed to finalize file",
                "action": "restart_upload",
                "details": str(e)
            }), 500
        
        # Clean up temp file if it still exists
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as e:
                pass
        
        # Upload to storage
        try:
            with open(final_file_path, "rb") as data:
                storage.upload_file(data, f"submissions/uploads/student_{student_id}", annotated_filename)
        except Exception as e:
            try:
                os.remove(final_file_path)
            except:
                pass
            return jsonify({
                "status": "error",
                "error": f"Error uploading to storage: {str(e)}",
                "action": "restart_upload"
            }), 500
        
        # Save grade and mark as corrected (but not reviewed yet)
        if submission.assignment.out_of > 0:
            if mark is not None and mark.strip():
                submission.mark = mark
        
        submission.corrected = True
        submission.corrected_by_id = current_user.id
        submission.correction_date = datetime.now(GMT_PLUS_2)
        submission.reviewed = False  # Not reviewed yet, will be reviewed by super_admin
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({
                'status': 'error',
                'error': f'Failed to save correction: {str(e)}',
                'action': 'restart_upload'
            }), 500
        
        # NOTE: WhatsApp notifications are now sent only after super_admin reviews and approves
        
        return jsonify({
            'status': 'success',
            'action': 'upload_complete',
            'message': 'Corrected PDF uploaded successfully! Awaiting Head review.'
        }), 200
    
    # Return success for chunk received
    return jsonify({
        "status": "success",
        "action": "continue",
        "message": "Chunk received."
    }), 200


# Upload corrected EXAM PDF with chunked upload (new route for exams)
@admin.route("/upload-corrected-exam-pdf-chunk/<int:submission_id>", methods=["POST"])
def upload_corrected_exam_pdf_chunk(submission_id):
    """
    Handles uploading a corrected exam PDF in chunks.
    When the last chunk is received, it finalizes the upload and saves the grade.
    """
    if not current_user.is_authenticated:
        return jsonify({
            "status": "error",
            "error": "Authentication required",
            "action": "redirect_login"
        }), 401
    
    submission = Submissions.query.get_or_404(submission_id)
    
    # Get chunk data from the request
    file_chunk = request.files.get("file_chunk")
    filename_from_form = request.form.get("filename")
    mark = request.form.get("mark")
    
    if not filename_from_form:
        return jsonify({
            "status": "error",
            "error": "Missing filename.",
            "action": "restart_upload"
        }), 400
    
    original_filename = secure_filename(filename_from_form)
    offset = int(float(request.form.get("offset", 0)))
    total_size = int(float(request.form.get("total_size", 0)))

    if not file_chunk or not original_filename:
        return jsonify({
            "status": "error",
            "error": "Missing file data.",
            "action": "restart_upload"
        }), 400
    
    # Validate PDF file type
    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if ext != "pdf":
        return jsonify({
            "status": "error",
            "error": "Only PDF files are allowed for corrected submissions.",
            "action": "invalid_file_type"
        }), 400

    # Define temp path for the file being assembled
    student_id = submission.student_id
    folder = f"student_{student_id}"
    temp_folder = os.path.join("website", "submissions", "uploads", folder, "temp")
    os.makedirs(temp_folder, exist_ok=True)
    
    temp_filename = f"corrected_exam_{submission_id}_{original_filename}.part"
    temp_file_path = os.path.join(temp_folder, temp_filename)
    
    # Read the chunk
    chunk_data = file_chunk.read()
    chunk_size = len(chunk_data)
    
    # Verify chunk integrity
    current_file_size = 0
    if os.path.exists(temp_file_path):
        current_file_size = os.path.getsize(temp_file_path)
        
        if current_file_size != offset:
            if current_file_size < offset:
                return jsonify({
                    "status": "error",
                    "error": "Upload corrupted - missing chunks detected",
                    "action": "restart_upload",
                    "current_offset": current_file_size,
                    "expected_offset": offset
                }), 400
            
            elif current_file_size > offset:
                try:
                    with open(temp_file_path, "r+b") as f:
                        f.truncate(offset)
                    return jsonify({
                        "status": "warning",
                        "message": "Chunk overlap detected, file truncated",
                        "action": "retry_chunk",
                        "retry_offset": offset
                    }), 200
                except IOError as e:
                    try:
                        os.remove(temp_file_path)
                    except:
                        pass
                    return jsonify({
                        "status": "error",
                        "error": "Failed to recover from corruption",
                        "action": "restart_upload"
                    }), 500
    
    # Append the chunk to the temporary file
    try:
        with open(temp_file_path, "ab") as f:
            f.seek(offset)
            f.write(chunk_data)
        
        # Verify the write was successful
        new_file_size = os.path.getsize(temp_file_path)
        expected_size = offset + chunk_size
        
        if new_file_size != expected_size:
            return jsonify({
                "status": "error",
                "error": "Chunk write verification failed",
                "action": "retry_chunk",
                "retry_offset": offset
            }), 500
            
    except IOError as e:
        return jsonify({
            "status": "error",
            "error": f"Could not write to file: {str(e)}",
            "action": "restart_upload",
            "details": str(e)
        }), 500
    
    # Finalization Logic - Check if this was the last chunk
    if offset + chunk_size >= total_size:
        # Final file size verification
        final_temp_size = os.path.getsize(temp_file_path)
        if final_temp_size != total_size:
            try:
                os.remove(temp_file_path)
            except:
                pass
            return jsonify({
                "status": "error",
                "error": "Upload corrupted - final size mismatch",
                "action": "restart_upload",
                "expected_size": total_size,
                "actual_size": final_temp_size
            }), 400
        
        # Create final filename based on original submission
        original_submission_filename = submission.file_url
        name, ext = os.path.splitext(original_submission_filename)
        annotated_filename = f"{name}_annotated{ext}"
        
        # Move temp file to final location
        final_folder = os.path.join("website", "submissions", "uploads", folder)
        os.makedirs(final_folder, exist_ok=True)
        final_file_path = os.path.join(final_folder, annotated_filename)
        
        # Remove existing corrected file if it exists
        if os.path.exists(final_file_path):
            try:
                os.remove(final_file_path)
            except Exception as e:
                pass
        
        try:
            os.rename(temp_file_path, final_file_path)
        except Exception as e:
            try:
                os.remove(temp_file_path)
            except:
                pass
            return jsonify({
                "status": "error",
                "error": "Failed to finalize file",
                "action": "restart_upload",
                "details": str(e)
            }), 500
        
        # Clean up temp file if it still exists
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as e:
                pass
        
        # Upload to storage
        try:
            with open(final_file_path, "rb") as data:
                storage.upload_file(data, f"submissions/uploads/student_{student_id}", annotated_filename)
        except Exception as e:
            try:
                os.remove(final_file_path)
            except:
                pass
            return jsonify({
                "status": "error",
                "error": f"Error uploading to storage: {str(e)}",
                "action": "restart_upload"
            }), 500
        
        # Save grade and mark as corrected
        if mark is not None and mark.strip():
            submission.mark = mark
        
        submission.corrected = True
        submission.corrected_by_id = current_user.id
        submission.correction_date = datetime.now(GMT_PLUS_2)
        
        # If super_admin is correcting, auto-approve
        if current_user.role == "super_admin":
            submission.reviewed = True
            submission.reviewed_by_id = current_user.id
            submission.review_date = datetime.now(GMT_PLUS_2)
        else:
            submission.reviewed = False  # Not reviewed yet, will be reviewed by Head
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({
                'status': 'error',
                'error': f'Failed to save correction: {str(e)}',
                'action': 'restart_upload'
            }), 500
        
        # If super_admin uploaded the correction, send notifications immediately
        if current_user.role == "super_admin":
            exam = Assignments.query.get(submission.assignment_id)
            try:
                send_whatsapp_message(submission.student.phone_number, 
                    f"Hi {submission.student.name}👋,\n\n"
                    f"*{exam.title}*\n"
                    f"You're correction is returned please check your account\n\n"
                    f"You scored : *{submission.mark if submission.mark else 'N/A'}* / {exam.out_of if hasattr(exam, 'out_of') else 'N/A'}\n\n"
                    f"_for further inquiries send to Dr. Adham_"
                )
                
                send_whatsapp_message(
                    submission.student.parent_phone_number, 
                    f"Dear Parent,\n"
                    f"*{submission.student.name}*\n\n"
                    f"Quiz *{exam.title}* on {exam.deadline_date.strftime('%d/%m/%Y') if hasattr(exam, 'deadline_date') and exam.deadline_date else 'N/A'} correction is returned on the student's account on website\n\n"
                    f"Scored *{submission.mark if submission.mark else 'N/A'}* / {exam.out_of if hasattr(exam, 'out_of') else 'N/A'}\n\n"
                    f"Dr. Adham will send the gradings on the group\n_For further inquiries send to Dr. Adham_"
                )
            
            
            except:
                pass
            return jsonify({
                'status': 'success',
                'action': 'upload_complete',
                'message': 'Corrected exam PDF uploaded and approved! Notifications sent.'
            }), 200
        else:
            # NOTE: WhatsApp notifications are now sent only after super_admin reviews and approves
            return jsonify({
                'status': 'success',
                'action': 'upload_complete',
                'message': 'Corrected exam PDF uploaded successfully! Awaiting Head review.'
            }), 200
    
    # Return success for chunk received
    return jsonify({
        "status": "success",
        "action": "continue",
        "message": "Chunk received."
    }), 200


#Send late message for a submission (Per student) (Admin route)
@admin.route("/assignments/<int:assignment_id>/submissions/<int:student_id>/late", methods=["POST"])
def send_late_message_for_submission(assignment_id, student_id):
    assignment = Assignments.query.get_or_404(assignment_id)
    student = Users.query.get(student_id)
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("admin.view_assignment_submissions", assignment_id=assignment_id))

    # Check if message was already sent
    existing_notification = Assignments_whatsapp.query.filter_by(
        assignment_id=assignment_id,
        user_id=student_id
    ).first()

    if existing_notification and existing_notification.message_sent:
        return jsonify({'status': 'info', 'message': 'Message was already sent to this student.'})

    student_late_message_sent = False
    parent_late_message_sent = False


    if not student.student_whatsapp and not student.parent_whatsapp:
        return jsonify({'status': 'info', 'message': 'Student has no WhatsApp number or parent WhatsApp number.'})


    try:

        send_whatsapp_message(student.phone_number, 
            f"HI *{student.name}*\n\n"
            f"*{assignment.title}*\n"
            f"Submission is missing\n"
            f"Didn't submit\n\n"
            f"Please take care to submit your future assignments"
        )
        student_late_message_sent = True

        send_whatsapp_message(
            student.parent_phone_number,
            f"Dear Parent,\n"
            f"*{student.name}*\n\n"
            f"Homework *{assignment.title}* due on *{assignment.deadline_date.strftime('%d/%m/%Y') if hasattr(assignment, 'deadline_date') and assignment.deadline_date else 'N/A'}*: Did not submit\n\n"
            f"Please take care starting next submission\n\n"
            f"_For further inquiries send to Dr. Adham_"
        )
        parent_late_message_sent = True
        
        # Record the sent message
        if student_late_message_sent or parent_late_message_sent:
            if existing_notification:
                existing_notification.message_sent = True
                existing_notification.sent_date = datetime.now(GMT_PLUS_2)
            else:
                new_notification = Assignments_whatsapp(
                    assignment_id=assignment_id,
                    user_id=student_id,
                    message_sent=True
                )
                db.session.add(new_notification)
            db.session.commit()

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'Error sending WhatsApp message: {str(e)}'})

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'success', 'message': 'Reminder sent successfully!', 'student_late_message_sent': student_late_message_sent, 'parent_late_message_sent': parent_late_message_sent})

# Bulk send reminders for assignments (Admin route)
@admin.route("/assignments/<int:assignment_id>/bulk_send_reminders", methods=["POST"])
def bulk_send_reminders_assignments(assignment_id):
    if current_user.role != "super_admin":
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    
    assignment = Assignments.query.get_or_404(assignment_id)
    data = request.get_json()
    student_ids = data.get('student_ids', [])
    
    if not student_ids:
        return jsonify({'status': 'error', 'message': 'No students selected'}), 400
    
    sent_count = 0
    skipped_count = 0
    
    for student_id in student_ids:
        student = Users.query.get(student_id)
        if not student:
            skipped_count += 1
            continue
        
        # Check if already sent
        existing_notification = Assignments_whatsapp.query.filter_by(
            assignment_id=assignment_id,
            user_id=student_id
        ).first()
        
        if existing_notification and existing_notification.message_sent:
            skipped_count += 1
            continue
        
        # Check if student has WhatsApp numbers
        if not student.student_whatsapp and not student.parent_whatsapp:
            skipped_count += 1
            continue
        
        try:
            # Send to student
            if student.student_whatsapp:
                send_whatsapp_message(student.phone_number, 
                    f"HI *{student.name}*\n\n"
                    f"*{assignment.title}*\n"
                    f"Submission is missing\n"
                    f"Didn't submit\n\n"
                    f"Please take care to submit your future assignments"
                )
            
            # Send to parent
            if student.parent_whatsapp:
                send_whatsapp_message(
                    student.parent_phone_number,
                    f"Dear Parent,\n"
                    f"*{student.name}*\n\n"
                    f"Homework *{assignment.title}* due on *{assignment.deadline_date.strftime('%d/%m/%Y') if hasattr(assignment, 'deadline_date') and assignment.deadline_date else 'N/A'}*: Did not submit\n\n"
                    f"Please take care starting next submission\n\n"
                    f"_For further inquiries send to Dr. Adham_"
                )
            
            # Record the sent message
            if existing_notification:
                existing_notification.message_sent = True
                existing_notification.sent_date = datetime.now(GMT_PLUS_2)
            else:
                new_notification = Assignments_whatsapp(
                    assignment_id=assignment_id,
                    user_id=student_id,
                    message_sent=True
                )
                db.session.add(new_notification)
            
            sent_count += 1
            
        except Exception as e:
            print(f"Error sending to student {student_id}: {str(e)}")
            skipped_count += 1
    
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'Database error: {str(e)}'}), 500
    
    return jsonify({
        'status': 'success',
        'sent_count': sent_count,
        'skipped_count': skipped_count
    })

# Send reminders by range for assignments (Admin route)
@admin.route("/assignments/<int:assignment_id>/send_reminders_by_range", methods=["POST"])
def send_reminders_by_range_assignments(assignment_id):
    if current_user.role != "super_admin":
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    
    assignment = Assignments.query.get_or_404(assignment_id)
    data = request.get_json()
    from_index = data.get('from_index')
    to_index = data.get('to_index')
    
    if from_index is None or to_index is None:
        return jsonify({'status': 'error', 'message': 'Invalid range'}), 400
    
    # Get all students who didn't submit
    submitted_student_ids = [sub.student_id for sub in assignment.submissions]
    
    # Build query for students who qualify for this assignment but haven't submitted
    mm_group_ids = [g.id for g in getattr(assignment, "groups_mm", [])]
    
    base_filters = [
        ~Users.id.in_(submitted_student_ids),
        Users.role == "student",
        Users.code != 'nth',
        Users.code != 'Nth',
    ]
    
    # Apply group filter
    if mm_group_ids:
        base_filters.append(Users.groups.any(Groups.id.in_(mm_group_ids)))
    elif assignment.groupid:
        base_filters.append(Users.groupid == assignment.groupid)
    
    not_submitted_students = Users.query.filter(and_(*base_filters)).order_by(Users.id).all()
    
    # Convert to 0-indexed
    from_idx = from_index - 1
    to_idx = to_index
    
    # Get the students in the range
    students_in_range = not_submitted_students[from_idx:to_idx]
    
    sent_count = 0
    skipped_count = 0
    
    for student in students_in_range:
        # Check if already sent
        existing_notification = Assignments_whatsapp.query.filter_by(
            assignment_id=assignment_id,
            user_id=student.id
        ).first()
        
        if existing_notification and existing_notification.message_sent:
            skipped_count += 1
            continue
        
        # Check if student has WhatsApp numbers
        if not student.student_whatsapp and not student.parent_whatsapp:
            skipped_count += 1
            continue
        
        try:
            # Send to student
            if student.student_whatsapp:
                send_whatsapp_message(student.phone_number, 
                    f"HI *{student.name}*\n\n"
                    f"*{assignment.title}*\n"
                    f"Submission is missing\n"
                    f"Didn't submit\n\n"
                    f"Please take care to submit your future assignments"
                )
            
            # Send to parent
            if student.parent_whatsapp:
                send_whatsapp_message(
                    student.parent_phone_number,
                    f"Dear Parent,\n"
                    f"*{student.name}*\n\n"
                    f"Homework *{assignment.title}* due on *{assignment.deadline_date.strftime('%d/%m/%Y') if hasattr(assignment, 'deadline_date') and assignment.deadline_date else 'N/A'}*: Did not submit\n\n"
                    f"Please take care starting next submission\n\n"
                    f"_For further inquiries send to Dr. Adham_"
                )
            
            # Record the sent message
            if existing_notification:
                existing_notification.message_sent = True
                existing_notification.sent_date = datetime.now(GMT_PLUS_2)
            else:
                new_notification = Assignments_whatsapp(
                    assignment_id=assignment_id,
                    user_id=student.id,
                    message_sent=True
                )
                db.session.add(new_notification)
            
            sent_count += 1
            
        except Exception as e:
            print(f"Error sending to student {student.id}: {str(e)}")
            skipped_count += 1
    
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'Database error: {str(e)}'}), 500
    
    return jsonify({
        'status': 'success',
        'sent_count': sent_count,
        'skipped_count': skipped_count
    })

#Delete a submission for student (Admin route)
@admin.route("/assignments/delete_submission/<int:submission_id>", methods=["POST"])
def delete_submission(submission_id):

    if current_user.role != "super_admin":
        flash("You are not allowed to delete student's submissions.", "danger")
        return redirect(url_for("admin.view_assignment_submissions", assignment_id=submission.assignment_id))


    submission = Submissions.query.get_or_404(submission_id)
    assignment = Assignments.query.get(submission.assignment_id)
    if not assignment:
        flash("Assignment not found.", "danger")
        return redirect(url_for("admin.view_assignment_submissions", assignment_id=submission.assignment_id))


    if assignment.type == "Exam":
        flash("You can't delete a submission for an exam.", "danger")
        return redirect(url_for("admin.view_assignment_submissions", assignment_id=submission.assignment_id))

    try:

        # Delete all upload status records for this submission
        Upload_status.query.filter_by(
            assignment_id=submission.assignment_id,
            user_id=submission.student_id
        ).delete()


        deadline_date = assignment.deadline_date
        upload_time = submission.upload_time

        if hasattr(deadline_date, 'tzinfo') and deadline_date.tzinfo is not None:
            if upload_time.tzinfo is None:
                upload_time = GMT_PLUS_2.localize(upload_time)
        else:
            if upload_time.tzinfo is not None:
                upload_time = upload_time.replace(tzinfo=None)

        if deadline_date > upload_time:
            if assignment.points:
                submission.student.points = (submission.student.points or 0) - assignment.points
        else:
            if assignment.points:
                submission.student.points = (submission.student.points or 0) - (assignment.points / 2)

        # Delete file from local storage
        local_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url)
        if os.path.exists(local_path):
            os.remove(local_path)

        try :
            local_path2 = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url.replace(".pdf", "_annotated.pdf"))
            if os.path.exists(local_path2):
                os.remove(local_path2)
            storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url.replace(".pdf", "_annotated.pdf"))
        except Exception:
            pass
        # Delete file from remote storage
        try:
            storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url)
        except Exception:
            flash("Error deleting file from storage.", "warning")
        db.session.delete(submission)
        db.session.commit()
        flash("Submission deleted successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"An error occurred while deleting the submission: {str(e)}", "danger")

    return redirect(url_for("admin.view_assignment_submissions", assignment_id=assignment.id))


#Delete attachment from assignment
@admin.route('/assignments/delete-attachment/<int:assignment_id>/<int:attachment_index>', methods=['POST'])
def delete_assignment_attachment(assignment_id, attachment_index):
    if current_user.role != "super_admin":
        return jsonify({"success": False, "message": "You are not allowed to delete attachments."}), 403
    
    assignment = get_item_if_admin_can_manage(Assignments, assignment_id, current_user)
    if not assignment:
        return jsonify({"success": False, "message": "Assignment not found or you do not have permission to edit it."}), 404
    
    try:
        existing_attachments = json.loads(assignment.attachments) if assignment.attachments else []
        
        if 0 <= attachment_index < len(existing_attachments):
            # If it's a file attachment, try to delete the file
            attachment = existing_attachments[attachment_index]
            if isinstance(attachment, dict) and attachment.get('type') == 'file':
                # Extract filename from URL
                url = attachment.get('url', '')
                if '/student/assignments/uploads/' in url:
                    filename = url.split('/student/assignments/uploads/')[-1]
                    file_path = os.path.join("website/assignments/uploads", filename)
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            try : 
                                storage.delete_file(folder="assignments/uploads", file_name=filename)
                            except Exception:
                                pass
                        except Exception:
                            pass  # Continue even if file deletion fails
            
            # Remove the attachment from the list
            deleted_attachment = existing_attachments.pop(attachment_index)
            
            # Update the assignment
            assignment.attachments = json.dumps(existing_attachments)
            assignment.last_edited_by = current_user.id
            cairo_tz = pytz.timezone('Africa/Cairo')
            aware_local_time = datetime.now(cairo_tz)
            naive_local_time = aware_local_time.replace(tzinfo=None)
            assignment.last_edited_at = naive_local_time
            
            db.session.commit()
            
            # Log the action
            new_log = AssistantLogs(
                assistant_id=current_user.id,
                action='Delete Attachment',
                log={
                    "action_name": "Delete Attachment",
                    "resource_type": "assignment",
                    "action_details": {
                        "id": assignment.id,
                        "title": assignment.title,
                        "summary": f"Attachment deleted from assignment '{assignment.title}'.",
                        "deleted_attachment": deleted_attachment
                    }
                }
            )
            db.session.add(new_log)
            db.session.commit()
            
            return jsonify({"success": True, "message": "Attachment deleted successfully"})
        else:
            return jsonify({"success": False, "message": "Invalid attachment index"}), 400
            
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error deleting attachment: {str(e)}"}), 500


#Delete attachment from assignment (for group-filtered pages)
@admin.route('/group/<int:group_id>/assignments/delete-attachment/<int:assignment_id>/<int:attachment_index>', methods=['POST'])
def delete_group_assignment_attachment(group_id, assignment_id, attachment_index):
    if current_user.role != "super_admin":
        return jsonify({"success": False, "message": "You are not allowed to delete attachments."}), 403
    
    assignment = get_item_if_admin_can_manage(Assignments, assignment_id, current_user)
    if not assignment:
        return jsonify({"success": False, "message": "Assignment not found or you do not have permission to edit it."}), 404
    
    try:
        existing_attachments = json.loads(assignment.attachments) if assignment.attachments else []
        
        if 0 <= attachment_index < len(existing_attachments):
            # If it's a file attachment, try to delete the file
            attachment = existing_attachments[attachment_index]
            if isinstance(attachment, dict) and attachment.get('type') == 'file':
                # Extract filename from URL
                url = attachment.get('url', '')
                if '/student/assignments/uploads/' in url:
                    filename = url.split('/student/assignments/uploads/')[-1]
                    file_path = os.path.join("website/assignments/uploads", filename)
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            try : 
                                storage.delete_file(folder="assignments/uploads", file_name=filename)
                            except Exception:
                                pass
                        except Exception:
                            pass  # Continue even if file deletion fails
            

            # Remove the attachment from the list
            existing_attachments.pop(attachment_index)
            
            # Update the assignment
            assignment.attachments = json.dumps(existing_attachments)
            assignment.last_edited_at = datetime.now()
            assignment.last_edited_by = current_user.id
            
            # Log the action
            new_log = AssistantLogs(
                assistant_id=current_user.id,
                action='Edit',
                log={
                    "action_name": "Edit",
                    "resource_type": "assignment",
                    "action_details": {
                        "id": assignment.id,
                        "title": assignment.title,
                        "summary": f"Attachment deleted from assignment '{assignment.title}'"
                    },
                    "data": None,
                    "before": {
                        "attachments": json.loads(assignment.attachments) if assignment.attachments else [],
                    },
                    "after": {
                        "attachments": existing_attachments,
                    }
                }
            )
            db.session.add(new_log)
            db.session.commit()
            
            return jsonify({"success": True, "message": "Attachment deleted successfully"})
        else:
            return jsonify({"success": False, "message": "Invalid attachment index"}), 400
            
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error deleting attachment: {str(e)}"}), 500


#Delete an assignment (for group-filtered pages)
@admin.route("/group/<int:group_id>/assignments/delete/<int:assignment_id>", methods=["POST"])
def delete_group_assignment(group_id, assignment_id):
    if current_user.role != "super_admin":
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "You are not allowed to delete assignments."}), 403
        flash("You are not allowed to delete assignments.", "danger")
        return redirect(url_for("admin.group_assignments", group_id=group_id))

    assignment = get_item_if_admin_can_manage(Assignments, assignment_id, current_user)
    if not assignment:
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "Assignment not found or you do not have permission to delete it."}), 404
        flash("Assignment not found or you do not have permission to delete it.", "danger")
        return redirect(url_for("admin.group_assignments", group_id=group_id))

    if not assignment.type == "Assignment":
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "Assignment is not an assignment."}), 400
        flash("Assignment is not an assignment.", "danger")
        return redirect(url_for("admin.group_assignments", group_id=group_id))

    # Delete WhatsApp notifications for this assignment
    try:
        Assignments_whatsapp.query.filter_by(assignment_id=assignment_id).delete()
        db.session.commit()
    except Exception:
        pass

    try:
        Upload_status.query.filter_by(assignment_id=assignment_id).delete()
        db.session.commit()
    except Exception:
        pass

    submissions = Submissions.query.filter_by(assignment_id=assignment_id).all()

    deleted_submissions = []
    deleted_attachments = []

    for submission in submissions:
        if assignment.deadline_date > submission.upload_time:
            if assignment.points:
                student = Users.query.get(submission.student_id)
                student.points = student.points - assignment.points
                db.session.commit()
        else:
            if assignment.points:
                student = Users.query.get(submission.student_id)
                student.points = student.points - (assignment.points / 2)
                db.session.commit()
        try:
            local_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url)
            if os.path.exists(local_path):
                os.remove(local_path)

            annotated_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url.replace(".pdf", "_annotated.pdf"))
            if os.path.exists(annotated_path):
                os.remove(annotated_path)

            try:
                storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url.replace(".pdf", "_annotated.pdf"))
            except Exception as e:
                # Ignore S3 errors, continue deleting
                pass
            try:
                storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url)
            except Exception as e:
                # Ignore S3 errors, continue deleting
                pass
            db.session.delete(submission)
            deleted_submissions.append({
                "submission_id": submission.id,
                "student_id": submission.student_id,
                "file_url": submission.file_url
            })
        except Exception:
            pass

    if assignment.attachments:
        try:
            attachment_list = json.loads(assignment.attachments)
            for attachment in attachment_list:
                # Handle both old format (strings) and new format (dicts with type/url/name)
                if isinstance(attachment, dict):
                    if attachment.get('type') == 'file':
                        file_path = attachment.get('url', '')
                        # Extract filename from URL if it's a full path
                        if '/' in file_path:
                            file_path = file_path.split('/')[-1]
                        
                        local_path = os.path.join("website/assignments/uploads", file_path)
                        if os.path.exists(local_path):
                            os.remove(local_path)
                        try:
                            storage.delete_file(folder="assignments/uploads", file_name=file_path)
                        except Exception:
                            pass
                        deleted_attachments.append(file_path)
                    # Links don't need file deletion, just log them
                    elif attachment.get('type') == 'link':
                        deleted_attachments.append(attachment.get('name', attachment.get('url', '')))
                else:
                    # Old format: plain string filename
                    file_path = attachment
                    local_path = os.path.join("website/assignments/uploads", file_path)
                    if os.path.exists(local_path):
                        os.remove(local_path)
                    try:
                        storage.delete_file(folder="assignments/uploads", file_name=file_path)
                    except Exception:
                        pass
                    deleted_attachments.append(file_path)
        except Exception as e:
            wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
            if wants_json:
                return jsonify({"success": False, "message": f"Error while deleting attachments: {str(e)}"}), 500

    # Log the delete action before deleting the assignment
    new_log = AssistantLogs(
        assistant_id=current_user.id,
        action='Delete',
        log={
            "action_name": "Delete",
            "resource_type": "assignment",
            "action_details": {
                "id": assignment.id,
                "title": assignment.title,
                "summary": f"Assignment '{assignment.title}' was deleted."
            },
            "data": None,
            "before": {
                "title": assignment.title,
                "description": assignment.description,
                "deadline_date": str(assignment.deadline_date) if assignment.deadline_date else None,
                "attachments": json.loads(assignment.attachments) if assignment.attachments else [],
                "points": assignment.points,
                "submissions_deleted_count": len(deleted_submissions),
                "subjectid": getattr(assignment, "subjectid", None),
                "subject": getattr(assignment.subject, "name", None) if hasattr(assignment, "subject") else None,
            },
            "after": None
        }
    )
    db.session.add(new_log)
    db.session.delete(assignment)
    db.session.commit()

    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
    if wants_json:
        return jsonify({"success": True, "message": "Assignment and its attachments deleted successfully!", "deleted_assignment_id": assignment_id})

    flash("Assignment and its attachments deleted successfully!", "success")
    return redirect(url_for("admin.group_assignments", group_id=group_id))


#Hide , Show Assignment (AJAX-friendly) for group-filtered pages
@admin.route("/group/<int:group_id>/assignments/visibility/<int:assignment_id>", methods=["POST"]) 
def toggle_group_assignment_visibility(group_id, assignment_id):
    if current_user.role != "super_admin":
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "You are not allowed to toggle assignment visibility."}), 403
        flash("You are not allowed to toggle assignment visibility.", "danger")
        return redirect(url_for("admin.group_assignments", group_id=group_id))

    assignment = get_item_if_admin_can_manage(Assignments, assignment_id, current_user)
    if not assignment:
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "Assignment not found or you do not have permission to edit it."}), 404
        flash("Assignment not found or you do not have permission to edit it.", "danger")
        return redirect(url_for("admin.group_assignments", group_id=group_id))

    if not assignment.type == "Assignment":
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "Assignment is not an assignment."}), 400
        flash("Assignment is not an assignment.", "danger")
        return redirect(url_for("admin.group_assignments", group_id=group_id))

    # Toggle visibility
    old_status = assignment.status
    new_status = "Hide" if assignment.status == "Show" else "Show"
    assignment.status = new_status
    assignment.last_edited_at = datetime.now()
    assignment.last_edited_by = current_user.id

    # Log the action
    new_log = AssistantLogs(
        assistant_id=current_user.id,
        action='Edit',
        log={
            "action_name": "Edit",
            "resource_type": "assignment",
            "action_details": {
                "id": assignment.id,
                "title": assignment.title,
                "summary": f"Assignment '{assignment.title}' visibility changed from {old_status} to {new_status}"
            },
            "data": None,
            "before": {
                "status": old_status,
            },
            "after": {
                "status": new_status,
            }
        }
    )
    db.session.add(new_log)
    db.session.commit()

    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
    if wants_json:
        return jsonify({"success": True, "message": f"Assignment visibility updated to {new_status}", "status": new_status})

    flash(f"Assignment visibility updated to {new_status}", "success")
    return redirect(url_for("admin.group_assignments", group_id=group_id))


#Edit an assignment 
@admin.route("/assignments/edit/<int:assignment_id>", methods=["GET", "POST"])
def edit_assignment(assignment_id):

    if current_user.role != "super_admin":
        flash("You are not allowed to edit assignments.", "danger")
        return redirect(url_for("admin.assignments"))

    # Get optional group_id from query params for back navigation
    group_id = request.args.get('group_id', type=int)
    group = None
    if group_id:
        group = Groups.query.get(group_id)

    assignment = get_item_if_admin_can_manage(Assignments, assignment_id, current_user)
    if not assignment:
        flash("Assignment not found or you do not have permission to edit it.", "danger")
        return redirect(url_for("admin.assignments"))

    if not assignment.type == "Assignment":
        flash("Assignment is not an assignment.", "danger")
        return redirect(url_for("admin.assignments"))


    existing_attachments = json.loads(assignment.attachments) if assignment.attachments else []

    groups = Groups.query.all()


    if request.method == "POST":
        # Save a copy of the old assignment state for logging
        old_assignment = {
            "title": assignment.title,
            "description": assignment.description,
            "deadline_date": str(assignment.deadline_date) if assignment.deadline_date else None,
            "groupid": assignment.groupid,
            "groups_mm": [g.id for g in getattr(assignment, "groups_mm", [])],
            "attachments": json.loads(assignment.attachments) if assignment.attachments else [],
            "student_whatsapp": assignment.student_whatsapp,
            "parent_whatsapp": assignment.parent_whatsapp,
            "out_of": assignment.out_of,
        }

        # Update basic fields
        assignment.title = request.form.get("title", "").strip()
        assignment.description = request.form.get("description", "").strip()
        assignment.last_edited_by = current_user.id
        cairo_tz = pytz.timezone('Africa/Cairo')
        aware_local_time = datetime.now(cairo_tz)
        naive_local_time = aware_local_time.replace(tzinfo=None)
        assignment.last_edited_at = naive_local_time
        # deadline
        try:
            assignment.deadline_date = parse_deadline(request.form.get("deadline_date", ""))
        except (TypeError, ValueError):
            flash("Invalid deadline date. Please use the datetime picker.", "error")
            return redirect(url_for("admin.assignments"))


        student_whatsapp = request.form.get("student_whatsapp", False)
        if student_whatsapp == "true":
            student_whatsapp = True
        else:
            student_whatsapp = False
        parent_whatsapp = request.form.get("parent_whatsapp", False)
        if parent_whatsapp == "true":
            parent_whatsapp = True
        else:
            parent_whatsapp = False

        #close after deadline
        close_after_deadline = request.form.get("close_after_deadline", False)
        if close_after_deadline == "true":
            close_after_deadline = True
        else:
            close_after_deadline = False



        # out of (full mark)
        out_of = request.form.get("out_of", 0)
        out_of = int(out_of) if str(out_of).isdigit() else 0
        assignment.student_whatsapp = student_whatsapp
        assignment.parent_whatsapp = parent_whatsapp
        assignment.out_of = out_of
        assignment.close_after_deadline = close_after_deadline


        # NEW: multi-selects (for many-to-many relationships)
        group_ids_mm  = [int(g) for g in request.form.getlist("groups[]") if g]

        if not group_ids_mm:
            groups = Groups.query.all()
            group_ids_mm = [group.id for group in groups]

        # Update many-to-many relationships
        if hasattr(assignment, "groups_mm"):
            assignment.groups_mm = Groups.query.filter(Groups.id.in_(group_ids_mm)).all() if group_ids_mm else []

        # Process new attachments with the new format
        upload_dir = "website/assignments/uploads/"
        os.makedirs(upload_dir, exist_ok=True)
        
        # Get all new attachment indices
        new_attachment_indices = []
        for key in request.form.keys():
            if key.startswith('new_attachments[') and '][name]' in key:
                index = key.split('[')[1].split(']')[0]
                if index not in new_attachment_indices:
                    new_attachment_indices.append(index)
        
        # Process each new attachment
        for idx in new_attachment_indices:
            attachment_name = request.form.get(f'new_attachments[{idx}][name]')
            attachment_type = request.form.get(f'new_attachments[{idx}][type]')
            
            if not attachment_name:
                continue
                
            attachment_obj = {
                'name': attachment_name,
                'type': attachment_type
            }
            
            if attachment_type == 'file':
                file = request.files.get(f'new_attachments[{idx}][file]')
                if file and file.filename:
                    original_filename = secure_filename(file.filename)
                    filename = f"{uuid.uuid4().hex}_{original_filename}"
                    file_path = os.path.join(upload_dir, filename)
                    file.save(file_path)
                    try:
                        with open(file_path, "rb") as f:
                            storage.upload_file(f, folder="assignments/uploads", file_name=filename)
                    except Exception as e:
                        flash(f"Error uploading file to storage: {str(e)}", "danger")
                        return redirect(url_for("admin.assignments"))
                    attachment_obj['url'] = f"/student/assignments/uploads/{filename}"
                    existing_attachments.append(attachment_obj)
            elif attachment_type == 'link':
                attachment_url = request.form.get(f'new_attachments[{idx}][url]')
                if attachment_url:
                    attachment_obj['url'] = attachment_url
                    existing_attachments.append(attachment_obj)

        assignment.attachments = json.dumps(existing_attachments)
        db.session.commit()

        # Log the edit action
        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Edit',
            log={
                "action_name": "Edit",
                "resource_type": "assignment",
                "action_details": {
                    "id": assignment.id,
                    "title": assignment.title,
                    "summary": f"Assignment '{assignment.title}' was edited."
                },
                "data": None,
                "before": old_assignment,
                "after": {
                    "title": assignment.title,
                    "description": assignment.description,
                    "deadline_date": str(assignment.deadline_date) if assignment.deadline_date else None,
                    "groupid": assignment.groupid,
                    "groups_mm": [g.id for g in getattr(assignment, "groups_mm", [])],
                    "attachments": json.loads(assignment.attachments) if assignment.attachments else [],
                    "student_whatsapp": assignment.student_whatsapp,
                    "parent_whatsapp": assignment.parent_whatsapp,
                    "out_of": assignment.out_of,
                    "close_after_deadline": assignment.close_after_deadline,
                }
            }
        )
        db.session.add(new_log)
        db.session.commit()

        # Check if it's an AJAX request
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # Return updated assignment data
            groups_names = [g.name for g in getattr(assignment, 'groups_mm', [])] if getattr(assignment, 'groups_mm', None) else []
            
            qualified_count = qualified_students_count_for_assignment(assignment)
            submitted_count = (
                Submissions.query
                .with_entities(Submissions.student_id)
                .filter_by(assignment_id=assignment.id)
                .distinct()
                .count()
            )

            updated_assignment = {
                "id": assignment.id,
                "title": assignment.title,
                "description": assignment.description,
                "creation_date": assignment.creation_date.strftime('%Y-%m-%d %I:%M %p') if assignment.creation_date else None,
                "deadline_date": assignment.deadline_date.strftime('%Y-%m-%d %I:%M %p') if assignment.deadline_date else None,
                "groups": groups_names,
                "status": assignment.status,
                "points": assignment.points,
                "submitted_students_count": submitted_count,
                "qualified_students_count": qualified_count,
                "student_whatsapp": assignment.student_whatsapp,
                "parent_whatsapp": assignment.parent_whatsapp,
                "out_of": assignment.out_of,
                "close_after_deadline": assignment.close_after_deadline,
            }
            
            return jsonify({"success": True, "message": "Assignment updated successfully!", "assignment": updated_assignment})

        flash("Assignment updated successfully!", "success")
        return redirect(url_for("admin.assignments"))
        
    if request.args.get("delete_attachment"):
        filename_to_delete = request.args.get("delete_attachment")
        if filename_to_delete in existing_attachments:
            file_path = os.path.join("website/assignments/uploads", filename_to_delete)
            if os.path.exists(file_path):
                os.remove(file_path)
            try:
                storage.delete_file(folder="assignments/uploads", file_name=filename_to_delete)
            except Exception:
                pass
            
            # Log the delete attachment action
            before_attachments = list(existing_attachments)
            existing_attachments.remove(filename_to_delete)
            assignment.attachments = json.dumps(existing_attachments)
            db.session.commit()

            new_log = AssistantLogs(
                assistant_id=current_user.id,
                action='Edit',
                log={
                    "action_name": "Edit",
                    "resource_type": "assignment_attachment",
                    "action_details": {
                        "id": assignment.id,
                        "title": assignment.title,
                        "summary": f"Attachment '{filename_to_delete}' was deleted from assignment '{assignment.title}'."
                    },
                    "data": None,
                    "before": {
                        "attachments": before_attachments
                    },
                    "after": {
                        "attachments": existing_attachments
                    }
                }
            )
            db.session.add(new_log)
            db.session.commit()

            flash("Attachment deleted successfully!", "success")
        else:
            flash("Attachment not found!", "error")
        return redirect(url_for("admin.edit_assignment", assignment_id=assignment_id))

    cairo_tz = pytz.timezone('Africa/Cairo')
    now_cairo = datetime.now(cairo_tz)
    assignment_late_exceptions = []
    late_exception_rows = (
        AssignmentLateException.query
        .filter_by(assignment_id=assignment.id)
        .join(Users, AssignmentLateException.student_id == Users.id)
        .order_by(Users.name.asc())
        .all()
    )
    for exception in late_exception_rows:
        student = exception.student
        aware_deadline = None
        if exception.extended_deadline:
            try:
                aware_deadline = cairo_tz.localize(exception.extended_deadline)
            except ValueError:
                aware_deadline = exception.extended_deadline.astimezone(cairo_tz)
        is_active = aware_deadline is None or aware_deadline >= now_cairo
        assignment_late_exceptions.append({
            "exception": exception,
            "student": student,
            "aware_deadline": aware_deadline,
            "is_active": is_active,
        })

    return render_template(
        "admin/assignments/edit_assignment.html",
        assignment=assignment,
        groups=groups,
        attachments=existing_attachments,
        group_id=group_id,
        group=group,
        late_exceptions=assignment_late_exceptions
    )


def _resolve_late_exception_redirect(assignment):
    source = request.form.get("source", "")
    if source == "student_profile":
        student_id = request.form.get("student_id") or request.form.get("student_identifier")
        if student_id and str(student_id).isdigit():
            return url_for("admin.student", user_id=int(student_id))
        exception_student_id = request.form.get("exception_student_id")
        if exception_student_id and str(exception_student_id).isdigit():
            return url_for("admin.student", user_id=int(exception_student_id))
        return url_for("admin.student", user_id=assignment.created_by or current_user.id)
    elif source == "exam_edit" or assignment.type == "Exam":
        return url_for("admin.edit_exam", exam_id=assignment.id)
    return url_for("admin.edit_assignment", assignment_id=assignment.id)


def _lookup_student_from_form():
    student_id_raw = request.form.get("student_id")
    if student_id_raw and str(student_id_raw).isdigit():
        student = Users.query.filter_by(id=int(student_id_raw), role="student").first()
        if student:
            return student

    identifier = request.form.get("student_identifier", "").strip()
    if not identifier:
        return None
    if identifier.isdigit():
        return Users.query.filter_by(id=int(identifier), role="student").first()

    return Users.query.filter(func.lower(Users.email) == identifier.lower(), Users.role == "student").first()


@admin.route("/assignments/<int:assignment_id>/late-exceptions", methods=["POST"])
def add_late_exception(assignment_id):
    if current_user.role != "super_admin":
        flash("You are not allowed to manage late submission exceptions.", "danger")
        return redirect(request.referrer or url_for("admin.assignments"))

    assignment = get_item_if_admin_can_manage(Assignments, assignment_id, current_user)
    if not assignment:
        flash("Assignment not found or you do not have permission to edit it.", "danger")
        return redirect(request.referrer or url_for("admin.assignments"))

    redirect_url = _resolve_late_exception_redirect(assignment)

    student = _lookup_student_from_form()
    if not student:
        flash("Student not found. Provide a valid student ID or email.", "danger")
        return redirect(redirect_url)

    existing_exception = AssignmentLateException.query.filter_by(
        assignment_id=assignment.id,
        student_id=student.id
    ).first()
    if existing_exception:
        flash("A late submission exception already exists for this student.", "warning")
        return redirect(redirect_url)

    extended_deadline_str = request.form.get("extended_deadline")
    extended_deadline = None
    if extended_deadline_str:
        try:
            extended_deadline = parse_deadline(extended_deadline_str)
        except (TypeError, ValueError):
            flash("Invalid extended deadline. Please use the datetime picker format.", "danger")
            return redirect(redirect_url)

    exception = AssignmentLateException(
        assignment_id=assignment.id,
        student_id=student.id,
        extended_deadline=extended_deadline
    )
    db.session.add(exception)
    db.session.commit()

    flash_message = f"Late submission exception granted to {student.name or student.email}."
    if extended_deadline:
        flash_message += " Extension deadline set."
    else:
        flash_message += " No deadline specified (manual closure required)."
    flash(flash_message, "success")

    return redirect(redirect_url)


@admin.route("/assignments/<int:assignment_id>/late-exceptions/<int:exception_id>/delete", methods=["POST"])
def remove_late_exception(assignment_id, exception_id):
    if current_user.role != "super_admin":
        flash("You are not allowed to manage late submission exceptions.", "danger")
        return redirect(request.referrer or url_for("admin.assignments"))

    assignment = get_item_if_admin_can_manage(Assignments, assignment_id, current_user)
    if not assignment:
        flash("Assignment not found or you do not have permission to edit it.", "danger")
        return redirect(request.referrer or url_for("admin.assignments"))

    redirect_url = _resolve_late_exception_redirect(assignment)

    exception = AssignmentLateException.query.filter_by(
        id=exception_id,
        assignment_id=assignment.id
    ).first()
    if not exception:
        flash("Late submission exception not found.", "warning")
        return redirect(redirect_url)

    student = exception.student
    db.session.delete(exception)
    db.session.commit()

    flash(f"Late submission exception removed for {student.name or student.email}.", "success")
    return redirect(redirect_url)


#Repost an assignment (create a copy with modifications)
@admin.route("/assignments/repost", methods=["POST"])
def repost_assignment():
    if current_user.role != "super_admin":
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "You are not allowed to repost assignments."}), 403
        flash("You are not allowed to repost assignments.", "danger")
        return redirect(url_for("admin.assignments"))

    # Get the original assignment ID (optional, for reference)
    original_assignment_id = request.form.get("original_assignment_id")
    
    # Create a new assignment with the posted data
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    
    if not title:
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "Assignment title is required."}), 400
        flash("Assignment title is required.", "danger")
        return redirect(url_for("admin.assignments"))
    
    # Parse deadline
    try:
        deadline_date = parse_deadline(request.form.get("deadline_date", ""))
    except (TypeError, ValueError):
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "Invalid deadline date. Please use the datetime picker."}), 400
        flash("Invalid deadline date. Please use the datetime picker.", "error")
        return redirect(url_for("admin.assignments"))
    
    # Get WhatsApp settings
    student_whatsapp = request.form.get("student_whatsapp", False)
    if student_whatsapp == "true":
        student_whatsapp = True
    else:
        student_whatsapp = False
        
    parent_whatsapp = request.form.get("parent_whatsapp", False)
    if parent_whatsapp == "true":
        parent_whatsapp = True
    else:
        parent_whatsapp = False
    
    # Get close after deadline setting
    close_after_deadline = request.form.get("close_after_deadline", False)
    if close_after_deadline == "true":
        close_after_deadline = True
    else:
        close_after_deadline = False
    
    # Get out_of (full mark)
    out_of = request.form.get("out_of", 0)
    out_of = int(out_of) if str(out_of).isdigit() else 0
    
    # Get group IDs
    group_ids_mm = [int(g) for g in request.form.getlist("groups[]") if g]
    
    if not group_ids_mm:
        groups = Groups.query.all()
        group_ids_mm = [group.id for group in groups]
    
    # Check if admin can manage these groups
    managed_ids = get_user_scope_ids()
    if not can_manage(group_ids_mm, managed_ids):
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "You do not have permission to assign to these groups."}), 403
        flash("You do not have permission to assign to these groups.", "danger")
        return redirect(url_for("admin.assignments"))
    
    # Handle attachments (copy from original and add new ones)
    upload_dir = "website/assignments/uploads/"
    os.makedirs(upload_dir, exist_ok=True)
    
    # Start with existing attachments if copying from original
    attachments = []
    if original_assignment_id:
        original_assignment = Assignments.query.get(original_assignment_id)
        if original_assignment and original_assignment.attachments:
            try:
                attachments = json.loads(original_assignment.attachments)
            except:
                attachments = []
    
    # Process new attachments
    new_attachment_indices = []
    for key in request.form.keys():
        if key.startswith('new_attachments[') and '][name]' in key:
            index = key.split('[')[1].split(']')[0]
            if index not in new_attachment_indices:
                new_attachment_indices.append(index)
    
    for idx in new_attachment_indices:
        attachment_name = request.form.get(f'new_attachments[{idx}][name]')
        attachment_type = request.form.get(f'new_attachments[{idx}][type]')
        
        if not attachment_name:
            continue
            
        attachment_obj = {
            'name': attachment_name,
            'type': attachment_type
        }
        
        if attachment_type == 'file':
            file = request.files.get(f'new_attachments[{idx}][file]')
            if file and file.filename:
                original_filename = secure_filename(file.filename)
                filename = f"{uuid.uuid4().hex}_{original_filename}"
                file_path = os.path.join(upload_dir, filename)
                file.save(file_path)
                try:
                    with open(file_path, "rb") as f:
                        storage.upload_file(f, folder="assignments/uploads", file_name=filename)
                except Exception as e:
                    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
                    if wants_json:
                        return jsonify({"success": False, "message": f"Error uploading file to storage: {str(e)}"}), 500
                    flash(f"Error uploading file to storage: {str(e)}", "danger")
                    return redirect(url_for("admin.assignments"))
                attachment_obj['url'] = f"/student/assignments/uploads/{filename}"
                attachments.append(attachment_obj)
        elif attachment_type == 'link':
            attachment_url = request.form.get(f'new_attachments[{idx}][url]')
            if attachment_url:
                attachment_obj['url'] = attachment_url
                attachments.append(attachment_obj)
    
    # Create the new assignment
    new_assignment = Assignments(
        title=title,
        description=description,
        deadline_date=deadline_date,
        attachments=json.dumps(attachments),
        status="Show",
        type="Assignment",
        created_by=current_user.id,
        student_whatsapp=student_whatsapp,
        parent_whatsapp=parent_whatsapp,
        out_of=out_of,
        close_after_deadline=close_after_deadline
    )
    
    # Set groups (many-to-many)
    if hasattr(new_assignment, "groups_mm"):
        new_assignment.groups_mm = Groups.query.filter(Groups.id.in_(group_ids_mm)).all() if group_ids_mm else []
    
    db.session.add(new_assignment)
    db.session.commit()
    
    # Log the repost action
    new_log = AssistantLogs(
        assistant_id=current_user.id,
        action='Create',
        log={
            "action_name": "Repost",
            "resource_type": "assignment",
            "action_details": {
                "id": new_assignment.id,
                "title": new_assignment.title,
                "summary": f"Assignment '{new_assignment.title}' was reposted." + (f" (copied from assignment #{original_assignment_id})" if original_assignment_id else "")
            },
            "data": {
                "original_assignment_id": original_assignment_id,
                "title": new_assignment.title,
                "description": new_assignment.description,
                "deadline_date": str(new_assignment.deadline_date) if new_assignment.deadline_date else None,
                "groups_mm": [g.id for g in getattr(new_assignment, "groups_mm", [])],
                "attachments": json.loads(new_assignment.attachments) if new_assignment.attachments else [],
                "student_whatsapp": new_assignment.student_whatsapp,
                "parent_whatsapp": new_assignment.parent_whatsapp,
                "out_of": new_assignment.out_of,
                "close_after_deadline": new_assignment.close_after_deadline,
            },
            "before": None,
            "after": None
        }
    )
    db.session.add(new_log)
    db.session.commit()
    
    # Check if it's an AJAX request
    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
    if wants_json:
        groups_names = [g.name for g in getattr(new_assignment, 'groups_mm', [])] if getattr(new_assignment, 'groups_mm', None) else []
        
        qualified_count = qualified_students_count_for_assignment(new_assignment)
        
        created_assignment = {
            "id": new_assignment.id,
            "title": new_assignment.title,
            "description": new_assignment.description,
            "creation_date": new_assignment.creation_date.strftime('%Y-%m-%d %I:%M %p') if new_assignment.creation_date else None,
            "deadline_date": new_assignment.deadline_date.strftime('%Y-%m-%d %I:%M %p') if new_assignment.deadline_date else None,
            "groups": groups_names,
            "status": new_assignment.status,
            "points": new_assignment.points,
            "submitted_students_count": 0,
            "qualified_students_count": qualified_count,
            "student_whatsapp": new_assignment.student_whatsapp,
            "parent_whatsapp": new_assignment.parent_whatsapp,
            "out_of": new_assignment.out_of,
            "close_after_deadline": new_assignment.close_after_deadline,
        }
        
        return jsonify({"success": True, "message": "Assignment reposted successfully!", "assignment": created_assignment})
    
    flash("Assignment reposted successfully!", "success")
    return redirect(url_for("admin.assignments"))


#Delete an assignment 
@admin.route("/assignments/delete/<int:assignment_id>", methods=["POST"])
def delete_assignment(assignment_id):

    if current_user.role != "super_admin":
        flash("You are not allowed to delete assignments.", "danger")
        return redirect(url_for("admin.assignments"))


    assignment = get_item_if_admin_can_manage(Assignments, assignment_id, current_user)
    if not assignment:
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "Assignment not found or you do not have permission to delete it."}), 404
        flash("Assignment not found or you do not have permission to delete it.", "danger")
        return redirect(url_for("admin.assignments"))


    if not assignment.type == "Assignment":
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "Assignment is not an assignment."}), 400
        flash("Assignment is not an assignment.", "danger")
        return redirect(url_for("admin.assignments"))

    # Delete WhatsApp notifications for this assignment
    try:
        Assignments_whatsapp.query.filter_by(assignment_id=assignment_id).delete()
        db.session.commit()
    except Exception:
        pass

    try :
        Upload_status.query.filter_by(assignment_id=assignment_id).delete()
        db.session.commit()
    except Exception:
        pass
        

    submissions = Submissions.query.filter_by(assignment_id=assignment_id).all()

    deleted_submissions = []
    deleted_attachments = []

    for submission in submissions:
        if assignment.deadline_date > submission.upload_time:
            if assignment.points:
                student = Users.query.get(submission.student_id)
                student.points = student.points - assignment.points
                db.session.commit()
        else:
            if assignment.points:
                student = Users.query.get(submission.student_id)
                student.points = student.points - (assignment.points / 2)
                db.session.commit()
        try:
            local_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url)
            if os.path.exists(local_path):
                os.remove(local_path)

            annotated_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url.replace(".pdf", "_annotated.pdf"))
            if os.path.exists(annotated_path):
                os.remove(annotated_path)

            try:
                storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url.replace(".pdf", "_annotated.pdf"))
            except Exception as e:
                # Ignore S3 errors, continue deleting
                pass
            try:
                storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url)
            except Exception as e:
                # Ignore S3 errors, continue deleting
                pass
            db.session.delete(submission)
            deleted_submissions.append({
                "submission_id": submission.id,
                "student_id": submission.student_id,
                "file_url": submission.file_url
            })
        except Exception:
            pass

    if assignment.attachments:
        try:
            attachment_list = json.loads(assignment.attachments)
            for attachment in attachment_list:
                # Handle both old format (strings) and new format (dicts with type/url/name)
                if isinstance(attachment, dict):
                    if attachment.get('type') == 'file':
                        file_path = attachment.get('url', '')
                        # Extract filename from URL if it's a full path
                        if '/' in file_path:
                            file_path = file_path.split('/')[-1]
                        
                        local_path = os.path.join("website/assignments/uploads", file_path)
                        if os.path.exists(local_path):
                            os.remove(local_path)
                        try:
                            storage.delete_file(folder="assignments/uploads", file_name=file_path)
                        except Exception:
                            pass
                        deleted_attachments.append(file_path)
                    # Links don't need file deletion, just log them
                    elif attachment.get('type') == 'link':
                        deleted_attachments.append(attachment.get('name', attachment.get('url', '')))
                else:
                    # Old format: plain string filename
                    file_path = attachment
                    local_path = os.path.join("website/assignments/uploads", file_path)
                    if os.path.exists(local_path):
                        os.remove(local_path)
                    try:
                        storage.delete_file(folder="assignments/uploads", file_name=file_path)
                    except Exception:
                        pass
                    deleted_attachments.append(file_path)
        except Exception as e:
            flash(f"Error while deleting attachments: {str(e)}", "danger")
    
    # Log the delete action before deleting the assignment
    new_log = AssistantLogs(
        assistant_id=current_user.id,
        action='Delete',
        log={
            "action_name": "Delete",
            "resource_type": "assignment",
            "action_details": {
                "id": assignment.id,
                "title": assignment.title,
                "summary": f"Assignment '{assignment.title}' was deleted."
            },
            "data": None,
            "before": {
                "title": assignment.title,
                "description": assignment.description,
                "deadline_date": str(assignment.deadline_date) if assignment.deadline_date else None,
                "attachments": json.loads(assignment.attachments) if assignment.attachments else [],
                "points": assignment.points,
                "submissions_deleted_count": len(deleted_submissions),
                "subjectid": getattr(assignment, "subjectid", None),  # Add subjectid
                "subject": getattr(assignment.subject, "name", None) if hasattr(assignment, "subject") else None,  # Add subject name
            },
            "after": None
        }
    )
    db.session.add(new_log)
    db.session.delete(assignment)
    db.session.commit()

    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
    if wants_json:
        return jsonify({"success": True, "message": "Assignment and its attachments deleted successfully!", "deleted_assignment_id": assignment_id})

    flash("Assignment and its attachments deleted successfully!", "success")
    return redirect(url_for("admin.assignments"))

#Hide , Show Assignment (AJAX-friendly)
@admin.route("/assignments/visibility/<int:assignment_id>", methods=["POST"]) 
def toggle_assignment_visibility(assignment_id):

    if current_user.role != "super_admin":
        flash("You are not allowed to toggle the visibility of assignments.", "danger")
        return redirect(url_for("admin.assignments"))


    assignment = get_item_if_admin_can_manage(Assignments, assignment_id, current_user)
    if not assignment:
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({"success": False, "message": "Assignment not found or permission denied."}), 404
        flash("Assignment not found or you do not have permission to toggle its visibility.", "danger")
        return redirect(url_for("admin.assignments"))



    old_status = assignment.status
    assignment.status = "Hide" if assignment.status == "Show" else "Show"
    db.session.commit()
    # Add log for toggling assignment visibility
    try:
        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Edit',
            log={
                "action_name": "Edit",
                "resource_type": "assignment_visibility",
                "action_details": {
                    "id": assignment.id,
                    "title": assignment.title,
                    "summary": f"Assignment '{assignment.title}' visibility was changed."
                },
                "data": None,
                "before": {
                    "visibility_status": old_status
                },
                "after": {
                    "visibility_status": assignment.status
                }
            }
        )
        db.session.add(new_log)
        db.session.commit()
    except Exception as e:
        pass

    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
    if wants_json:
        return jsonify({"success": True, "message": f"Assignment status is: {assignment.status} now!", "status": assignment.status, "assignment_id": assignment.id})

    flash(f"Assignment status is: {assignment.status} now!", "success")
    return redirect(url_for("admin.assignments"))









#View submission media (Important)
@admin.route("/submissions/uploads/<int:submission_id>")
def submission_media(submission_id):
    submission = Submissions.query.get_or_404(submission_id)
    folder = f"student_{submission.student_id}"
    filename = submission.file_url
    file_path = os.path.join("website/submissions/uploads", folder, filename)

    if os.path.isfile(file_path):
        return send_from_directory(f"submissions/uploads/{folder}", filename)
    else:
        try:
            storage.download_file(folder=f"submissions/uploads/student_{submission.student_id}", file_name=filename, local_path=file_path)
        except Exception as e:
            flash(f'Error downloading file: {str(e)}', 'danger')
            flash('File not found!', 'danger')
            return redirect(url_for("admin.view_assignment_submissions", assignment_id=submission.assignment_id))
        return send_from_directory(f"submissions/uploads/{folder}", filename)

#=================================================================
#Materials
#=================================================================

@admin.route('/api/folders-data', methods=["GET"])
def folders_data():
    folders_query = get_visible_to_admin_query(Materials_folder, current_user)
    folders = folders_query.order_by(Materials_folder.id.desc()).all()

    folders_list = []
    for folder in folders:
        schools_names = [s.name for s in getattr(folder, 'schools_mm', [])] if getattr(folder, 'schools_mm', None) else []
        stages_names = [s.name for s in getattr(folder, 'stages_mm', [])] if getattr(folder, 'stages_mm', None) else []
        groups_names = [g.name for g in getattr(folder, 'groups_mm', [])] if getattr(folder, 'groups_mm', None) else []

        schools_display = ', '.join(schools_names) if schools_names else 'All Schools'
        stages_display = ', '.join(stages_names) if stages_names else 'All Stages'
        groups_display = ', '.join(groups_names) if groups_names else 'All Classes'

        # Count materials in this folder
        material_count = Materials.query.filter_by(folderid=folder.id).count()

        folders_list.append({
            "id": folder.id,
            "title": folder.title,
            "description": folder.description,
            "category": folder.category,
            "subject": folder.subject.name if folder.subject else "N/A",
            "subject_id": folder.subject.id if folder.subject else None,
            "schools": schools_display,
            "stages": stages_display,
            "groups": groups_display,
            "material_count": material_count
        })
    
    return jsonify(folders_list)


@admin.route('/api/folder/<int:folder_id>', methods=["GET"])
def get_folder_data(folder_id):
    """API endpoint to fetch single folder data for editing"""
    folder = get_item_if_admin_can_manage(Materials_folder, folder_id, current_user)
    if not folder:
        return jsonify({"success": False, "message": "Folder not found or you do not have permission to view it."}), 404

    schools_mm = [{"id": s.id, "name": s.name} for s in getattr(folder, 'schools_mm', [])] if getattr(folder, 'schools_mm', None) else []
    stages_mm = [{"id": s.id, "name": s.name} for s in getattr(folder, 'stages_mm', [])] if getattr(folder, 'stages_mm', None) else []
    groups_mm = [{"id": g.id, "name": g.name} for g in getattr(folder, 'groups_mm', [])] if getattr(folder, 'groups_mm', None) else []

    folder_data = {
        "id": folder.id,
        "title": folder.title,
        "description": folder.description,
        "category": folder.category,
        "subject": {
            "id": folder.subject.id if folder.subject else None,
            "name": folder.subject.name if folder.subject else None
        },
        "schools_mm": schools_mm,
        "stages_mm": stages_mm,
        "groups_mm": groups_mm,
    }

    return jsonify({"success": True, "folder": folder_data})








@admin.route("/folders", methods=["GET", "POST"])
def folders():
    return "Paused for now"


    if request.method == "POST":


        managed_group_ids = [g.id for g in getattr(current_user, "managed_groups", [])]
        managed_stage_ids = [s.id for s in getattr(current_user, "managed_stages", [])]
        managed_school_ids = [s.id for s in getattr(current_user, "managed_schools", [])]
        managed_subject_ids = [s.id for s in getattr(current_user, "managed_subjects", [])]

        groups = Groups.query.filter(Groups.id.in_(managed_group_ids)).all() if managed_group_ids else []
        stages = Stages.query.filter(Stages.id.in_(managed_stage_ids)).all() if managed_stage_ids else []
        schools = Schools.query.filter(Schools.id.in_(managed_school_ids)).all() if managed_school_ids else []
        subjects = Subjects.query.filter(Subjects.id.in_(managed_subject_ids)).all() if managed_subject_ids else []

        subject_school_map = {}
        for subject in subjects:
            # For each subject, get its associated schools and format them for JavaScript
            schools_list = [{"id": school.id, "name": school.name} for school in subject.schools]
            # Sort schools to put "Online" schools first, then alphabetically
            schools_list.sort(key=lambda school: (0 if "Online" in school["name"] else 1, school["name"].lower()))
            subject_school_map[subject.id] = schools_list






        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        category = request.form.get("category")

        subject_id_single = int_or_none(request.form.get("subject_id"))

        group_ids  = parse_multi_ids("groups_mm[]")
        stage_ids  = parse_multi_ids("stages_mm[]")
        school_ids = parse_multi_ids("schools_mm[]")

        if not title:
            flash("Title is required!", "danger")
            return redirect(url_for("admin.folders"))

        if not group_ids:
            group_ids = [g.id for g in groups]
        if not stage_ids:
            stage_ids = [s.id for s in stages]
        if not school_ids:

            subject_id = subject_id_single

            if subject_id and subject_id in subject_school_map:
                school_ids = [school['id'] for school in subject_school_map[subject_id]]
            else:
                flash("Choose a subject" , "danger")
                return redirect(url_for("admin.announcements"))

        if not subject_id_single:
            flash("You must select a subject.", "danger")
            return redirect(url_for("admin.folders"))

        if group_ids:
            if not set(group_ids).issubset(set(managed_group_ids)):
                flash("You are not allowed to create a folder for one or more selected groups.", "danger")
                return redirect(url_for("admin.folders"))
        if stage_ids:
            if not set(stage_ids).issubset(set(managed_stage_ids)):
                flash("You are not allowed to create a folder for one or more selected stages.", "danger")
                return redirect(url_for("admin.folders"))
        if school_ids:
            if not set(school_ids).issubset(set(managed_school_ids)):
                flash("You are not allowed to create a folder for one or more selected schools.", "danger")
                return redirect(url_for("admin.folders"))


        if subject_id_single:
            if subject_id_single not in managed_subject_ids:
                flash("You are not allowed to create a folder for this subject.", "danger")
                return redirect(url_for("admin.folders"))

        new_record = Materials_folder(
            title=title,
            description=description,
            subjectid=subject_id_single,
            category=category,
        )

        # IMPORTANT: add to session BEFORE assigning M2M relations
        db.session.add(new_record)

        # attach many-to-many scopes if provided
        if group_ids:
            new_record.groups_mm = Groups.query.filter(Groups.id.in_(group_ids)).all()
        if stage_ids:
            new_record.stages_mm = Stages.query.filter(Stages.id.in_(stage_ids)).all()
        if school_ids:
            new_record.schools_mm = Schools.query.filter(Schools.id.in_(school_ids)).all()
        
        db.session.commit()

        # Log the action
        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Create',
            log={
                "action_name": "Create",
                "resource_type": "materials_folder",
                "action_details": {
                    "id": new_record.id,
                    "title": new_record.title,
                    "summary": f"Materials folder '{new_record.title}' was created."
                },
                "data": {
                    "title": new_record.title,
                    "description": new_record.description,
                    "category": new_record.category,
                    "stages_mm": [s.id for s in new_record.stages_mm],
                    "groups_mm": [g.id for g in new_record.groups_mm],
                    "schools_mm": [s.id for s in new_record.schools_mm],
                    "subject": new_record.subject.name
                },
                "before": None,
                "after": None
            }
        )
        db.session.add(new_log)
        db.session.commit()

        # AJAX response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            folder_data = {
                "id": new_record.id,
                "title": new_record.title,
                "description": new_record.description,
                "category": new_record.category,
                "subject": new_record.subject.name if new_record.subject else "N/A",
                "subject_id": new_record.subject.id if new_record.subject else None,
                "schools": ', '.join([s.name for s in new_record.schools_mm]) if new_record.schools_mm else 'All Schools',
                "stages": ', '.join([s.name for s in new_record.stages_mm]) if new_record.stages_mm else 'All Stages',
                "groups": ', '.join([g.name for g in new_record.groups_mm]) if new_record.groups_mm else 'All Classes',
                "material_count": 0
            }
            return jsonify({"success": True, "message": "Folder added successfully!", "folder": folder_data})

        flash("Folder added successfully!", "success")
        return redirect(url_for("admin.folders"))
    

    return render_template("admin/materials/folder.html")

    
@admin.route("/folders/<int:folder_id>/edit", methods=["GET", "POST"])
def edit_folder(folder_id):
    return "Paused for now"
    folder = get_item_if_admin_can_manage(Materials_folder, folder_id, current_user)
    if not folder:
        flash("Folder not found or you do not have permission to edit it.", "danger")
        return redirect(url_for("admin.folders"))

    groups = Groups.query.all()
    stages = Stages.query.all()
    schools = Schools.query.all()
    subjects = Subjects.query.all()

    if request.method == "POST":
        old_data = {
            "title": folder.title,
            "description": folder.description,
            "category": folder.category,
            "subjectid": folder.subjectid,
            "stages_mm": [s.id for s in folder.stages_mm],
            "groups_mm": [g.id for g in folder.groups_mm],
            "schools_mm": [s.id for s in folder.schools_mm]
        }

        folder.title = (request.form.get("title") or "").strip()
        folder.description = (request.form.get("description") or "").strip()
        folder.category = request.form.get("category")
        
        # Handle subject selection
        subject_id = int_or_none(request.form.get("subject"))
        folder.subjectid = subject_id

        # legacy single-selects (kept for backward compatibility)
        stage_id_single = int_or_none(request.form.get("stage"))
        group_id_single = int_or_none(request.form.get("group"))
        school_id_single = int_or_none(request.form.get("school"))

        # new multi-selects
        group_ids_mm  = parse_multi_ids("groups_mm[]")
        stage_ids_mm  = parse_multi_ids("stages_mm[]")
        
        # Handle new multi-select schools format (comma-separated string)
        school_ids_str = request.form.get("school_ids", "").strip()
        if school_ids_str:
            school_ids_mm = [int(s.strip()) for s in school_ids_str.split(",") if s.strip()]
        else:
            school_ids_mm = []

        folder.stageid = stage_id_single
        folder.groupid = group_id_single
        folder.schoolid = school_id_single

        if not group_id_single and not group_ids_mm:
            groups = Groups.query.all()
            group_ids_mm = [group.id for group in groups]

        if not stage_id_single and not stage_ids_mm:
            stages = Stages.query.all()
            stage_ids_mm = [stage.id for stage in stages]
        
        if not school_id_single and not school_ids_mm:
            schools = Schools.query.all()
            school_ids_mm = [school.id for school in schools]

        # Update many-to-many relationships
        if hasattr(folder, "groups_mm"):
            folder.groups_mm = Groups.query.filter(Groups.id.in_(group_ids_mm)).all() if group_ids_mm else []
        if hasattr(folder, "stages_mm"):
            folder.stages_mm = Stages.query.filter(Stages.id.in_(stage_ids_mm)).all() if stage_ids_mm else []
        if hasattr(folder, "schools_mm"):
            folder.schools_mm = Schools.query.filter(Schools.id.in_(school_ids_mm)).all() if school_ids_mm else []

        db.session.commit()

        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Edit',
            log={
                "action_name": "Edit",
                "resource_type": "materials_folder",
                "action_details": {
                    "id": folder.id,
                    "title": folder.title,
                    "summary": f"Materials folder '{folder.title}' was edited."
                },
                "data": None,
                "before": old_data,
                "after": {
                    "title": folder.title,
                    "description": folder.description,
                    "category": folder.category,
                    "subjectid": folder.subjectid,
                    "stages_mm": [s.id for s in folder.stages_mm],
                    "groups_mm": [g.id for g in folder.groups_mm],
                    "schools_mm": [s.id for s in folder.schools_mm]
                }
            }
        )
        db.session.add(new_log)
        db.session.commit()

        # AJAX response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            folder_data = {
                "id": folder.id,
                "title": folder.title,
                "description": folder.description,
                "category": folder.category,
                "subject": folder.subject.name if folder.subject else "N/A",
                "subject_id": folder.subject.id if folder.subject else None,
                "schools": ', '.join([s.name for s in folder.schools_mm]) if folder.schools_mm else 'All Schools',
                "stages": ', '.join([s.name for s in folder.stages_mm]) if folder.stages_mm else 'All Stages',
                "groups": ', '.join([g.name for g in folder.groups_mm]) if folder.groups_mm else 'All Classes',
                "material_count": Materials.query.filter_by(folderid=folder.id).count()
            }
            return jsonify({"success": True, "message": "Folder updated successfully!", "folder": folder_data})

        flash("Folder updated successfully!", "success")
        return redirect(url_for("admin.folders"))
    return render_template("admin/materials/folder_edit.html", folder=folder, groups=groups, stages=stages, schools=schools, subjects=subjects)

@admin.route("/folders/delete/<int:folder_id>", methods=["POST"])
def delete_folder(folder_id):
    folder = get_item_if_admin_can_manage(Materials_folder, folder_id, current_user)
    if not folder:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Folder not found or you don't have permission."}), 404
        flash("Folder not found or you do not have permission to delete it.", "danger")
        return redirect(url_for("admin.folders"))

    try:
        files = Materials.query.filter_by(folderid=folder.id).all()
        for file in files:
            file_path = os.path.join("website/material/uploads", file.url)
            if os.path.isfile(file_path):
                os.remove(file_path)
            db.session.delete(file)
            try :
                storage.delete_file(folder="material/uploads", file_name=file.url)
            except Exception:
                pass
        
        # Log the action before deleting
        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Delete',
            log={
                "action_name": "Delete",
                "resource_type": "materials_folder",
                "action_details": {
                    "id": folder.id,
                    "title": folder.title,
                    "summary": f"Materials folder '{folder.title}' and all its files were deleted."
                },
                "data": None,
                "before": {
                    "title": folder.title,
                    "description": folder.description,
                    "category": folder.category,
                    "files_count": len(files)
                },
                "after": None
            }
        )
        db.session.add(new_log)
        db.session.delete(folder)
        db.session.commit()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True, "message": "Folder and all associated files deleted successfully!"})
        
        flash("Folder and all associated files deleted successfully!", "success")
    except Exception as e:
        db.session.rollback()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": f"An error occurred while deleting the folder: {str(e)}"}), 500
        flash(f"An error occurred while deleting the folder: {str(e)}", "danger")
    return redirect(url_for("admin.folders"))


@admin.route("/folders/<int:folder_id>", methods=["GET", "POST"])
def view_material(folder_id):
    folder = get_item_if_admin_can_manage(Materials_folder, folder_id, current_user)
    if not folder:
        flash("Folder not found or you do not have permission to view it.", "danger")
        return redirect(url_for("admin.folders"))

    materials_query = Materials.query.filter_by(folderid=folder_id).order_by(Materials.id.asc())

    if request.method == "POST":
        title = request.form["title"]

        if 'record_file' not in request.files or request.files["record_file"].filename == '':
            flash("No file selected.", "danger")
            return redirect(url_for("admin.view_material", folder_id=folder_id))


        file = request.files["record_file"]
        original_filename = secure_filename(file.filename)
        file_ext = os.path.splitext(original_filename)[1]
        unique_filename = f"{uuid.uuid4().hex}{file_ext}"
        filepath = os.path.join("website/material/uploads", unique_filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        file.save(filepath)
        with open(filepath, "rb") as f:
            storage.upload_file(f, folder="material/uploads", file_name=unique_filename)
        filename = unique_filename  # Ensure the db entry uses the uuid filename

        new_record = Materials(title=title, url=filename, folderid=folder_id)
        db.session.add(new_record)
        db.session.commit()

        # Log the action
        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Create',
            log={
                "action_name": "Create",
                "resource_type": "material_file",
                "action_details": {
                    "id": new_record.id,
                    "title": new_record.title,
                    "summary": f"Material file '{new_record.title}' was added to folder '{folder.title}'."
                },
                "data": {
                    "title": new_record.title,
                    "filename": new_record.url,
                    "folder_id": new_record.folderid
                },
                "before": None,
                "after": None
            }
        )
        db.session.add(new_log)
        db.session.commit()

        flash("File added successfully!", "success")
        return redirect(url_for("admin.view_material", folder_id=folder_id))

    materials = materials_query.all()
    return render_template("admin/materials/material.html", material=materials, folder=folder)

@admin.route("/material/delete/<int:folder_id>/<int:material_id>", methods=["POST"])
def delete_material(folder_id, material_id):

    folder = get_item_if_admin_can_manage(Materials_folder, folder_id, current_user)
    if not folder:
        flash("Folder not found or you do not have permission to delete it.", "danger")
        return redirect(url_for("admin.folders"))


    material = Materials.query.get_or_404(material_id)



    file_path = os.path.join("website/material/uploads", material.url)

    # Log the action before deleting
    new_log = AssistantLogs(
        assistant_id=current_user.id,
        action='Delete',
        log={
            "action_name": "Delete",
            "resource_type": "material_file",
            "action_details": {
                "id": material.id,
                "title": material.title,
                "summary": f"Material file '{material.title}' was deleted."
            },
            "data": None,
            "before": {
                "title": material.title,
                "filename": material.url,
                "folder_id": material.folderid
            },
            "after": None
        }
    )
    db.session.add(new_log)

    if os.path.isfile(file_path):
        try:
            os.remove(file_path)
            try :
                storage.delete_file(folder="material/uploads", file_name=material.url)
            except Exception:
                pass
            flash("Material file deleted from the server.", "info")
        except Exception as e:
            flash(f"An error occurred while deleting the file: {str(e)}", "danger")

    db.session.delete(material)
    db.session.commit()
    flash("Material deleted successfully!", "success")
    return redirect(url_for("admin.view_material", folder_id=folder_id))


@admin.route("/material/uploads/<int:material_id>")
def material_media(material_id):
    material = Materials.query.get_or_404(material_id)

    filename = secure_filename(material.url)
    file_path = os.path.join("website/material/uploads", filename)

    if os.path.isfile(file_path):
        return send_from_directory("material/uploads", filename)
    else :
        try :
            storage.download_file(folder="material/uploads", file_name=filename, local_path=file_path)
        except Exception:
            flash("File not found", 'warning')
            return redirect(url_for("admin.view_material", folder_id=material.folderid))
        return send_from_directory("material/uploads", filename)
    
#=================================================================
#Setup Subject , School , Stage , Class (Academic Setup)
#================================================================= 

@admin.route('/students/setup', methods=['GET', 'POST'])
def students_setup():
    """Simplified setup - only manages groups now."""
    if current_user.role != "super_admin":
        flash("You are not authorized to access this page.", "danger")
        return redirect(url_for("admin.index"))

    # Handle add operations (groups only)
    if request.method == 'POST':
        name = request.form.get('name')

        if not name:
            flash('Group name is required.', 'error')
            return redirect(url_for('admin.students_setup'))

        # Ensure uniqueness (case-insensitive, trimmed)
        name_clean = name.strip()
        existing = Groups.query.filter(db.func.lower(Groups.name) == name_clean.lower()).first()
        if existing:
            flash('Group already exists.', 'error')
        else:
            new_group = Groups(name=name_clean)
            db.session.add(new_group)
            try:
                db.session.commit()
                flash('Group created successfully!', 'success')
                
                # Log the action
                new_log = AssistantLogs(
                    assistant_id=current_user.id,
                    action='Create',
                    log={
                        "action_name": "Create",
                        "resource_type": "group",
                        "action_details": {
                            "id": new_group.id,
                            "title": new_group.name,
                            "summary": f"New group '{new_group.name}' was created."
                        },
                        "data": {
                            "name": new_group.name
                        },
                        "before": None,
                        "after": None
                    }
                )
                db.session.add(new_log)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                flash(f'Error creating group: {str(e)}', 'error')

        return redirect(url_for('admin.students_setup'))

    # Fetch all groups with student counts
    groups_data = []
    for group in Groups.query.order_by(Groups.id).all():
        users = getattr(group, 'users', None)
        if users is not None:
            try:
                count = users.filter_by(role='student').count()
            except AttributeError:
                count = len([u for u in users if getattr(u, 'role', None) == 'student'])
        else:
            count = 0
        
        groups_data.append({
            'id': group.id,
            'name': group.name,
            'count': count
        })
    
    return render_template(
        'admin/students_setup.html',
        groups=groups_data
    )


@admin.route('/students/setup/update/<int:item_id>', methods=['POST'])
def update_entity(item_id):
    """Update a group name."""
    if current_user.role != "super_admin":
        flash("You are not authorized to update this group.", "danger")
        return redirect(url_for("admin.students_setup"))
    
    group = Groups.query.get(item_id)
    if not group:
        flash('Group not found.', 'error')
        return redirect(url_for('admin.students_setup'))
    
    new_name = request.form.get('name')
    if not new_name:
        flash('Group name is required.', 'error')
        return redirect(url_for('admin.students_setup'))
    
    new_name_clean = new_name.strip()
    
    # Check if the new name already exists (excluding the current group)
    existing = Groups.query.filter(
        db.func.lower(Groups.name) == new_name_clean.lower(),
        Groups.id != item_id
    ).first()
    
    if existing:
        flash('A group with this name already exists.', 'error')
        return redirect(url_for('admin.students_setup'))
    
    # Store old name for logging
    old_name = group.name
    
    # Update the group name
    group.name = new_name_clean
    
    try:
        db.session.commit()
        flash('Group updated successfully!', 'success')
        
        # Log the action
        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Update',
            log={
                "action_name": "Update",
                "resource_type": "group",
                "action_details": {
                    "id": group.id,
                    "title": group.name,
                    "summary": f"Group name changed from '{old_name}' to '{group.name}'."
                },
                "data": None,
                "before": {
                    "name": old_name
                },
                "after": {
                    "name": group.name
                }
            }
        )
        db.session.add(new_log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating group: {str(e)}', 'error')
    
    return redirect(url_for('admin.students_setup'))


@admin.route('/students/setup/delete/<int:item_id>', methods=['POST'])
def delete_entity(item_id):
    """Delete a group - simplified to groups only."""
    if current_user.role != "super_admin":
        flash("You are not authorized to delete this group.", "danger")
        return redirect(url_for("admin.students_setup"))
    
    group = Groups.query.get(item_id)
    if group:
        # Log the action before deleting
        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Delete',
            log={
                "action_name": "Delete",
                "resource_type": "group",
                "action_details": {
                    "id": group.id,
                    "title": group.name,
                    "summary": f"Group '{group.name}' was deleted."
                },
                "data": None,
                "before": {
                    "name": group.name
                },
                "after": None
            }
        )
        db.session.add(new_log)
        db.session.delete(group)
        db.session.commit()
        flash('Group deleted successfully!', 'success')
    else:
        flash('Group not found.', 'error')

    return redirect(url_for('admin.students_setup'))


#===============================================================================
#Sessions (Sessions have videos)
#===============================================================================


@admin.route('/api/sessions-data', methods=["GET"])
def sessions_data():
    sessions_query = get_visible_to_admin_query(Sessions, current_user)
    sessions = sessions_query.order_by(Sessions.creation_date.desc()).all()

    sessions_list = []
    for session in sessions:
        schools_names = [s.name for s in getattr(session, 'schools_mm', [])] if getattr(session, 'schools_mm', None) else []
        stages_names = [s.name for s in getattr(session, 'stages_mm', [])] if getattr(session, 'stages_mm', None) else []
        groups_names = [g.name for g in getattr(session, 'groups_mm', [])] if getattr(session, 'groups_mm', None) else []

        schools_display = ', '.join(schools_names) if schools_names else 'All Schools'
        stages_display = ', '.join(stages_names) if stages_names else 'All Stages'
        groups_display = ', '.join(groups_names) if groups_names else 'All Classes'

        # Count videos in this session
        video_count = Videos.query.filter_by(session_id=session.id).count()

        sessions_list.append({
            "id": session.id,
            "title": session.title,
            "description": session.description,
            "creation_date": session.creation_date.strftime('%Y-%m-%d %H:%M') if session.creation_date else None,
            "subject": session.subject.name if session.subject else "N/A",
            "subject_id": session.subject.id if session.subject else None,
            "schools": schools_display,
            "stages": stages_display,
            "groups": groups_display,
            "video_count": video_count
        })
    
    return jsonify(sessions_list)


@admin.route('/api/session/<int:session_id>', methods=["GET"])
def get_session_data(session_id):
    """API endpoint to fetch single session data for editing"""
    session = get_item_if_admin_can_manage(Sessions, session_id, current_user)
    if not session:
        return jsonify({"success": False, "message": "Session not found or you do not have permission to view it."}), 404

    schools_mm = [{"id": s.id, "name": s.name} for s in getattr(session, 'schools_mm', [])] if getattr(session, 'schools_mm', None) else []
    stages_mm = [{"id": s.id, "name": s.name} for s in getattr(session, 'stages_mm', [])] if getattr(session, 'stages_mm', None) else []
    groups_mm = [{"id": g.id, "name": g.name} for g in getattr(session, 'groups_mm', [])] if getattr(session, 'groups_mm', None) else []

    session_data = {
        "id": session.id,
        "title": session.title,
        "description": session.description,
        "subject": {
            "id": session.subject.id if session.subject else None,
            "name": session.subject.name if session.subject else None
        },
        "schools_mm": schools_mm,
        "stages_mm": stages_mm,
        "groups_mm": groups_mm,
    }

    return jsonify({"success": True, "session": session_data})




@admin.route("/sessions", methods=["GET", "POST"])
def manage_sessions():
    return "Paused for now"
    if request.method == "POST":


        group_ids_user, stage_ids_user, school_ids_user, subject_ids_user = get_user_scope_ids()


        sessions_query = get_visible_to_admin_query(Sessions, current_user)
        sessions = sessions_query.order_by(Sessions.creation_date.desc()).all()
        
        groups = Groups.query.filter(Groups.id.in_(group_ids_user)).all()
        stages = Stages.query.filter(Stages.id.in_(stage_ids_user)).all()
        subjects = Subjects.query.filter(Subjects.id.in_(subject_ids_user)).all()




        subject_school_map = {}
        for subject in subjects:
            # For each subject, get its associated schools and format them for JavaScript
            schools_list = [{"id": school.id, "name": school.name} for school in subject.schools]
            # Sort schools to put "Online" schools first, then alphabetically
            schools_list.sort(key=lambda school: (0 if "Online" in school["name"] else 1, school["name"].lower()))
            subject_school_map[subject.id] = schools_list






        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()

        if not title:
            flash("Session title is required.", "danger")
            return redirect(url_for("admin.manage_sessions"))


        group_id_single = int_or_none(request.form.get("group"))
        stage_id_single = int_or_none(request.form.get("stage"))
        school_id_single = int_or_none(request.form.get("school"))
        subject_id_single = int_or_none(request.form.get("subject"))

        # Handle multi-selects (matching assignment pattern)
        group_ids_mm  = [int(g) for g in request.form.getlist("groups[]") if g]
        stage_ids_mm  = [int(s) for s in request.form.getlist("stages[]") if s]
        
        # Handle new multi-select schools format (comma-separated string)
        school_ids_str = request.form.get("school_ids", "").strip()
        if school_ids_str:
            school_ids_mm = [int(s.strip()) for s in school_ids_str.split(",") if s.strip()]
        else:
            school_ids_mm = []


        if not group_id_single and not group_ids_mm:
            group_ids_mm = group_ids_user[:] if group_ids_user else [g.id for g in Groups.query.all()]
        if not stage_id_single and not stage_ids_mm:
            stage_ids_mm = stage_ids_user[:] if stage_ids_user else [s.id for s in Stages.query.all()]
            
        if not school_id_single and not school_ids_mm:
            # Default school selection logic starts here.

            # Check if a specific subject was selected from the form.
            subject_id = subject_id_single

            if subject_id and subject_id in subject_school_map:
                # If a valid subject is selected, assign all schools associated with THAT subject.
                school_ids_mm = [school['id'] for school in subject_school_map[subject_id]]
            else:
                # Fallback: If no specific subject was chosen, assign all schools
                # the user has permission for. This preserves the original "all" behavior
                # for cases where no subject is specified.
                if school_ids_user:
                    school_ids_mm = school_ids_user[:]
                else:
                    # Super-admin case: get all unique school IDs from the map
                    all_school_ids = set()
                    for schools in subject_school_map.values():
                        for school in schools:
                            all_school_ids.add(school['id'])
                    school_ids_mm = list(all_school_ids)




        if (group_id_single and group_id_single not in group_ids_user) or \
           (not can_manage(group_ids_mm, group_ids_user)):
            flash("You do not have permission for one of the selected groups.", "danger")
            return redirect(url_for("admin.manage_sessions"))
        if (stage_id_single and stage_id_single not in stage_ids_user) or \
           (not can_manage(stage_ids_mm, stage_ids_user)):
            flash("You do not have permission for one of the selected stages.", "danger")
            return redirect(url_for("admin.manage_sessions"))
        if (school_id_single and school_id_single not in school_ids_user) or \
           (not can_manage(school_ids_mm, school_ids_user)):
            flash("You do not have permission for one of the selected schools.", "danger")
            return redirect(url_for("admin.manage_sessions"))
        if subject_id_single:
            if subject_id_single not in subject_ids_user:
                flash("You do not have permission for this subject.", "danger")
                return redirect(url_for("admin.manage_sessions"))


    

        try:
            new_session = Sessions(
                title=title,
                description=description,
                added_by=current_user.id,
                groupid=group_id_single,
                stageid=stage_id_single,
                schoolid=school_id_single,
                subjectid=subject_id_single
            )
            db.session.add(new_session)


            if group_ids_mm:
                new_session.groups_mm = Groups.query.filter(Groups.id.in_(group_ids_mm)).all()
            if stage_ids_mm:
                new_session.stages_mm = Stages.query.filter(Stages.id.in_(stage_ids_mm)).all()
            if school_ids_mm:
                new_session.schools_mm = Schools.query.filter(Schools.id.in_(school_ids_mm)).all()

            db.session.commit() 


            if 'thumbnail' in request.files and request.files['thumbnail'].filename != '':
                thumbnail = request.files['thumbnail']
                filename = f'session_{new_session.id}.jpg'
                local_path = os.path.join('website/static/sessions', filename)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                thumbnail.save(local_path)
                with open(local_path, "rb") as f:
                    storage.upload_file(f, folder="sessions", file_name=filename)
    
            # AJAX response
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                session_data = {
                    "id": new_session.id,
                    "title": new_session.title,
                    "description": new_session.description,
                    "creation_date": new_session.creation_date.strftime('%Y-%m-%d %H:%M') if new_session.creation_date else None,
                    "subject": new_session.subject.name if new_session.subject else "N/A",
                    "subject_id": new_session.subject.id if new_session.subject else None,
                    "schools": ', '.join([s.name for s in new_session.schools_mm]) if new_session.schools_mm else 'All Schools',
                    "stages": ', '.join([s.name for s in new_session.stages_mm]) if new_session.stages_mm else 'All Stages',
                    "groups": ', '.join([g.name for g in new_session.groups_mm]) if new_session.groups_mm else 'All Classes',
                    "video_count": 0
                }
                return jsonify({"success": True, "message": "Session created successfully!", "session": session_data})
            
            flash("Session created successfully!", "success")
        except Exception as e:
            db.session.rollback()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": False, "message": f"Error creating session: {str(e)}"}), 500
            flash(f"Error creating session: {str(e)}", "danger")

        return redirect(url_for("admin.manage_sessions"))

    return render_template("admin/sessions/manage_sessions.html")


@admin.route("/session/<int:session_id>", methods=["GET", "POST"])
def session_details(session_id):
    return "Paused for now"
    """
    Displays a single session and its videos.
    Handles adding new videos TO THIS session.
    """
    session = get_item_if_admin_can_manage(Sessions, session_id, current_user)
    if not session:
        flash("Session not found or you don't have permission.", "danger")
        return redirect(url_for("admin.manage_sessions"))

    if request.method == "POST":

        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        video_url = (request.form.get("video_url") or "").strip()

        if not title or not video_url:
            flash("Video title and URL are required.", "danger")
            return redirect(url_for("admin.session_details", session_id=session_id))


        video_url = re.sub(r'&list=[^&]+', '', video_url)
        video_url = re.sub(r'\?list=[^&]+$', '', video_url)

        try:
            new_video = Videos(
                title=title,
                description=description,
                video_url=video_url,
                session_id=session.id  
            )
            db.session.add(new_video)
            db.session.commit()
            flash("Video added successfully!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding video: {str(e)}", "danger")

        return redirect(url_for("admin.session_details", session_id=session_id))

    return render_template("admin/sessions/session_details.html", session=session)

@admin.route("/session/edit/<int:session_id>", methods=["GET", "POST"])
def edit_session(session_id):
    return "Paused for now"

    """
    Handles editing the details and scope of a session.
    """
    session = get_item_if_admin_can_manage(Sessions, session_id, current_user)
    if not session:
        flash("Session not found or you don't have permission.", "danger")
        return redirect(url_for("admin.manage_sessions"))

    # Get data needed for both GET and POST
    group_ids_user, stage_ids_user, school_ids_user, subject_ids_user = get_user_scope_ids()
    groups = Groups.query.filter(Groups.id.in_(group_ids_user)).all()
    stages = Stages.query.filter(Stages.id.in_(stage_ids_user)).all()
    schools = Schools.query.filter(Schools.id.in_(school_ids_user)).all()
    subjects = Subjects.query.filter(Subjects.id.in_(subject_ids_user)).all()

    if request.method == "POST":
        session.title = (request.form.get("title") or "").strip()
        session.description = (request.form.get("description") or "").strip()

        # Update scopes
        session.groupid = int_or_none(request.form.get("group"))
        session.stageid = int_or_none(request.form.get("stage"))
        session.schoolid = int_or_none(request.form.get("school"))
        session.subjectid = int_or_none(request.form.get("subject"))

        # Handle multi-selects (matching assignment pattern)
        group_ids_mm  = [int(g) for g in request.form.getlist("groups[]") if g]
        stage_ids_mm  = [int(s) for s in request.form.getlist("stages[]") if s]
        
        # Handle new multi-select schools format (comma-separated string)
        school_ids_str = request.form.get("school_ids", "").strip()
        if school_ids_str:
            school_ids_mm = [int(s.strip()) for s in school_ids_str.split(",") if s.strip()]
        else:
            school_ids_mm = []

        if not group_ids_mm:
            groups = Groups.query.all()
            group_ids_mm = [group.id for group in groups]

        if not stage_ids_mm:
            stages = Stages.query.all()
            stage_ids_mm = [stage.id for stage in stages]
        
        if not school_ids_mm:
            schools = Schools.query.all()
            school_ids_mm = [school.id for school in schools]

        # Update many-to-many relationships
        session.groups_mm = Groups.query.filter(Groups.id.in_(group_ids_mm)).all() if group_ids_mm else []
        session.stages_mm = Stages.query.filter(Stages.id.in_(stage_ids_mm)).all() if stage_ids_mm else []
        session.schools_mm = Schools.query.filter(Schools.id.in_(school_ids_mm)).all() if school_ids_mm else []

        # Handle thumbnail upload
        if 'thumbnail' in request.files and request.files['thumbnail'].filename != '':
            thumbnail = request.files['thumbnail']
            filename = f'session_{session.id}.jpg'
            local_path = os.path.join('website/static/sessions', filename)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            thumbnail.save(local_path)
            with open(local_path, "rb") as f:
                storage.upload_file(f, folder="sessions", file_name=filename)

        db.session.commit()
        
        # AJAX response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            session_data = {
                "id": session.id,
                "title": session.title,
                "description": session.description,
                "creation_date": session.creation_date.strftime('%Y-%m-%d %H:%M') if session.creation_date else None,
                "subject": session.subject.name if session.subject else "N/A",
                "subject_id": session.subject.id if session.subject else None,
                "schools": ', '.join([s.name for s in session.schools_mm]) if session.schools_mm else 'All Schools',
                "stages": ', '.join([s.name for s in session.stages_mm]) if session.stages_mm else 'All Stages',
                "groups": ', '.join([g.name for g in session.groups_mm]) if session.groups_mm else 'All Classes',
                "video_count": Videos.query.filter_by(session_id=session.id).count()
            }
            return jsonify({"success": True, "message": "Session updated successfully!", "session": session_data})
        
        flash("Session updated successfully!", "success")
        return redirect(url_for("admin.manage_sessions"))

    return render_template("admin/sessions/edit_session.html", session=session,
                           groups=groups, stages=stages, schools=schools, subjects=subjects)


@admin.route("/session/<int:session_id>/delete", methods=["POST"])
def delete_session(session_id):
    return "Paused for now"
    """
    Deletes a session and all its associated videos.
    """
    session = get_item_if_admin_can_manage(Sessions, session_id, current_user)
    if not session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Session not found or you don't have permission."}), 404
        flash("Session not found or you don't have permission.", "danger")
        return redirect(url_for("admin.manage_sessions"))

    try:
        filename = f'session_{session.id}.jpg'
        local_path = os.path.join('website/static/sessions', filename)
        if os.path.exists(local_path):
            os.remove(local_path)
        storage.delete_file(folder="sessions", file_name=filename)

        db.session.delete(session)
        db.session.commit()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True, "message": "Session and all its videos were deleted."})
        
        flash("Session and all its videos were deleted.", "success")
    except Exception as e:
        db.session.rollback()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": f"Error deleting session: {str(e)}"}), 500
        flash(f"Error deleting session: {str(e)}", "danger")

    return redirect(url_for("admin.manage_sessions"))


#=================================================================
#Video Management Routes (Updated)
#=================================================================

@admin.route("/videos/edit/<int:video_id>", methods=["GET", "POST"])
def manage_videos(video_id):
    return "Paused for now"

    """
    Edits an individual video. Now redirects back to its parent session.
    Scope management is removed as it's handled by the session.
    """
    video = get_item_if_admin_can_manage(Videos, video_id, current_user)
    if not video:
        flash("Video not found or you do not have permission to edit it.", "danger")
        return redirect(url_for("admin.manage_sessions")) # Fallback redirect

    if request.method == "POST":
        video.title = (request.form.get("title") or "").strip()
        video.description = (request.form.get("description") or "").strip()
        video.video_url = (request.form.get("video_url") or "").strip()

        if not video.title or not video.video_url:
            flash("Title and video URL are required.", "danger")
            return redirect(url_for("admin.edit_video", video_id=video_id))


        
        db.session.commit()

        flash("Video updated successfully!", "success")
        return redirect(url_for("admin.session_details", session_id=video.session_id))

    return render_template("admin/edit_video.html", video=video)


@admin.route("/videos/<int:video_id>/delete", methods=["POST"])
def delete_video(video_id):
    return "Paused for now"

    """
    Deletes a single video. Now redirects back to its parent session.
    """
    video = get_item_if_admin_can_manage(Videos, video_id, current_user)
    if not video:
        flash("Video not found or you do not have permission to delete it.", "danger")
        return redirect(url_for("admin.manage_sessions")) 

    session_id_redirect = video.session_id


    db.session.delete(video)
    db.session.commit()
    flash("Video deleted successfully!", "success")
    return redirect(url_for("admin.session_details", session_id=session_id_redirect))

@admin.route("/videos/play/<int:video_id>")
def play_videos(video_id):
    return "Paused for now"

    video = Videos.query.get_or_404(video_id)
    return render_template("admin/video_play.html", video=video)

#=================================================================
#Attendance
#=================================================================

def get_qualified_students_for_attendance_session(session):
    """
    Returns a list of students qualified for this attendance session.
    Uses MM relationships if present, else legacy FKs, else global.
    """
    base_filters = [
        Users.role == "student",
        Users.code != 'nth',
        Users.code != 'Nth',
    ]

    mm_group_ids  = [g.id for g in getattr(session, "groups_mm", [])]
    mm_stage_ids  = [s.id for s in getattr(session, "stages_mm", [])]
    mm_school_ids = [s.id for s in getattr(session, "schools_mm", [])]

    filters = list(base_filters)

    if mm_group_ids:
        filters.append(Users.groupid.in_(mm_group_ids))
    elif session.groupid:
        filters.append(Users.groupid == session.groupid)

    if mm_stage_ids:
        filters.append(Users.stageid.in_(mm_stage_ids))
    elif session.stageid:
        filters.append(Users.stageid == session.stageid)

    if mm_school_ids:
        filters.append(Users.schoolid.in_(mm_school_ids))
    elif session.schoolid:
        filters.append(Users.schoolid == session.schoolid)

    if getattr(session, "subjectid", None):
        filters.append(Users.subjectid == session.subjectid)

    return Users.query.filter(and_(*filters)).all()


@admin.route('/api/attendance-data', methods=["GET"])
def attendance_data():
    return "Paused for now"

    attendance_query = get_visible_to_admin_query(Attendance_session, current_user)
    sessions = attendance_query.order_by(Attendance_session.session_date.desc()).all()

    sessions_list = []
    for session in sessions:
        schools_names = [s.name for s in getattr(session, 'schools_mm', [])] if getattr(session, 'schools_mm', None) else []
        stages_names = [s.name for s in getattr(session, 'stages_mm', [])] if getattr(session, 'stages_mm', None) else []
        groups_names = [g.name for g in getattr(session, 'groups_mm', [])] if getattr(session, 'groups_mm', None) else []

        schools_display = ', '.join(schools_names) if schools_names else 'All Schools'
        stages_display = ', '.join(stages_names) if stages_names else 'All Stages'
        groups_display = ', '.join(groups_names) if groups_names else 'All Classes'

        # Calculate stats
        qualified_students = get_qualified_students_for_attendance_session(session)
        num_entitled = len(qualified_students)
        
        # Count students marked as present (attendance_status = 'present')
        num_present = Attendance_student.query.filter_by(
            attendance_session_id=session.id,
            attendance_status='present'
        ).count()
        
        # Count students marked as absent (attendance_status = 'absent')
        num_absent = Attendance_student.query.filter_by(
            attendance_session_id=session.id,
            attendance_status='absent'
        ).count()

        sessions_list.append({
            "id": session.id,
            "title": session.title,
            "session_date": session.session_date.strftime('%Y-%m-%d %I:%M %p') if session.session_date else None,
            "session_date_iso": session.session_date.strftime('%Y-%m-%dT%H:%M') if session.session_date else None,
            "subject": session.subject.name if session.subject else "N/A",
            "subject_id": session.subject.id if session.subject else None,
            "schools": schools_display,
            "stages": stages_display,
            "groups": groups_display,
            "stats": {
                "present": num_present,
                "absent": num_absent,
                "total": num_entitled
            },
            "points": session.points,
        })
    
    return jsonify(sessions_list)


@admin.route('/api/attendance/<int:session_id>', methods=["GET"])
def get_attendance_data(session_id):
    return "Paused for now"

    """API endpoint to fetch single attendance session data for editing"""
    session = get_item_if_admin_can_manage(Attendance_session, session_id, current_user)
    if not session:
        return jsonify({"success": False, "message": "Attendance session not found or you do not have permission to view it."}), 404

    schools_mm = [{"id": s.id, "name": s.name} for s in getattr(session, 'schools_mm', [])] if getattr(session, 'schools_mm', None) else []
    stages_mm = [{"id": s.id, "name": s.name} for s in getattr(session, 'stages_mm', [])] if getattr(session, 'stages_mm', None) else []
    groups_mm = [{"id": g.id, "name": g.name} for g in getattr(session, 'groups_mm', [])] if getattr(session, 'groups_mm', None) else []

    session_data = {
        "id": session.id,
        "title": session.title,
        "session_date_iso": session.session_date.strftime('%Y-%m-%dT%H:%M') if session.session_date else None,
        "subject": {
            "id": session.subject.id if session.subject else None,
            "name": session.subject.name if session.subject else None
        },
        "schools_mm": schools_mm,
        "stages_mm": stages_mm,
        "groups_mm": groups_mm,
    }

    return jsonify({"success": True, "session": session_data})









@admin.route("/attendance", methods=["GET", "POST"])
def attendance():

    return "Paused for now"



    if request.method == "POST":


        attendance_query = get_visible_to_admin_query(Attendance_session, current_user)

        groups = Groups.query.filter(Groups.id.in_([g.id for g in current_user.managed_groups])).all()
        stages = Stages.query.filter(Stages.id.in_([s.id for s in current_user.managed_stages])).all()
        schools = Schools.query.filter(Schools.id.in_([s.id for s in current_user.managed_schools])).all()
        subjects = Subjects.query.filter(Subjects.id.in_([s.id for s in current_user.managed_subjects])).all()

        subject_school_map = {}
        for subject in subjects:
            # For each subject, get its associated schools and format them for JavaScript
            schools_list = [{"id": school.id, "name": school.name} for school in subject.schools]
            # Sort schools to put "Online" schools first, then alphabetically
            schools_list.sort(key=lambda school: (0 if "Online" in school["name"] else 1, school["name"].lower()))
            subject_school_map[subject.id] = schools_list






        # Create a new attendance session
        title = request.form.get("title")
        group_ids = request.form.getlist("groups[]")
        stage_ids = request.form.getlist("stages[]")
        school_ids = request.form.getlist("schools[]")
        subject_id = request.form.get("subject_id")
        points_raw = request.form.get("points", 0)
        points = int(points_raw) if str(points_raw).isdigit() else 0

        try :
            subject_id = int(subject_id)
        except ValueError:
            flash("Invalid subject.", "danger")
            return redirect(url_for("admin.attendance"))

        if not group_ids:
            group_ids = [g.id for g in groups]
        if not stage_ids:
            stage_ids = [s.id for s in stages]

        if not school_ids:
            if subject_id and subject_id in subject_school_map:
                school_ids = [school['id'] for school in subject_school_map[subject_id]]
            else:
                flash("Choose a subject" , "danger")
                return redirect(url_for("admin.attendance"))
        if not subject_id:
            flash("You must select a subject.", "danger")
            return redirect(url_for("admin.attendance"))



        if group_ids:
            if not can_manage(group_ids, [g.id for g in groups]):
                flash("You are not allowed to post to one or more selected groups.", "danger")
                return redirect(url_for("admin.attendance"))

        if stage_ids:
            if not can_manage(stage_ids, [s.id for s in stages]):
                flash("You are not allowed to post to one or more selected stages.", "danger")
                return redirect(url_for("admin.attendance"))

        if school_ids:
            if not can_manage(school_ids, [s.id for s in schools]):
                flash("You are not allowed to post to one or more selected schools.", "danger")
                return redirect(url_for("admin.attendance"))

        if subject_id:
            if subject_id not in [s.id for s in subjects]:
                flash("You are not allowed to post to this subject.", "danger")
                return redirect(url_for("admin.attendance"))

        try:
            session_date = parse_deadline(request.form.get("session_date", ""))
        except (TypeError, ValueError):
            flash("Invalid deadline date. Please use the datetime picker.", "error")
            return redirect(url_for("admin.attendance"))

        new_session = Attendance_session(
            title=title,
            session_date=session_date,
            subjectid=subject_id,
            points=points,
        )
        db.session.add(new_session)

        if group_ids:
            new_session.groups_mm = Groups.query.filter(Groups.id.in_(group_ids)).all()
        if stage_ids:
            new_session.stages_mm = Stages.query.filter(Stages.id.in_(stage_ids)).all()
        if school_ids:
            new_session.schools_mm = Schools.query.filter(Schools.id.in_(school_ids)).all()

        db.session.commit()
        
        # AJAX response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # Calculate stats
            qualified_students = get_qualified_students_for_attendance_session(new_session)
            num_entitled = len(qualified_students)
            num_marked = Attendance_student.query.filter_by(attendance_session_id=new_session.id).count()
            
            session_data = {
                "id": new_session.id,
                "title": new_session.title,
                "session_date": new_session.session_date.strftime('%Y-%m-%d %I:%M %p') if new_session.session_date else None,
                "session_date_iso": new_session.session_date.strftime('%Y-%m-%dT%H:%M') if new_session.session_date else None,
                "subject": new_session.subject.name if new_session.subject else "N/A",
                "subject_id": new_session.subject.id if new_session.subject else None,
                "schools": ', '.join([s.name for s in new_session.schools_mm]) if new_session.schools_mm else 'All Schools',
                "stages": ', '.join([s.name for s in new_session.stages_mm]) if new_session.stages_mm else 'All Stages',
                "groups": ', '.join([g.name for g in new_session.groups_mm]) if new_session.groups_mm else 'All Classes',
                "stats": {
                    "present": num_marked,
                    "absent": num_entitled - num_marked,
                    "total": num_entitled
                },
                "points": new_session.points,
            }
            return jsonify({"success": True, "message": "Attendance session created successfully!", "session": session_data})
        
        flash("Attendance session created.", "success")
        return redirect(url_for("admin.attendance"))



    return render_template(
        "admin/attendance/attendance.html")

@admin.route("/attendance/edit/<int:session_id>", methods=["GET", "POST"])
def edit_attendance_session(session_id):
    return "Paused for now"

    session = get_item_if_admin_can_manage(Attendance_session, session_id, current_user)

    if not session:
        flash("Attendance session not found or you do not have permission to edit it.", "danger")
        return redirect(url_for("admin.attendance"))

    groups = Groups.query.all()
    stages = Stages.query.all()
    schools = Schools.query.all()
    subjects = Subjects.query.all()

    if request.method == "POST":
        session.title = request.form.get("title")
        session_date = request.form.get("session_date")
        group_ids = request.form.getlist("groups[]")
        stage_ids = request.form.getlist("stages[]")
        school_ids_str = request.form.get("school_ids", "")
        school_ids = [id.strip() for id in school_ids_str.split(",") if id.strip()]
        subject_id = request.form.get("subject_id")

        try:
            session_date = parse_deadline(request.form.get("session_date", ""))
        except (TypeError, ValueError):
            flash("Invalid deadline date. Please use the datetime picker.", "error")
            return redirect(url_for("admin.edit_attendance_session", session_id=session_id))

        if not group_ids:
            group_ids = [g.id for g in groups]
        if not stage_ids:
            stage_ids = [s.id for s in stages]
        if not school_ids:
            school_ids = [s.id for s in schools]
        if not subject_id:
            flash("You must select a subject.", "danger")
            return redirect(url_for("admin.edit_attendance_session", session_id=session_id))

        try :
            subject_id = int(subject_id)
        except ValueError:
            flash("Invalid subject.", "danger")
            return redirect(url_for("admin.edit_attendance_session", session_id=session_id))

        session.session_date = session_date
        session.subjectid = subject_id
        session.groups_mm = Groups.query.filter(Groups.id.in_(group_ids)).all()
        session.stages_mm = Stages.query.filter(Stages.id.in_(stage_ids)).all() 
        session.schools_mm = Schools.query.filter(Schools.id.in_(school_ids)).all() 

        db.session.commit()
        
        # AJAX response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # Calculate stats
            qualified_students = get_qualified_students_for_attendance_session(session)
            num_entitled = len(qualified_students)
            num_marked = Attendance_student.query.filter_by(attendance_session_id=session.id).count()
            
            session_data = {
                "id": session.id,
                "title": session.title,
                "session_date": session.session_date.strftime('%Y-%m-%d %I:%M %p') if session.session_date else None,
                "session_date_iso": session.session_date.strftime('%Y-%m-%dT%H:%M') if session.session_date else None,
                "subject": session.subject.name if session.subject else "N/A",
                "subject_id": session.subject.id if session.subject else None,
                "schools": ', '.join([s.name for s in session.schools_mm]) if session.schools_mm else 'All Schools',
                "stages": ', '.join([s.name for s in session.stages_mm]) if session.stages_mm else 'All Stages',
                "groups": ', '.join([g.name for g in session.groups_mm]) if session.groups_mm else 'All Classes',
                "stats": {
                    "present": num_marked,
                    "absent": num_entitled - num_marked,
                    "total": num_entitled
                }
            }
            return jsonify({"success": True, "message": "Attendance session updated successfully!", "session": session_data})
        
        flash("Attendance session updated.", "success")
        return redirect(url_for("admin.attendance"))

    return render_template("admin/attendance/edit_session.html", session=session, groups=groups, stages=stages, schools=schools, subjects=subjects)

@admin.route("/attendance/delete/<int:session_id>", methods=["POST"])
def delete_attendance_session(session_id):
    return "Paused for now"

    session = Attendance_session.query.get_or_404(session_id)
    students = session.attendance_student.all()
    try:
        for student in students:
            #delete points
            student.student.points = student.student.points - session.points
            db.session.commit()
            db.session.delete(student)
        db.session.delete(session)
        db.session.commit()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True, "message": "Attendance session deleted successfully!"})
        
        flash("Attendance session deleted.", "success")
    except Exception as e:
        db.session.rollback()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": f"Error deleting session: {str(e)}"}), 500
        flash(f"Error deleting session: {str(e)}", "danger")
    
    return redirect(url_for("admin.attendance"))

@admin.route("/attendance/<int:session_id>", methods=["GET", "POST"])
def attendance_session_detail(session_id):
    return "Paused for now"

    session = Attendance_session.query.get_or_404(session_id)
    students = get_qualified_students_for_attendance_session(session)

    if request.method == "POST":
        for student in students:
            status = request.form.get(f"status_{student.id}", "absent")
            record = Attendance_student.query.filter_by(attendance_session_id=session.id, student_id=student.id).first()
            if not record:
                record = Attendance_student(attendance_session_id=session.id, student_id=student.id)
                db.session.add(record)
            record.attendance_status = status
        db.session.commit()
        flash("Attendance updated.", "success")
        return redirect(url_for("admin.attendance_session_detail", session_id=session.id))

    # Separate students with and without attendance records
    attendance_records = {a.student_id: a.attendance_status for a in Attendance_student.query.filter_by(attendance_session_id=session.id).all()}
    students_without_records = [s for s in students if s.id not in attendance_records]
    students_with_records = [s for s in students if s.id in attendance_records]
    
    return render_template("admin/attendance/session_detail.html", 
                         session=session, 
                         students_without_records=students_without_records,
                         students_with_records=students_with_records,
                         attendance_map=attendance_records)


#=================================================================
#Online Exam
#=================================================================


@admin.route("/online/exam", methods=["GET", "POST"])
def online_exam():

    if request.method == "POST":

        # --- Ensure admin has access to see exams ---
        assignments_query = get_visible_to_admin_query(Assignments, current_user)
        groups = Groups.query.filter(Groups.id.in_([g.id for g in current_user.managed_groups])).all()
        

        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()

        student_whatsapp = request.form.get("student_whatsapp", False)
        if student_whatsapp == "true":
            student_whatsapp = True
        else:
            student_whatsapp = False
        parent_whatsapp = request.form.get("parent_whatsapp", False)
        if parent_whatsapp == "true":
            parent_whatsapp = True
        else:
            parent_whatsapp = False


        close_after_deadline = request.form.get("close_after_deadline", False)
        if close_after_deadline == "true":
            close_after_deadline = True
        else:
            close_after_deadline = False

        # Check for locked_group_id (from group-specific exam page)
        locked_group_id = request.form.get("locked_group_id")
        if locked_group_id:
            locked_group_id = int(locked_group_id) if str(locked_group_id).isdigit() else None

        group_ids = parse_multi_ids("groups[]")

        # If locked_group_id is set, enforce it and ignore any other group selections
        if locked_group_id:
            # Validate that the user can manage this group
            if not can_manage([locked_group_id], [g.id for g in groups]):
                flash("You are not allowed to post to this group.", "danger")
                wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
                if wants_json:
                    return jsonify({"success": False, "message": "You are not allowed to post to this group."}), 403
                return redirect(url_for("admin.online_exam"))
            group_ids = [locked_group_id]
        else:
            if not group_ids:
                group_ids = [g.id for g in groups]

            if group_ids:
                if not can_manage(group_ids, [g.id for g in groups]):
                    flash("You are not allowed to post to one or more selected groups.", "danger")
                    return redirect(url_for("admin.online_exam"))

        # deadline
        try:
            deadline_date = parse_deadline(request.form.get("deadline_date", ""))
        except (TypeError, ValueError):
            flash("Invalid deadline date. Please use the datetime picker.", "error")
            return redirect(url_for("admin.online_exam"))

        # points (int)
        points_raw = request.form.get("points", 0)
        points = int(points_raw) if str(points_raw).isdigit() else 0

        out_of_raw = request.form.get("out_of", 0)
        out_of = int(out_of_raw) if str(out_of_raw).isdigit() else 0

        # Process new attachment format
        upload_dir = "website/assignments/uploads/"
        attachments = []
        os.makedirs(upload_dir, exist_ok=True)

        # Get all attachment indices
        attachment_indices = []
        for key in request.form.keys():
            if key.startswith('attachments[') and '][name]' in key:
                index = key.split('[')[1].split(']')[0]
                if index not in attachment_indices:
                    attachment_indices.append(index)
        
        # Process each attachment
        for idx in attachment_indices:
            attachment_name = request.form.get(f'attachments[{idx}][name]')
            attachment_type = request.form.get(f'attachments[{idx}][type]')
            
            if not attachment_name:
                continue
                
            attachment_obj = {
                'name': attachment_name,
                'type': attachment_type
            }
            
            if attachment_type == 'file':
                file = request.files.get(f'attachments[{idx}][file]')
                if file and file.filename:
                    original_filename = secure_filename(file.filename)
                    filename = f"{uuid.uuid4().hex}_{original_filename}"
                    file_path = os.path.join(upload_dir, filename)
                    file.save(file_path)
                    try:
                        with open(file_path, "rb") as f:
                            storage.upload_file(f, folder="assignments/uploads", file_name=filename)
                    except Exception as e:
                        flash(f"Error uploading file to storage: {str(e)}", "danger")
                        return redirect(url_for("admin.online_exam"))
                    attachment_obj['url'] = f"/student/assignments/uploads/{filename}"
                    attachments.append(attachment_obj)
            elif attachment_type == 'link':
                attachment_url = request.form.get(f'attachments[{idx}][url]')
                if attachment_url:
                    attachment_obj['url'] = attachment_url
                    attachments.append(attachment_obj)

        cairo_tz = pytz.timezone('Africa/Cairo')
        aware_local_time = datetime.now(cairo_tz)
        naive_local_time = aware_local_time.replace(tzinfo=None)

        # ---- create and persist
        new_exam = Assignments(
            title=title,
            description=description,
            deadline_date=deadline_date,
            attachments=json.dumps(attachments),
            points=points,
            type="Exam",
            creation_date=naive_local_time,
            created_by=current_user.id,
            out_of=out_of,
            student_whatsapp=student_whatsapp,
            parent_whatsapp=parent_whatsapp,
            close_after_deadline=close_after_deadline,
        )

        # IMPORTANT: add to session BEFORE assigning M2M relations
        db.session.add(new_exam)

        new_exam.groups_mm = Groups.query.filter(Groups.id.in_(group_ids)).all()

        db.session.commit()

        # --- LOGGING: Add log for exam creation
        try:
            new_log = AssistantLogs(
                assistant_id=current_user.id,
                action='Create',
                log={
                    "action_name": "Create",
                    "resource_type": "exam",
                    "action_details": {
                        "id": new_exam.id,
                        "title": new_exam.title,
                        "summary": f"Exam '{new_exam.title}' was created."
                    },
                    "data": {
                        "title": new_exam.title,
                        "description": new_exam.description,
                        "deadline_date": str(new_exam.deadline_date) if new_exam.deadline_date else None,
                        "groupid": new_exam.groupid,
                        "groups": [g.id for g in getattr(new_exam, "groups_mm", [])],
                        "attachments": json.loads(new_exam.attachments) if new_exam.attachments else [],
                        "points": new_exam.points,
                        "out_of": new_exam.out_of,
                        "student_whatsapp": new_exam.student_whatsapp,
                        "parent_whatsapp": new_exam.parent_whatsapp,
                        "close_after_deadline": new_exam.close_after_deadline,
                    },
                    "before": None,
                    "after": None
                }
            )
            db.session.add(new_log)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash("Exam added, but failed to log the action.", "warning")

        # Return JSON for AJAX requests
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            groups_names = [g.name for g in getattr(new_exam, 'groups_mm', [])] if getattr(new_exam, 'groups_mm', None) else []
            
            qualified_count = qualified_students_count_for_assignment(new_exam)
            
            exam_data = {
                "id": new_exam.id,
                "title": new_exam.title,
                "description": new_exam.description,
                "creation_date": new_exam.creation_date.strftime('%Y-%m-%d %I:%M %p') if new_exam.creation_date else None,
                "deadline_date": new_exam.deadline_date.strftime('%Y-%m-%d %I:%M %p') if new_exam.deadline_date else None,
                "groups": groups_names,
                "points": new_exam.points,
                "status": new_exam.status,
                "submitted_students_count": 0,
                "qualified_students_count": qualified_count,
                "out_of": new_exam.out_of,
                "student_whatsapp": new_exam.student_whatsapp,
                "parent_whatsapp": new_exam.parent_whatsapp,
                "close_after_deadline": new_exam.close_after_deadline,
            }
            
            return jsonify({"success": True, "message": "Exam added successfully!", "exam": exam_data})

        flash("Exam added successfully!", "success")
        return redirect(url_for("admin.online_exam"))



    return render_template(
        "admin/online_exam/online_exam.html"
    )

@admin.route("/online/exam/submissions/<int:exam_id>", methods=["GET", "POST"])
def view_exam_submissions(exam_id):
    exam = get_item_if_admin_can_manage(Assignments, exam_id, current_user)
    
    # Get optional group_id from query params for back navigation
    group_id = request.args.get('group_id', type=int)
    group = None
    if group_id:
        group = Groups.query.get(group_id)
    else:
        # If exam has exactly 1 group, use it; otherwise None
        exam_groups = list(getattr(exam, 'groups_mm', []))
        if len(exam_groups) == 1:
            group_id = exam_groups[0].id
        else:
            group_id = None



        

    if not exam:
        flash("Exam not found or you do not have permission to view its submissions.", "danger")
        return redirect(url_for("admin.online_exam"))

    if not exam.type == "Exam":
        # flash("Assignment is not an exam.", "danger")
        # return redirect(url_for("admin.online_exam"))
        return redirect(url_for("admin.view_assignment_submissions", assignment_id=exam_id))

    if request.method == "POST":
        submission_id = request.form.get("submission_id")
        mark = request.form.get("mark")
        submission = Submissions.query.get_or_404(submission_id)
        
        # Check if mark is being changed
        mark_changed = submission.mark != mark
        
        submission.mark = mark
        
        # If super_admin is updating, auto-approve and send notifications
        if current_user.role == "super_admin":
            if mark_changed:
                submission.corrected = True
                submission.corrected_by_id = current_user.id
                submission.correction_date = datetime.now(GMT_PLUS_2)
                submission.reviewed = True
                submission.reviewed_by_id = current_user.id
                submission.review_date = datetime.now(GMT_PLUS_2)
                
                # Send WhatsApp notifications immediately
                try:
                    send_whatsapp_message(
                        submission.student.phone_number,
                        (
                            f"Hi *{submission.student.name}*👋,\n\n"
                            f"*{exam.title}*\n"
                            "We have received your quiz submission ✅\n\n"
                            "Thank you for your dedication! ☺"
                        )
                    )

                    send_whatsapp_message(
                        submission.student.parent_phone_number,
                        (
                            f"Dear Parent,\n"
                            f"*{submission.student.name}*\n\n"
                            f"Quiz *{exam.title}* on "
                            f"{exam.deadline_date.strftime('%d/%m/%Y') if exam.deadline_date else 'N/A'} correction is returned on the student's account on website\n\n"
                            f"Scored *{submission.mark if submission.mark else 'N/A'}* / "
                            f"{exam.out_of if hasattr(exam, 'out_of') else 'N/A'}\n\n"
                            f"Dr. Adham will send the gradings on the group\n"
                            f"_For further inquiries send to Dr. Adham_"
                        )
                    )
                
                except:
                    pass
                
                flash("Grade updated and notifications sent!", "success")
            else:
                flash("Grade updated successfully!", "success")
        # If assistant/admin is updating, require review
        else:
            if mark_changed and submission.reviewed:
                submission.reviewed = False
                submission.corrected_by_id = current_user.id
                submission.correction_date = datetime.now(GMT_PLUS_2)
                flash("Grade updated! Awaiting Head review before student notification.", "info")
            elif mark_changed:
                submission.corrected_by_id = current_user.id
                submission.correction_date = datetime.now(GMT_PLUS_2)
                flash("Grade updated! Awaiting Head review.", "success")
            else:
                flash("Grade updated successfully!", "success")
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating grade: {str(e)}", "danger")
        return redirect(url_for("admin.view_exam_submissions", exam_id=exam_id))

    # ✅ Subquery of qualified student IDs (scoped to current admin)
    qualified_students_subq = (
        get_qualified_students_query(exam, current_user.id)
        .with_entities(Users.id)
        .subquery()
    )

    # ✅ Build submissions query
    submissions_query = (
        Submissions.query
        .join(Users, Submissions.student_id == Users.id)
        .filter(Submissions.assignment_id == exam_id)
        .filter(Submissions.student_id.in_(db.select(qualified_students_subq)))
    )
    
    # ✅ For assistants (not super_admin), only show assigned submissions
    if current_user.role != "super_admin":
        submissions_query = submissions_query.filter(
            Submissions.assigned_to_id == current_user.id
        )
    
    # ✅ Get submissions sorted alphabetically by student name
    submissions = submissions_query.order_by(Users.name).all()

    # ✅ All qualified students (for template use)
    all_qualified_students = (
        get_qualified_students_query(exam, current_user.id).all()
    )

    # ✅ Students who have submitted (set of IDs)
    submitted_student_ids = {sub.student_id for sub in submissions}

    # ✅ Students who have NOT submitted (sorted alphabetically)
    not_submitted_students = sorted(
        [student for student in all_qualified_students if student.id not in submitted_student_ids],
        key=lambda s: s.name
    )


    whatsapp_notifications = Assignments_whatsapp.query.filter_by(
        assignment_id=exam_id
    ).all()

    notification_status = {notif.user_id: notif.message_sent for notif in whatsapp_notifications}

    # Get all assistants (for super admin assignment panel)
    assistants = []
    if current_user.role == "super_admin":
        assistants = Users.query.filter(
            (Users.role == 'admin') | (Users.role == 'super_admin'),
            Users.id != current_user.id
        ).order_by(Users.name).all()

    # Calculate statistics for assignment panel
    total_submissions = len(submissions)
    unassigned_count = len([s for s in submissions if s.assigned_to_id is None])
    approved_count = len([s for s in submissions if s.reviewed])
    waiting_approval_count = len([s for s in submissions if s.corrected and not s.reviewed])
    not_corrected_assigned = len([s for s in submissions if not s.corrected and s.assigned_to_id is not None])

    return render_template(
        "admin/online_exam/exam_submissions.html", 
        exam=exam, 
        submissions=submissions,
        not_submitted_students=not_submitted_students,
        notification_status=notification_status,
        group_id=group_id,
        group=group,
        submitted_student_ids = submitted_student_ids,
        assistants=assistants,
        total_submissions=total_submissions,
        unassigned_count=unassigned_count,
        approved_count=approved_count,
        waiting_approval_count=waiting_approval_count,
        not_corrected_assigned=not_corrected_assigned,
    )


#Send late message for a submission (Per student) (Admin route)
@admin.route("/online/exam/<int:assignment_id>/submissions/<int:student_id>/late", methods=["POST"])
def send_late_message_for_submission_exam(assignment_id, student_id):
    assignment = Assignments.query.get_or_404(assignment_id)
    student = Users.query.get(student_id)
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("admin.view_exam_submissions", assignment_id=assignment_id))

    # Check if message was already sent
    existing_notification = Assignments_whatsapp.query.filter_by(
        assignment_id=assignment_id,
        user_id=student_id
    ).first()

    if existing_notification and existing_notification.message_sent:
        return jsonify({'status': 'info', 'message': 'Message was already sent to this student.'})

    student_late_message_sent = False
    parent_late_message_sent = False

    if not student.student_whatsapp and not student.parent_whatsapp:
        return jsonify({'status': 'info', 'message': 'Student has no WhatsApp number or parent WhatsApp number.'})

    try:

        send_whatsapp_message(student.phone_number, 
            f"HI *{student.name}*\n\n"
            f"*{assignment.title}*\n"
            f"Submission is missing\n"
            f"Didn't submit\n\n"
            f"Please take care to submit your future assignments"
        )
        student_late_message_sent = True

        send_whatsapp_message(
            student.parent_phone_number,
            f"Dear Parent,\n"
            f"*{student.name}*\n\n"
            f"Quiz *{assignment.title}* due on *{assignment.deadline_date.strftime('%d/%m/%Y') if hasattr(assignment, 'deadline_date') and assignment.deadline_date else 'N/A'}*: Did not submit\n\n"
            f"_For further inquiries send to Dr. Adham_"
        )
        parent_late_message_sent = True
        # Record the sent message
        if student_late_message_sent or parent_late_message_sent:
            if existing_notification:
                existing_notification.message_sent = True
                existing_notification.sent_date = datetime.now(GMT_PLUS_2)
            else:
                new_notification = Assignments_whatsapp(
                    assignment_id=assignment_id,
                    user_id=student_id,
                    message_sent=True
                )
                db.session.add(new_notification)
            db.session.commit()

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'Error sending WhatsApp message: {str(e)}'})

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'success', 'message': 'Reminder sent successfully!', 'student_late_message_sent': student_late_message_sent, 'parent_late_message_sent': parent_late_message_sent})

# Bulk send reminders for exams (Admin route)
@admin.route("/online/exam/<int:exam_id>/bulk_send_reminders", methods=["POST"])
def bulk_send_reminders_exams(exam_id):
    if current_user.role != "super_admin":
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    
    exam = Assignments.query.get_or_404(exam_id)
    data = request.get_json()
    student_ids = data.get('student_ids', [])
    
    if not student_ids:
        return jsonify({'status': 'error', 'message': 'No students selected'}), 400
    
    sent_count = 0
    skipped_count = 0
    
    for student_id in student_ids:
        student = Users.query.get(student_id)
        if not student:
            skipped_count += 1
            continue
        
        # Check if already sent
        existing_notification = Assignments_whatsapp.query.filter_by(
            assignment_id=exam_id,
            user_id=student_id
        ).first()
        
        if existing_notification and existing_notification.message_sent:
            skipped_count += 1
            continue
        
        # Check if student has WhatsApp numbers
        if not student.student_whatsapp and not student.parent_whatsapp:
            skipped_count += 1
            continue
        
        try:
            # Send to student
            if student.student_whatsapp:
                send_whatsapp_message(student.phone_number, 
                    f"HI *{student.name}*\n\n"
                    f"*{exam.title}*\n"
                    f"Submission is missing\n"
                    f"Didn't submit\n\n"
                    f"Please take care to submit your future assignments"
                )
            
            # Send to parent
            if student.parent_whatsapp:
                send_whatsapp_message(
                    student.parent_phone_number,
                    f"Dear Parent,\n"
                    f"*{student.name}*\n\n"
                    f"Quiz *{exam.title}* due on *{exam.deadline_date.strftime('%d/%m/%Y') if hasattr(exam, 'deadline_date') and exam.deadline_date else 'N/A'}*: Did not submit\n\n"
                    f"_For further inquiries send to Dr. Adham_"
                )
            
            # Record the sent message
            if existing_notification:
                existing_notification.message_sent = True
                existing_notification.sent_date = datetime.now(GMT_PLUS_2)
            else:
                new_notification = Assignments_whatsapp(
                    assignment_id=exam_id,
                    user_id=student_id,
                    message_sent=True
                )
                db.session.add(new_notification)
            
            sent_count += 1
            
        except Exception as e:
            print(f"Error sending to student {student_id}: {str(e)}")
            skipped_count += 1
    
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'Database error: {str(e)}'}), 500
    
    return jsonify({
        'status': 'success',
        'sent_count': sent_count,
        'skipped_count': skipped_count
    })

# Send reminders by range for exams (Admin route)
@admin.route("/online/exam/<int:exam_id>/send_reminders_by_range", methods=["POST"])
def send_reminders_by_range_exams(exam_id):
    if current_user.role != "super_admin":
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    
    exam = Assignments.query.get_or_404(exam_id)
    data = request.get_json()
    from_index = data.get('from_index')
    to_index = data.get('to_index')
    
    if from_index is None or to_index is None:
        return jsonify({'status': 'error', 'message': 'Invalid range'}), 400
    
    # Get all students who didn't submit
    submitted_student_ids = [sub.student_id for sub in exam.submissions]
    
    # Build query for students who qualify for this exam but haven't submitted
    mm_group_ids = [g.id for g in getattr(exam, "groups_mm", [])]
    
    base_filters = [
        ~Users.id.in_(submitted_student_ids),
        Users.role == "student",
        Users.code != 'nth',
        Users.code != 'Nth',
    ]
    
    # Apply group filter
    if mm_group_ids:
        base_filters.append(Users.groups.any(Groups.id.in_(mm_group_ids)))
    elif exam.groupid:
        base_filters.append(Users.groupid == exam.groupid)
    
    not_submitted_students = Users.query.filter(and_(*base_filters)).order_by(Users.id).all()
    
    # Convert to 0-indexed
    from_idx = from_index - 1
    to_idx = to_index
    
    # Get the students in the range
    students_in_range = not_submitted_students[from_idx:to_idx]
    
    sent_count = 0
    skipped_count = 0
    
    for student in students_in_range:
        # Check if already sent
        existing_notification = Assignments_whatsapp.query.filter_by(
            assignment_id=exam_id,
            user_id=student.id
        ).first()
        
        if existing_notification and existing_notification.message_sent:
            skipped_count += 1
            continue
        
        # Check if student has WhatsApp numbers
        if not student.student_whatsapp and not student.parent_whatsapp:
            skipped_count += 1
            continue
        
        try:
            # Send to student
            if student.student_whatsapp:
                send_whatsapp_message(student.phone_number, 
                    f"HI *{student.name}*\n\n"
                    f"*{exam.title}*\n"
                    f"Submission is missing\n"
                    f"Didn't submit\n\n"
                    f"Please take care to submit your future assignments"
                )
            
            # Send to parent
            if student.parent_whatsapp:
                send_whatsapp_message(
                    student.parent_phone_number,
                    f"Dear Parent,\n"
                    f"*{student.name}*\n\n"
                    f"Quiz *{exam.title}* due on *{exam.deadline_date.strftime('%d/%m/%Y') if hasattr(exam, 'deadline_date') and exam.deadline_date else 'N/A'}*: Did not submit\n\n"
                    f"_For further inquiries send to Dr. Adham_"
                )
            
            # Record the sent message
            if existing_notification:
                existing_notification.message_sent = True
                existing_notification.sent_date = datetime.now(GMT_PLUS_2)
            else:
                new_notification = Assignments_whatsapp(
                    assignment_id=exam_id,
                    user_id=student.id,
                    message_sent=True
                )
                db.session.add(new_notification)
            
            sent_count += 1
            
        except Exception as e:
            print(f"Error sending to student {student.id}: {str(e)}")
            skipped_count += 1
    
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'Database error: {str(e)}'}), 500
    
    return jsonify({
        'status': 'success',
        'sent_count': sent_count,
        'skipped_count': skipped_count
    })

#Delete a submission for student (Admin route for EXAMS)
@admin.route("/online/exam/delete_submission/<int:submission_id>", methods=["POST"])
def delete_exam_submission(submission_id):

    if current_user.role != "super_admin":
        flash("You are not allowed to delete student's submissions.", "danger")
        submission = Submissions.query.get_or_404(submission_id)
        return redirect(url_for("admin.view_exam_submissions", exam_id=submission.assignment_id))


    submission = Submissions.query.get_or_404(submission_id)
    exam = Assignments.query.get(submission.assignment_id)
    if not exam:
        flash("Exam not found.", "danger")
        return redirect(url_for("admin.view_exam_submissions", exam_id=submission.assignment_id))


    if exam.type != "Exam":
        flash("Assignment is not an exam.", "danger")
        return redirect(url_for("admin.view_exam_submissions", exam_id=submission.assignment_id))

    try:

        # Delete all upload status records for this submission
        Upload_status.query.filter_by(
            assignment_id=submission.assignment_id,
            user_id=submission.student_id
        ).delete()


        deadline_date = exam.deadline_date
        upload_time = submission.upload_time

        if hasattr(deadline_date, 'tzinfo') and deadline_date.tzinfo is not None:
            if upload_time.tzinfo is None:
                upload_time = GMT_PLUS_2.localize(upload_time)
        else:
            if upload_time.tzinfo is not None:
                upload_time = upload_time.replace(tzinfo=None)

        if deadline_date > upload_time:
            if exam.points:
                submission.student.points = (submission.student.points or 0) - exam.points
        else:
            if exam.points:
                submission.student.points = (submission.student.points or 0) - (exam.points / 2)

        local_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url)
        try:
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
            pass

        db.session.delete(submission)
        db.session.commit()

        flash("Exam submission deleted successfully!", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"An error occurred while deleting the exam submission: {str(e)}", "danger")

    return redirect(url_for("admin.view_exam_submissions", exam_id=exam.id))






@admin.route("/online/exam/toggle/<int:exam_id>", methods=["POST"])
def toggle_exam(exam_id):
    exam = get_item_if_admin_can_manage(Assignments, exam_id, current_user)
    if not exam:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Exam not found or you do not have permission to toggle its visibility."}), 404
        flash("Exam not found or you do not have permission to toggle its visibility.", "danger")
        return redirect(url_for("admin.online_exam"))

    if exam.type != "Exam":
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Assignment is not an exam."}), 400
        flash("Assignment is not an exam.", "danger")
        return redirect(url_for("admin.online_exam"))

    old_status = exam.status
    exam.status = "Hide" if exam.status == "Show" else "Show"
    db.session.commit()
    # Add log for toggling exam visibility
    try:
        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Edit',
            log={
                "action_name": "Edit",
                "resource_type": "exam_visibility",
                "action_details": {
                    "id": exam.id,
                    "title": exam.title,
                    "summary": f"Exam '{exam.title}' visibility was changed."
                },
                "data": None,
                "before": {
                    "visibility_status": old_status
                },
                "after": {
                    "visibility_status": exam.status
                }
            }
        )
        db.session.add(new_log)
        db.session.commit()
    except Exception as e:
        flash(f"Error logging exam visibility change: {str(e)}", "danger")
    
    # Return JSON for AJAX requests
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"success": True, "message": f"Exam status is: {exam.status} now!", "status": exam.status})
    
    flash(f"Exam status is: {exam.status} now!", "success")
    return redirect(url_for("admin.online_exam"))

@admin.route("/online/exam/edit/<int:exam_id>", methods=["GET", "POST"])
def edit_exam(exam_id):

    if current_user.role != "super_admin":
        flash("You are not allowed to edit exams.", "danger")
        return redirect(url_for("admin.online_exam"))
    
    # Get optional group_id from query params for back navigation
    group_id = request.args.get('group_id', type=int)
    group = None
    if group_id:
        group = Groups.query.get(group_id)


    exam = get_item_if_admin_can_manage(Assignments, exam_id, current_user)
    if not exam:
        flash("Exam not found or you do not have permission to edit it.", "danger")
        return redirect(url_for("admin.online_exam"))

    if not exam.type == "Exam":
        flash("Assignment is not an exam.", "danger")
        return redirect(url_for("admin.online_exam"))


    existing_attachments = json.loads(exam.attachments) if exam.attachments else []

    groups = Groups.query.all()


    if request.method == "POST":
        # Save a copy of the old exam state for logging
        old_exam = {
            "title": exam.title,
            "description": exam.description,
            "deadline_date": str(exam.deadline_date) if exam.deadline_date else None,
            "groupid": exam.groupid,
            "groups_mm": [g.id for g in getattr(exam, "groups_mm", [])],
            "attachments": json.loads(exam.attachments) if exam.attachments else [],
            "student_whatsapp": exam.student_whatsapp,
            "parent_whatsapp": exam.parent_whatsapp,
            "out_of": exam.out_of,
        }

        # Update basic fields
        exam.title = request.form.get("title", "").strip()
        exam.description = request.form.get("description", "").strip()
        exam.last_edited_by = current_user.id
        cairo_tz = pytz.timezone('Africa/Cairo')
        aware_local_time = datetime.now(cairo_tz)
        naive_local_time = aware_local_time.replace(tzinfo=None)
        exam.last_edited_at = naive_local_time
        # deadline
        try:
            exam.deadline_date = parse_deadline(request.form.get("deadline_date", ""))
        except (TypeError, ValueError):
            flash("Invalid deadline date. Please use the datetime picker.", "error")
            return redirect(url_for("admin.online_exam"))


        student_whatsapp = request.form.get("student_whatsapp", False)
        if student_whatsapp == "true":
            student_whatsapp = True
        else:
            student_whatsapp = False
        parent_whatsapp = request.form.get("parent_whatsapp", False)
        if parent_whatsapp == "true":
            parent_whatsapp = True
        else:
            parent_whatsapp = False

        #close after deadline
        close_after_deadline = request.form.get("close_after_deadline", False)
        if close_after_deadline == "true":
            close_after_deadline = True
        else:
            close_after_deadline = False



        # out of (full mark)
        out_of = request.form.get("out_of", 0)
        out_of = int(out_of) if str(out_of).isdigit() else 0
        exam.student_whatsapp = student_whatsapp
        exam.parent_whatsapp = parent_whatsapp
        exam.out_of = out_of
        exam.close_after_deadline = close_after_deadline


        # NEW: multi-selects (for many-to-many relationships)
        group_ids_mm  = [int(g) for g in request.form.getlist("groups[]") if g]

        if not group_ids_mm:
            groups = Groups.query.all()
            group_ids_mm = [group.id for group in groups]

        # Update many-to-many relationships
        if hasattr(exam, "groups_mm"):
            exam.groups_mm = Groups.query.filter(Groups.id.in_(group_ids_mm)).all() if group_ids_mm else []

        # Handle new attachments
        upload_dir = "website/assignments/uploads/"
        os.makedirs(upload_dir, exist_ok=True)

        # Get all new attachment indices
        new_attachment_indices = []
        for key in request.form.keys():
            if key.startswith('new_attachments[') and '][name]' in key:
                index = key.split('[')[1].split(']')[0]
                if index not in new_attachment_indices:
                    new_attachment_indices.append(index)
        
        # Process each new attachment
        for idx in new_attachment_indices:
            attachment_name = request.form.get(f'new_attachments[{idx}][name]')
            attachment_type = request.form.get(f'new_attachments[{idx}][type]')
            
            if not attachment_name:
                continue
                
            attachment_obj = {
                'name': attachment_name,
                'type': attachment_type
            }
            
            if attachment_type == 'file':
                file = request.files.get(f'new_attachments[{idx}][file]')
                if file and file.filename:
                    original_filename = secure_filename(file.filename)
                    filename = f"{uuid.uuid4().hex}_{original_filename}"
                    file_path = os.path.join(upload_dir, filename)
                    file.save(file_path)
                    try:
                        with open(file_path, "rb") as f:
                            storage.upload_file(f, folder="assignments/uploads", file_name=filename)
                    except Exception as e:
                        flash(f"Error uploading file to storage: {str(e)}", "danger")
                        return redirect(url_for("admin.online_exam"))
                    attachment_obj['url'] = f"/student/assignments/uploads/{filename}"
                    existing_attachments.append(attachment_obj)
            elif attachment_type == 'link':
                attachment_url = request.form.get(f'new_attachments[{idx}][url]')
                if attachment_url:
                    attachment_obj['url'] = attachment_url
                    existing_attachments.append(attachment_obj)

        exam.attachments = json.dumps(existing_attachments)
        db.session.commit()

        # Log the edit action
        new_log = AssistantLogs(
            assistant_id=current_user.id,
            action='Edit',
            log={
                "action_name": "Edit",
                "resource_type": "exam",
                "action_details": {
                    "id": exam.id,
                    "title": exam.title,
                    "summary": f"Exam '{exam.title}' was edited."
                },
                "data": None,
                "before": old_exam,
                "after": {
                    "title": exam.title,
                    "description": exam.description,
                    "deadline_date": str(exam.deadline_date) if exam.deadline_date else None,
                    "groupid": exam.groupid,
                    "groups_mm": [g.id for g in getattr(exam, "groups_mm", [])],
                    "attachments": json.loads(exam.attachments) if exam.attachments else [],
                    "student_whatsapp": exam.student_whatsapp,
                    "parent_whatsapp": exam.parent_whatsapp,
                    "out_of": exam.out_of,
                    "close_after_deadline": exam.close_after_deadline,
                }
            }
        )
        db.session.add(new_log)
        db.session.commit()

        # Check if it's an AJAX request
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # Return updated exam data
            groups_names = [g.name for g in getattr(exam, 'groups_mm', [])] if getattr(exam, 'groups_mm', None) else []
            
            qualified_count = qualified_students_count_for_assignment(exam)
            submitted_count = (
                Submissions.query
                .with_entities(Submissions.student_id)
                .filter_by(assignment_id=exam.id)
                .distinct()
                .count()
            )

            updated_exam = {
                "id": exam.id,
                "title": exam.title,
                "description": exam.description,
                "creation_date": exam.creation_date.strftime('%Y-%m-%d %I:%M %p') if exam.creation_date else None,
                "deadline_date": exam.deadline_date.strftime('%Y-%m-%d %I:%M %p') if exam.deadline_date else None,
                "groups": groups_names,
                "status": exam.status,
                "points": exam.points,
                "submitted_students_count": submitted_count,
                "qualified_students_count": qualified_count,
                "student_whatsapp": exam.student_whatsapp,
                "parent_whatsapp": exam.parent_whatsapp,
                "out_of": exam.out_of,
                "close_after_deadline": exam.close_after_deadline,
            }
            
            return jsonify({"success": True, "message": "Exam updated successfully!", "exam": updated_exam})

        flash("Exam updated successfully!", "success")
        return redirect(url_for("admin.online_exam"))
        
    if request.args.get("delete_attachment"):
        filename_to_delete = request.args.get("delete_attachment")
        if filename_to_delete in existing_attachments:
            file_path = os.path.join("website/assignments/uploads", filename_to_delete)
            if os.path.exists(file_path):
                os.remove(file_path)
            try:
                storage.delete_file(folder="assignments/uploads", file_name=filename_to_delete)
            except Exception:
                pass
            
            # Log the delete attachment action
            before_attachments = list(existing_attachments)
            existing_attachments.remove(filename_to_delete)
            exam.attachments = json.dumps(existing_attachments)
            db.session.commit()

            new_log = AssistantLogs(
                assistant_id=current_user.id,
                action='Edit',
                log={
                    "action_name": "Edit",
                    "resource_type": "exam_attachment",
                    "action_details": {
                        "id": exam.id,
                        "title": exam.title,
                        "summary": f"Attachment '{filename_to_delete}' was deleted from exam '{exam.title}'."
                    },
                    "data": None,
                    "before": {
                        "attachments": before_attachments
                    },
                    "after": {
                        "attachments": existing_attachments
                    }
                }
            )
            db.session.add(new_log)
            db.session.commit()

            flash("Attachment deleted successfully!", "success")
        else:
            flash("Attachment not found!", "error")
        return redirect(url_for("admin.edit_exam", exam_id=exam_id))

    cairo_tz = pytz.timezone('Africa/Cairo')
    now_cairo = datetime.now(cairo_tz)
    exam_late_exceptions = []
    late_exception_rows = (
        AssignmentLateException.query
        .filter_by(assignment_id=exam.id)
        .join(Users, AssignmentLateException.student_id == Users.id)
        .order_by(Users.name.asc())
        .all()
    )
    for exception in late_exception_rows:
        student = exception.student
        aware_deadline = None
        if exception.extended_deadline:
            try:
                aware_deadline = cairo_tz.localize(exception.extended_deadline)
            except ValueError:
                aware_deadline = exception.extended_deadline.astimezone(cairo_tz)
        is_active = aware_deadline is None or aware_deadline >= now_cairo
        exam_late_exceptions.append({
            "exception": exception,
            "student": student,
            "aware_deadline": aware_deadline,
            "is_active": is_active,
        })

    return render_template(
        "admin/online_exam/edit_exam.html",
        exam=exam,
        groups=groups,
        attachments=existing_attachments,
        group_id=group_id,
        group=group,
        late_exceptions=exam_late_exceptions
    )

@admin.route('/online/exam/delete-attachment/<int:exam_id>/<int:attachment_index>', methods=['POST'])
def delete_exam_attachment(exam_id, attachment_index):
    """Delete a specific attachment from an exam"""
    exam = get_item_if_admin_can_manage(Assignments, exam_id, current_user)
    if not exam:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Exam not found or you do not have permission to delete attachments from it."}), 404
        flash("Exam not found or you do not have permission to delete attachments from it.", "danger")
        return redirect(url_for("admin.online_exam"))

    if not exam.type == "Exam":
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Assignment is not an exam."}), 400
        flash("Assignment is not an exam.", "danger")
        return redirect(url_for("admin.online_exam"))

    try:
        attachments = json.loads(exam.attachments) if exam.attachments else []
        
        if attachment_index >= len(attachments):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": False, "message": "Attachment index out of range."}), 400
            flash("Attachment index out of range.", "danger")
            return redirect(url_for("admin.online_exam"))

        attachment = attachments[attachment_index]
        # If it's a file attachment, try to delete the file
        if isinstance(attachment, dict) and attachment.get('type') == 'file':
            url = attachment.get('url', '')
            if '/student/assignments/uploads/' in url:
                filename = url.split('/student/assignments/uploads/')[-1]
                file_path = os.path.join("website/assignments/uploads", filename)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        try:
                            storage.delete_file(folder="assignments/uploads", file_name=filename)
                        except Exception:
                            pass
                    except Exception:
                        pass  # Continue even if file deletion fails
        
        # Remove the attachment from the list
        attachments.pop(attachment_index)
        exam.attachments = json.dumps(attachments)
        exam.last_edited_by = current_user.id
        cairo_tz = pytz.timezone('Africa/Cairo')
        aware_local_time = datetime.now(cairo_tz)
        naive_local_time = aware_local_time.replace(tzinfo=None)
        exam.last_edited_at = naive_local_time
        db.session.commit()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True, "message": "Attachment deleted successfully"})
        flash("Attachment deleted successfully", "success")
        return redirect(url_for("admin.online_exam"))

    except Exception as e:
        db.session.rollback()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": f"Failed to delete attachment: {str(e)}"}), 500
        flash(f"Failed to delete attachment: {str(e)}", "danger")
        return redirect(url_for("admin.online_exam"))

@admin.route('/group/<int:group_id>/online/exam/delete-attachment/<int:exam_id>/<int:attachment_index>', methods=['POST'])
def delete_group_exam_attachment(group_id, exam_id, attachment_index):
    """Delete a specific attachment from an exam in a group context"""
    group = Groups.query.get(group_id)
    if not group:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Group not found."}), 404
        flash("Group not found.", "danger")
        return redirect(url_for("admin.online_exam"))

    exam = get_item_if_admin_can_manage(Assignments, exam_id, current_user)
    if not exam:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Exam not found or you do not have permission to delete attachments from it."}), 404
        flash("Exam not found or you do not have permission to delete attachments from it.", "danger")
        return redirect(url_for("admin.group_exams", group_id=group_id))

    if not exam.type == "Exam":
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Assignment is not an exam."}), 400
        flash("Assignment is not an exam.", "danger")
        return redirect(url_for("admin.group_exams", group_id=group_id))

    try:
        attachments = json.loads(exam.attachments) if exam.attachments else []
        
        if attachment_index >= len(attachments):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": False, "message": "Attachment index out of range."}), 400
            flash("Attachment index out of range.", "danger")
            return redirect(url_for("admin.group_exams", group_id=group_id))

        attachment = attachments[attachment_index]
        
        # If it's a file attachment, try to delete the file and also remove it from storage if possible
        if isinstance(attachment, dict) and attachment.get('type') == 'file':
            url = attachment.get('url', '')
            if '/student/assignments/uploads/' in url:
                filename = url.split('/student/assignments/uploads/')[-1]
                file_path = os.path.join("website/assignments/uploads", filename)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        try:
                            storage.delete_file(folder="assignments/uploads", file_name=filename)
                        except Exception:
                            pass
                    except Exception:
                        pass  # Continue even if file deletion fails
        # Remove the attachment from the list
        attachments.pop(attachment_index)
        exam.attachments = json.dumps(attachments)
        exam.last_edited_by = current_user.id
        cairo_tz = pytz.timezone('Africa/Cairo')
        aware_local_time = datetime.now(cairo_tz)
        naive_local_time = aware_local_time.replace(tzinfo=None)
        exam.last_edited_at = naive_local_time
        db.session.commit()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True, "message": "Attachment deleted successfully"})
        flash("Attachment deleted successfully", "success")
        return redirect(url_for("admin.group_exams", group_id=group_id))

    except Exception as e:
        db.session.rollback()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": f"Failed to delete attachment: {str(e)}"}), 500
        flash(f"Failed to delete attachment: {str(e)}", "danger")
        return redirect(url_for("admin.group_exams", group_id=group_id))

@admin.route("/online/exam/delete/<int:exam_id>", methods=["POST"])
def delete_exam(exam_id):
    exam = get_item_if_admin_can_manage(Assignments, exam_id, current_user)
    if not exam:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Exam not found or you do not have permission to delete it."}), 404
        flash("Exam not found or you do not have permission to delete it.", "danger")
        return redirect(url_for("admin.online_exam"))

    if not exam.type == "Exam":
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "message": "Assignment is not an exam."}), 400
        flash("Assignment is not an exam.", "danger")
        return redirect(url_for("admin.online_exam"))

    submissions = Submissions.query.filter_by(assignment_id=exam_id).all()

    deleted_submissions = []
    deleted_attachments = []
    # Also delete related Assignments_whatsapp records for this exam
    whatsapp_notifications = Assignments_whatsapp.query.filter_by(assignment_id=exam_id).all()
    for notif in whatsapp_notifications:
        try:
            db.session.delete(notif)
        except Exception:
            pass

    
    try :
        Upload_status.query.filter_by(assignment_id=exam_id).delete()
        db.session.commit()
    except Exception:
        pass
        

    for submission in submissions:
        try:
            if exam.deadline_date > submission.upload_time:
                if exam.points:
                    student = Users.query.get(submission.student_id)
                    student.points = student.points - exam.points
                    db.session.commit()
            else:
                if exam.points:
                    student = Users.query.get(submission.student_id)
                    student.points = student.points - (exam.points / 2)
                    db.session.commit()

            local_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url)
            if os.path.exists(local_path):
                os.remove(local_path)

            annotated_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url.replace(".pdf", "_annotated.pdf"))
            if os.path.exists(annotated_path):
                os.remove(annotated_path)

            try:
                storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url.replace(".pdf", "_annotated.pdf"))
            except Exception:
                pass
            try:
                storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url)
            except Exception as e:
                flash(f"Error deleting from s3: {e}", 'error')
                pass
            db.session.delete(submission)
            deleted_submissions.append({
                "submission_id": submission.id,
                "student_id": submission.student_id,
                "file_url": submission.file_url
            })
        except Exception:
            pass

    if exam.attachments:
        try:
            attachment_list = json.loads(exam.attachments)
            for attachment in attachment_list:
                # Handle both old format (strings) and new format (dicts with type/url/name)
                if isinstance(attachment, dict):
                    if attachment.get('type') == 'file':
                        file_path = attachment.get('url', '')
                        # Extract filename from URL if it's a full path
                        if '/' in file_path:
                            file_path = file_path.split('/')[-1]
                        
                        local_path = os.path.join("website/assignments/uploads", file_path)
                        if os.path.exists(local_path):
                            os.remove(local_path)
                        try:
                            storage.delete_file(folder="assignments/uploads", file_name=file_path)
                        except Exception:
                            pass
                        deleted_attachments.append(file_path)
                    # Links don't need file deletion, just log them
                    elif attachment.get('type') == 'link':
                        deleted_attachments.append(attachment.get('name', attachment.get('url', '')))
                else:
                    # Old format: plain string filename
                    file_path = attachment
                    local_path = os.path.join("website/assignments/uploads", file_path)
                    if os.path.exists(local_path):
                        os.remove(local_path)
                    try:
                        storage.delete_file(folder="assignments/uploads", file_name=file_path)
                    except Exception:
                        pass
                    deleted_attachments.append(file_path)
        except Exception as e:
            flash(f"Error while deleting attachments: {str(e)}", "danger")
    
    # Log the delete action before deleting the exam
    new_log = AssistantLogs(
        assistant_id=current_user.id,
        action='Delete',
        log={
            "action_name": "Delete",
            "resource_type": "exam",
            "action_details": {
                "id": exam.id,
                "title": exam.title,
                "summary": f"Exam '{exam.title}' was deleted."
            },
            "data": None,
            "before": {
                "title": exam.title,
                "description": exam.description,
                "deadline_date": str(exam.deadline_date) if exam.deadline_date else None,
                "attachments": json.loads(exam.attachments) if exam.attachments else [],
                "submissions_deleted_count": len(deleted_submissions),
                "subjectid": getattr(exam, "subjectid", None),
                "subject": getattr(exam.subject, "name", None) if hasattr(exam, "subject") else None,
                "points": exam.points,
            },
            "after": None
        }
    )
    db.session.add(new_log)
    db.session.delete(exam)
    db.session.commit()
    
    # Return JSON for AJAX requests
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"success": True, "message": "Exam, its attachments, and points deleted successfully!"})
    
    flash("Exam, its attachments, and points deleted successfully!", "success")
    return redirect(url_for("admin.online_exam"))




@admin.route("/correction/exam/<int:submission_id>")
def edit_pdf_exam(submission_id):
    submission = Submissions.query.get_or_404(submission_id)
    pdfurl = f"/admin/getpdf/{submission_id}"
    filename = submission.file_url


    assignment = Assignments.query.get(submission.assignment_id)
    student_name = submission.student.name
    #take only first 2 names then truntcate 
    student_name = student_name.split(" ")[0] + " " + student_name.split(" ")[1]
    student_name = student_name[:20] + "..."
    if assignment.out_of > 0:
        show_grade = True
    else:
        show_grade = False


    return render_template("admin/editpdf.html", pdfurl=pdfurl, filename=filename , submission_id=submission_id, show_grade=show_grade, student_name=student_name)


@admin.route("/online/exam/annotate2/<int:submission_id>")
def edit_pdf2_exam(submission_id):
    submission = Submissions.query.get_or_404(submission_id)
    pdfurl = f"/admin/getpdf2/{submission_id}"
    filename = submission.file_url

    assignment = Assignments.query.get(submission.assignment_id)
    student_name = submission.student.name
    #take only first 2 names then truntcate 
    student_name = student_name.split(" ")[0] + " " + student_name.split(" ")[1]
    student_name = student_name[:20] + "..."
    if assignment.out_of > 0:
        show_grade = True
    else:
        show_grade = False
    return render_template("admin/editpdf.html", pdfurl=pdfurl, filename=filename , submission_id=submission_id, show_grade=show_grade, student_name=student_name)


@admin.route("/online/exam/delete_submission/<int:submission_id>", methods=["POST"])
def delete_submission_exam(submission_id):

    if current_user.role != "super_admin":
        flash("You are not allowed to delete student's submissions.", "danger")
        return redirect(url_for("admin.view_exam_submissions", assignment_id=submission.assignment_id))

    submission = Submissions.query.get_or_404(submission_id)
    assignment = Assignments.query.get(submission.assignment_id)
    if not assignment:
        flash("Assignment not found.", "danger")
        return redirect(url_for("admin.view_exam_submissions", assignment_id=submission.assignment_id))

    if assignment.type != "Exam":
        flash("This route is only for exam submissions.", "danger")
        return redirect(url_for("admin.view_exam_submissions", assignment_id=submission.assignment_id))

    try:
        # Delete all upload status records for this submission
        Upload_status.query.filter_by(
            assignment_id=submission.assignment_id,
            user_id=submission.student_id
        ).delete()

        deadline_date = assignment.deadline_date
        upload_time = submission.upload_time

        if hasattr(deadline_date, 'tzinfo') and deadline_date.tzinfo is not None:
            if upload_time.tzinfo is None:
                upload_time = GMT_PLUS_2.localize(upload_time)
        else:
            if upload_time.tzinfo is not None:
                upload_time = upload_time.replace(tzinfo=None)

        if deadline_date > upload_time:
            if assignment.points:
                submission.student.points = (submission.student.points or 0) - assignment.points
        else:
            if assignment.points:
                submission.student.points = (submission.student.points or 0) - (assignment.points / 2)

        # Delete file from local storage
        local_path = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url)
        if os.path.exists(local_path):
            os.remove(local_path)

        try :
            local_path2 = os.path.join("website", "submissions", "uploads", f"student_{submission.student_id}", submission.file_url.replace(".pdf", "_annotated.pdf"))
            if os.path.exists(local_path2):
                os.remove(local_path2)
            storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url.replace(".pdf", "_annotated.pdf"))
        except Exception:
            pass
        # Delete file from remote storage
        try:
            storage.delete_file(f"submissions/uploads/student_{submission.student_id}", submission.file_url)
        except Exception:
            flash("Error deleting file from storage.", "warning")
        db.session.delete(submission)
        db.session.commit()
        flash("Submission deleted successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"An error occurred while deleting the submission: {str(e)}", "danger")

    return redirect(url_for("admin.view_exam_submissions", assignment_id=assignment.id))


#=================================================================
#Account
#=================================================================

@admin.route('/account', methods=['GET', 'POST'])
def account():
    if request.method == "POST":
        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if not check_password_hash(current_user.password, current_password):
            flash("The current password is incorrect.", "danger")
            return redirect(url_for("admin.account"))
        if new_password != confirm_password:
            flash("New password and confirm password do not match.", "danger")
            return redirect(url_for("admin.account"))
        if new_password.lower() == "password":
            flash("New password cannot be 'password'. Please choose a stronger password.", "danger")
            return redirect(url_for("admin.account"))

        current_user.password = generate_password_hash(new_password, method="pbkdf2:sha256", salt_length=8)
        db.session.commit()
        flash("Your password has been successfully updated!", "success")
        return redirect(url_for("admin.account"))

    student_count = None
    if current_user.role == "admin":
        group_ids = get_user_scope_ids()
        query = Users.query.filter_by(role='student')
        if group_ids :
            query = query.filter(Users.groupid.in_(group_ids))
            student_count = query.distinct().count()
    elif current_user.role == "super_admin":
        students = Users.query.filter_by(role='student').all()
        student_count = len(students)

    return render_template('admin/account.html', student_count=student_count)



#=================================================================
#Leaderboard
#=================================================================


@admin.route('/leaderboard', methods=['GET'])
def leaderboard():
    return "Paused for now"

    # Pagination parameters
    try:
        page = int(request.args.get('page', 1))
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    per_page = 30  # You can adjust this as needed

    # Get filter parameters
    school_id = request.args.get('school', type=int)
    stage_id = request.args.get('grade', type=int)
    group_id = request.args.get('class', type=int)
    subject_id = request.args.get('subject', type=int)

    # Get all schools, stages, groups, and subjects for filtering/sorting
    schools = Schools.query.all()
    stages = Stages.query.all()
    groups = Groups.query.all()
    subjects = Subjects.query.all()

    # Get all students, sorted by points descending, with optional filters
    users_query = Users.query.filter(
        (Users.role == 'student') & (Users.code != 'nth') & (Users.code != 'Nth')
    )

    # Only show students the user manages
    if current_user.role == "admin":
        group_ids, stage_ids, school_ids, subject_ids = get_user_scope_ids()
        users_query = users_query.filter(
            Users.groupid.in_(group_ids),
            Users.stageid.in_(stage_ids),
            Users.schoolid.in_(school_ids),
            Users.subjectid.in_(subject_ids)
        )

    # Apply filters if provided
    if school_id:
        users_query = users_query.filter(Users.schoolid == school_id)
    if stage_id:
        users_query = users_query.filter(Users.stageid == stage_id)
    if group_id:
        users_query = users_query.filter(Users.groupid == group_id)
    if subject_id:
        users_query = users_query.filter(Users.subjectid == subject_id)

    users_query = users_query.order_by(Users.points.desc())
    total_users = users_query.count()
    users = users_query.offset((page - 1) * per_page).limit(per_page).all()

    # Calculate total pages
    total_pages = (total_users + per_page - 1) // per_page

    # Pass all users and filter data to the template, along with pagination info
    return render_template(
        'admin/leaderboard.html',
        users=users,
        schools=schools,
        stages=stages,
        groups=groups,
        subjects=subjects,
        page=page,
        per_page=per_page,
        total_users=total_users,
        total_pages=total_pages,
        selected_school=school_id,
        selected_stage=stage_id,
        selected_group=group_id,
        selected_subject=subject_id
    )



#=================================================================
#Whatsapp
#=================================================================


@admin.route('/whatsapp', methods=['GET', 'POST'])
def whatsapp():
    student_with_whatsapp_count = Users.query.filter(
        Users.role == 'student',
        Users.student_whatsapp.isnot(None),
        Users.student_whatsapp != ''
    ).count()

    parent_with_whatsapp_count = Users.query.filter(
        Users.role == 'student',
        Users.parent_whatsapp.isnot(None),
        Users.parent_whatsapp != ''
    ).count()

    if request.method == 'POST':
        subject_id = request.form.get('subject_id', type=int)
        school_id = request.form.get('school_id', type=int)
        stage_id = request.form.get('stage_id', type=int)
        group_id = request.form.get('group_id', type=int)
        message = request.form.get('message', '')

        # Query all students matching the filters
        query = Users.query.filter(
            db.and_(
                Users.role == 'student',
                Users.role != 'Nth',
                Users.role != 'nth'
            )
        )
        if subject_id:
            query = query.filter(Users.subjectid == subject_id)
        if school_id:
            query = query.filter(Users.schoolid == school_id)
        if stage_id:
            query = query.filter(Users.stageid == stage_id)
        if group_id:
            query = query.filter(Users.groupid == group_id)

        students = query.all()
        sent_count = 0
        for student in students:
            # Send to student if they have WhatsApp
            if student.student_whatsapp and student.student_whatsapp.strip():
                try:
                    send_whatsapp_message(student.student_whatsapp, message)
                    sent_count += 1
                except Exception as e:
                    pass  # Optionally log error
            # Send to parent if they have WhatsApp
            if student.parent_whatsapp and student.parent_whatsapp.strip():
                try:
                    send_whatsapp_message(student.parent_whatsapp, message)
                    sent_count += 1
                except Exception as e:
                    pass  # Optionally log error

        return jsonify({"success": True, "message": f"Message sent to {sent_count} WhatsApp recipients (students and parents)."})

    return render_template(
        'admin/whatsapp.html',
        student_with_whatsapp_count=student_with_whatsapp_count,
        parent_with_whatsapp_count=parent_with_whatsapp_count
    )

@admin.route('/api/filters/recipients_count')
def api_recipients_count():
    """
    Returns the count of students matching the selected subject, school, stage, and group,
    and who have a WhatsApp number (either student or parent).
    """
    subject_id = request.args.get('subject_id', type=int)
    school_id = request.args.get('school_id', type=int)
    stage_id = request.args.get('stage_id', type=int)
    group_id = request.args.get('group_id', type=int)

    query = Users.query.filter(
        db.and_(
            Users.role != 'Nth',
            Users.role != 'nth',
            Users.role == 'student'
        )
    )

    if subject_id:
        query = query.filter(Users.subjectid == subject_id)
    if school_id:
        query = query.filter(Users.schoolid == school_id)
    if stage_id:
        query = query.filter(Users.stageid == stage_id)
    if group_id:
        query = query.filter(Users.groupid == group_id)


    should_receive = query.filter(
        db.or_(
            db.and_(Users.phone_number.isnot(None), Users.phone_number != ''),
            db.and_(Users.parent_phone_number.isnot(None), Users.parent_phone_number != '')
        )
    )


    # Only count students with at least one WhatsApp number
    query = query.filter(
        db.or_(
            db.and_(Users.student_whatsapp.isnot(None), Users.student_whatsapp != ''),
            db.and_(Users.parent_whatsapp.isnot(None), Users.parent_whatsapp != '')
        )
    )



    count = query.distinct().count()
    should_receive_count = should_receive.distinct().count()
    return jsonify({"count": count, "should_receive_count": should_receive_count})


#=================================================================
#Logs (Super Admin Only)
#=================================================================
@admin.route("/logs", methods=["GET"])
def logs():
    if current_user.role != 'super_admin' :
        flash("You are not authorized to view logs.", "danger")
        return redirect(url_for("admin.dashboard"))
    logs = AssistantLogs.query.order_by(AssistantLogs.timestamp.desc()).all()

    for log in logs:
        log.timestamp = log.timestamp.astimezone(GMT_PLUS_2)
        log_diff = {}
        log_data = log.log if isinstance(log.log, dict) else {}
        before = log_data.get("before")
        after = log_data.get("after")
        if before and after and isinstance(before, dict) and isinstance(after, dict):
            for key in set(before.keys()).union(after.keys()):
                before_val = before.get(key)
                after_val = after.get(key)
                if before_val != after_val:
                    log_diff[key] = {"before": before_val, "after": after_val}
        log.diff = log_diff if log_diff else None
    return render_template("admin/logs.html", logs=logs)


#=================================================================
#WhatsApp Messages (Super Admin Only)
#=================================================================
@admin.route("/whatsapp-messages", methods=["GET", "POST"])
def whatsapp_messages():
    if current_user.role != 'super_admin':
        flash("You are not authorized to view WhatsApp messages.", "danger")
        return redirect(url_for("admin.dashboard"))
    
    # Handle POST request to send a new message
    if request.method == "POST":
        phone_number = request.form.get('phone_number', '').strip()
        message_content = request.form.get('message', '').strip()
        country_code = request.form.get('country_code', '2').strip()
        
        if not phone_number or not message_content:
            flash("Phone number and message are required.", "danger")
            return redirect(url_for("admin.whatsapp_messages"))
        
        try:
            # Send the WhatsApp message
            success, result_message = send_whatsapp_message(f"{phone_number}", message_content, country_code)
            
            if success:
                flash(result_message, "success")
            else:
                flash(f"Failed to queue message: {result_message}", "danger")
        except Exception as e:
            flash(f"Error sending message: {str(e)}", "danger")
        
        return redirect(url_for("admin.whatsapp_messages"))
    
    # Get filter parameters
    status_filter = request.args.get('status', 'all')
    user_id_filter = request.args.get('user_id', type=int)
    
    # Build query
    query = WhatsappMessages.query
    
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    
    if user_id_filter:
        query = query.filter_by(user_id=user_id_filter)
    
    # Get messages ordered by date (newest first)
    messages = query.order_by(WhatsappMessages.date_added.desc()).all()
    
    # Adjust timestamps to GMT+2/GMT+3
    for message in messages:
        if message.date_added:
            message.date_added = message.date_added.astimezone(GMT_PLUS_2) + timedelta(hours=3)
    
    # Get all users for filter dropdown (optional)
    users = Users.query.filter(Users.id.in_([m.user_id for m in messages if m.user_id])).all()
    
    return render_template(
        "admin/whatsapp_messages.html",
        messages=messages,
        users=users,
        status_filter=status_filter,
        user_id_filter=user_id_filter
    )



#=================================================================
# Critical Students Page (Based on "Exam" Assignments <60%) & Missing Assignments >=2
#=================================================================


@admin.route("/critical/students")
def critical_students():
    # Just load the HTML; the data is provided via AJAX from the API route.
    return render_template(
        "admin/critical_students.html",
        critical_exam_students=[],
        critical_assignment_students=[]
    )






    
@admin.route("/critical/api")
def critical_students_api():
    return "Paused for now"

    """
    API endpoint: Detect critical students (low exam % and missing assignments), JSON response.
    Uses efficient single queries and robust type checking to avoid errors.
    Filters by assignments where the admin manages the subject AND school AND group AND stage.
    """
    
    # Get current user's managed scope
    group_ids, stage_ids, school_ids, subject_ids = get_user_scope_ids()
    
    # =====================================================================
    # 1. Students with <60% in any Exam (Corrected AND Logic)
    # =====================================================================
    critical_exam_students = []

    # If admin doesn't manage at least one of each category, they can't see anything.
    if all([group_ids, stage_ids, school_ids, subject_ids]):
        # Build base query for failing submissions
        failing_submissions_query = Submissions.query.join(
            Assignments, Submissions.assignment_id == Assignments.id
        ).join(
            Users, Submissions.student_id == Users.id
        ).filter(
            and_(
                Users.role == "student",
                Assignments.type == "Exam",
                Assignments.out_of > 0,
                Submissions.mark.isnot(None),
                Submissions.mark.op('~')(r'^[0-9]+(\.[0-9]+)?$'), 
                (cast(Submissions.mark, Float) / Assignments.out_of * 100) < 60
            )
        )
        
        # Build the strict AND filters for the admin's scope
        subject_filter = Assignments.subjectid.in_(subject_ids)
        
        school_filter = or_(
            Assignments.schoolid.in_(school_ids),
            Assignments.schools_mm.any(Schools.id.in_(school_ids))
        )
        
        group_filter = or_(
            Assignments.groupid.in_(group_ids),
            Assignments.groups_mm.any(Groups.id.in_(group_ids))
        )
        
        stage_filter = or_(
            Assignments.stageid.in_(stage_ids),
            Assignments.stages_mm.any(Stages.id.in_(stage_ids))
        )

        # Apply all scope filters with AND
        failing_submissions_query = failing_submissions_query.filter(
            and_(subject_filter, school_filter, group_filter, stage_filter)
        )
        
        failing_submissions = failing_submissions_query.all()

        for sub in failing_submissions:
            student = sub.student
            assignment = sub.assignment
            
            student_mark = float(sub.mark)
            full_mark = float(assignment.out_of)
            percent = (student_mark / full_mark) * 100
            
            critical_exam_students.append({
                "student_id": student.id,
                "student_name": student.name,
                "exam_title": assignment.title,
                "student_mark": student_mark,
                "full_mark": full_mark,
                "percent": round(percent, 2),
                "school_id": student.schoolid,
                "subject_id": student.subjectid,
            })

    # =====================================================================
    # 2. Students who missed 2+ Assignments (Optimized and Corrected)
    # =====================================================================
    critical_assignment_students = []
    
    # Proceed only if the admin has a valid scope
    if all([group_ids, stage_ids, school_ids, subject_ids]):
        # Step 1: Get all assignments matching the admin's strict AND scope ONCE
        subject_filter = Assignments.subjectid.in_(subject_ids)
        school_filter = or_(Assignments.schoolid.in_(school_ids), Assignments.schools_mm.any(Schools.id.in_(school_ids)))
        group_filter = or_(Assignments.groupid.in_(group_ids), Assignments.groups_mm.any(Groups.id.in_(group_ids)))
        stage_filter = or_(Assignments.stageid.in_(stage_ids), Assignments.stages_mm.any(Stages.id.in_(stage_ids)))
        
        scoped_hws = Assignments.query.filter(
            Assignments.type == "Assignment",
            and_(subject_filter, school_filter, group_filter, stage_filter)
        ).all()

        if scoped_hws:
            # Step 2: Get all students within the admin's broad OR scope
            students_query = Users.query.filter(Users.role == "student").filter(
                or_(
                    Users.groupid.in_(group_ids),
                    Users.stageid.in_(stage_ids),
                    Users.schoolid.in_(school_ids),
                    Users.subjectid.in_(subject_ids)
                )
            )
            all_students = students_query.all()

            # Step 3: Get all relevant submissions in a single query
            student_ids = [s.id for s in all_students]
            assignment_ids = [a.id for a in scoped_hws]
            all_submissions = Submissions.query.filter(
                Submissions.student_id.in_(student_ids),
                Submissions.assignment_id.in_(assignment_ids)
            ).all()

            # Map submissions to students for fast lookup
            submissions_by_student = {}
            for sub in all_submissions:
                submissions_by_student.setdefault(sub.student_id, set()).add(sub.assignment_id)
            
            # Step 4: Process in Python (NO more database queries in the loop)
            for student in all_students:
                # For each student, determine which of the scoped assignments they should see
                visible_hws = []
                for hw in scoped_hws:
                    # An assignment is visible if its targets match the student's profile.
                    # An empty target list (e.g., no specific groups) means it's visible to all.
                    g_targets = {g.id for g in hw.groups_mm} | ({hw.groupid} if hw.groupid else set())
                    s_targets = {s.id for s in hw.stages_mm} | ({hw.stageid} if hw.stageid else set())
                    sch_targets = {s.id for s in hw.schools_mm} | ({hw.schoolid} if hw.schoolid else set())
                    
                    group_match = (not g_targets) or (student.groupid in g_targets)
                    stage_match = (not s_targets) or (student.stageid in s_targets)
                    school_match = (not sch_targets) or (student.schoolid in sch_targets)
                    subject_match = (hw.subjectid is None) or (student.subjectid == hw.subjectid)

                    if group_match and stage_match and school_match and subject_match:
                        visible_hws.append(hw)
                
                if not visible_hws:
                    continue

                total_hw = len(visible_hws)
                submitted_ids = submissions_by_student.get(student.id, set())
                done = sum(1 for hw in visible_hws if hw.id in submitted_ids)
                missing = total_hw - done
                
                if missing >= 2:
                    critical_assignment_students.append({
                        "student_id": student.id,
                        "student_name": student.name,
                        "total_assignments": total_hw,
                        "completed_assignments": done,
                        "missing_assignments": missing
                    })

    # =====================================================================
    # Final JSON Response
    # =====================================================================
    return jsonify({
        "critical_exam_students": critical_exam_students,
        "critical_assignment_students": critical_assignment_students
    })


#Upload status 
@admin.route("/upload/status")
def upload_status():
    # Get all upload statuses with related user and assignment data
    uploads = Upload_status.query.join(
        Users, Upload_status.user_id == Users.id
    ).join(
        Assignments, Upload_status.assignment_id == Assignments.id
    ).add_columns(
        Upload_status.id,
        Upload_status.upload_status,
        Upload_status.upload_type,
        Upload_status.file_name,
        Upload_status.total_chunks,
        Upload_status.current_chunk,
        Upload_status.last_chunk_date,
        Upload_status.last_chunk_size,
        Upload_status.total_size,
        Upload_status.bytes_uploaded,
        Upload_status.progress_percent,
        Upload_status.created_at,
        Upload_status.failure_reason,
        Users.id.label('user_id'),
        Users.name.label('user_name'),
        Assignments.id.label('assignment_id'),
        Assignments.title.label('assignment_title')
    ).order_by(Upload_status.last_chunk_date.desc()).all()
    
    # Format the data for the template
    upload_data = []
    for upload in uploads:
        # Use the pre-calculated progress_percent from the model
        progress_percentage = round(upload.progress_percent, 2)
        
        # Format file size for display
        total_size_mb = round(upload.total_size / (1024 * 1024), 2) if upload.total_size else 0
        uploaded_size_mb = round(upload.bytes_uploaded / (1024 * 1024), 2) if upload.bytes_uploaded else 0
        
        upload_data.append({
            'id': upload.id,
            'status': upload.upload_status,
            'type': upload.upload_type,
            'file_name': upload.file_name,
            'user_id': upload.user_id,
            'user_name': upload.user_name,
            'assignment_id': upload.assignment_id,
            'assignment_title': upload.assignment_title,
            'total_chunks': upload.total_chunks,
            'current_chunk': upload.current_chunk,
            'progress_percentage': progress_percentage,
            'total_size_mb': total_size_mb,
            'uploaded_size_mb': uploaded_size_mb,
            'created_at': upload.created_at,
            'last_chunk_date': upload.last_chunk_date,
            'failure_reason': upload.failure_reason
        })
    
    # Get summary statistics
    total_uploads = len(upload_data)
    pending_uploads = sum(1 for u in upload_data if u['status'] == 'pending')
    completed_uploads = sum(1 for u in upload_data if u['status'] == 'completed')
    failed_uploads = sum(1 for u in upload_data if u['status'] == 'failed')
    
    stats = {
        'total': total_uploads,
        'pending': pending_uploads,
        'completed': completed_uploads,
        'failed': failed_uploads
    }
    
    return render_template("admin/upload_status.html", uploads=upload_data, stats=stats)

#------------------------------------------------------
@admin.route('/zoom/user/<int:user_id>')
def zoom_user(user_id):
    user = Users.query.get_or_404(user_id)
    user.zoom_id = None
    db.session.commit()
    flash(f"Zoom ID for {user.name} has been deleted successfully!", "success")
    return redirect(url_for('admin.zoom'))

@admin.route('/zoom')
def zoom():
    zoom_meetings = Zoom_meeting.query.all()

    groups = Groups.query.all()

    
    return render_template("admin/zoom.html", 
                         zoom_meetings=zoom_meetings,

                         groups=groups,
            )

@admin.route('/zoom/<int:meeting_id>')
def view_zoom_meeting(meeting_id):
    # View details of a specific Zoom meeting
    meeting = Zoom_meeting.query.get_or_404(meeting_id)
    
    # Get all memberships (participants with their Zoom details)
    memberships = ZoomMeetingMember.query.filter_by(zoom_meeting_id=meeting.id).all()
    
    # Filter users based on meeting scope (subject, groups, stages, schools)
    users_query = Users.query
    
    # Filter by subject if specified
    if meeting.subject_id:
        users_query = users_query.filter(Users.subjectid == meeting.subject_id)
    
    # Filter by groups if specified
    if meeting.groups:
        group_ids = [group.id for group in meeting.groups]
        users_query = users_query.filter(Users.groupid.in_(group_ids))
    
    # Filter by stages if specified
    if meeting.stages:
        stage_ids = [stage.id for stage in meeting.stages]
        users_query = users_query.filter(Users.stageid.in_(stage_ids))
    
    # Filter by schools if specified
    if meeting.schools:
        school_ids = [school.id for school in meeting.schools]
        users_query = users_query.filter(Users.schoolid.in_(school_ids))
    
    #Get users with no zoom_id
    all_users = users_query.filter(Users.zoom_id.is_(None)).all()
    
    return render_template("admin/view_zoom_meeting.html", meeting=meeting, memberships=memberships, all_users=all_users)

@admin.route('/zoom/create', methods=['POST'])
def create_zoom_meeting():
    return "Paused for now"

    try:
        import re
        # Handle AJAX form submission to create a new Zoom meeting
        meeting_input = request.form.get('meeting_id')
        
        # Extract meeting ID from Zoom invite link or use as is
        # Zoom links can be like: https://zoom.us/j/1234567890 or https://us05web.zoom.us/j/1234567890
        meeting_id = meeting_input
        if 'zoom.us/' in meeting_input:
            # Extract meeting ID from URL
            match = re.search(r'/j/(\d+)', meeting_input)
            if match:
                meeting_id = match.group(1)
            else:
                return jsonify({
                    'success': False,
                    'message': 'Invalid Zoom link format. Could not extract meeting ID.'
                }), 400
        
        subject_id = request.form.get('subject_id')
        creator_id = current_user.id
        
        # Get selected groups, stages, and schools
        group_ids = request.form.getlist('groups[]')
        stage_ids = request.form.getlist('stages[]')
        school_ids = request.form.getlist('schools[]')
        
        # Check if meeting already exists
        existing_meeting = Zoom_meeting.query.filter_by(meeting_id=meeting_id).first()
        if existing_meeting:
            return jsonify({
                'success': False,
                'message': 'A meeting with this ID already exists!'
            }), 400
        
        # Create new Zoom meeting
        new_meeting = Zoom_meeting(
            meeting_id=meeting_id,
            subject_id=subject_id if subject_id else None,
            creator_id=creator_id
        )
        
        # Add relationships
        if group_ids:
            new_meeting.groups = Groups.query.filter(Groups.id.in_(group_ids)).all()
        if stage_ids:
            new_meeting.stages = Stages.query.filter(Stages.id.in_(stage_ids)).all()
        if school_ids:
            new_meeting.schools = Schools.query.filter(Schools.id.in_(school_ids)).all()
        
        db.session.add(new_meeting)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Zoom meeting created successfully!',
            'meeting_id': meeting_id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 400

@admin.route('/zoom/<int:meeting_id>/delete', methods=['POST'])
def delete_zoom_meeting(meeting_id):
    try:
        # Find the meeting
        meeting = Zoom_meeting.query.get_or_404(meeting_id)
        
        # Delete the meeting (cascade will handle memberships)
        db.session.delete(meeting)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Zoom meeting deleted successfully!'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 400

@admin.route('/zoom/link_participant', methods=['POST'])
def link_zoom_participant():
    try:
        data = request.get_json()
        zoom_id = data.get('zoom_id')
        user_id = data.get('user_id')
        
        if not zoom_id or not user_id:
            return jsonify({
                'success': False,
                'message': 'Missing zoom_id or user_id'
            }), 400
        
        # Find the membership record by zoom_id
        membership = ZoomMeetingMember.query.filter_by(zoom_id=zoom_id).first()
        
        if not membership:
            return jsonify({
                'success': False,
                'message': 'Membership not found for this Zoom ID'
            }), 404
        
        # Find the user
        user = Users.query.get_or_404(user_id)
        
        # Link the participant to the user
        membership.user_id = user_id
        
        # Update the user's zoom_id
        user.zoom_id = zoom_id
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Participant linked to {user.name} successfully!',
            'user_name': user.name,
            'user_email': user.email
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 400

@admin.route('/zoom/unlink_participant', methods=['POST'])
def unlink_zoom_participant():
    try:
        data = request.get_json()
        zoom_id = data.get('zoom_id')
        
        if not zoom_id:
            return jsonify({
                'success': False,
                'message': 'Missing zoom_id'
            }), 400
        
        # Find the membership record by zoom_id
        membership = ZoomMeetingMember.query.filter_by(zoom_id=zoom_id).first()
        
        if not membership:
            return jsonify({
                'success': False,
                'message': 'Membership not found for this Zoom ID'
            }), 404
        
        # Unlink the participant by setting user_id to None
        membership.user_id = None
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Participant unlinked successfully!'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 400


#--- Temp ---

@admin.route('/temp/activate/<int:user_id>')
def temp_activate(user_id):
    user = Users.query.get_or_404(user_id)
    user.student_whatsapp = user.phone_number_country_code + user.phone_number
    user.parent_whatsapp = user.parent_phone_number_country_code + user.parent_phone_number
    db.session.commit()
    flash(f"Student {user.name} has been activated successfully!", "success")
    return redirect(url_for('admin.student' , user_id=user_id))


@admin.route('/temp/deactivate/<int:user_id>')
def temp_deactivate(user_id):
    user = Users.query.get_or_404(user_id)
    user.student_whatsapp = None
    user.parent_whatsapp = None
    db.session.commit()
    flash(f"Student {user.name} has been deactivated successfully!", "success")
    return redirect(url_for('admin.student' , user_id=user_id))

#==================================================================================================
#Groups (Main route)
#==================================================================================================

@admin.route('/group/<int:group_id>')
def group(group_id):
    group = Groups.query.get_or_404(group_id)
    return render_template("admin/group.html", group=group)

@admin.route('/group/<int:group_id>/assignments')
def group_assignments(group_id):
    group = Groups.query.get_or_404(group_id)
    return render_template("admin/assignments/assignments.html", filter_group_id=group_id, group=group)

@admin.route('/group/<int:group_id>/exams')
def group_exams(group_id):
    group = Groups.query.get_or_404(group_id)
    return render_template("admin/online_exam/online_exam.html", filter_group_id=group_id, group=group)

@admin.route('/group/<int:group_id>/students', methods=['GET'])
def group_students(group_id):
    group = Groups.query.get_or_404(group_id)
    
    # Check if user has access to this group
    if current_user.role != "super_admin":
        group_ids = get_user_scope_ids()
        if group_id not in group_ids:
            flash("You don't have access to this group.", "danger")
            return redirect(url_for("admin.index"))
    
    page = request.args.get('page', 1, type=int)
    per_page = 51
    search = request.args.get('search', '', type=str).strip()
    
    # Filter students by the specific group
    query = Users.query.filter(
        Users.role == 'student',
        Users.code != 'nth',
        Users.code != 'Nth',
        Users.groupid == group_id
    )
    
    # Apply search filter
    if search:
        search_like = f"%{search}%"
        query = query.filter(
            (Users.name.ilike(search_like)) |
            (Users.code.ilike(search_like)) |
            (Users.phone_number.ilike(search_like)) |
            (Users.email.ilike(search_like)) |
            (Users.parent_phone_number.ilike(search_like))
        )
    
    query = query.distinct()
    pagination = query.order_by(Users.code.asc()).paginate(page=page, per_page=per_page, error_out=False)
    users = pagination.items
    
    return render_template(
        'admin/group_students.html',
        users=users,
        group=group,
        pagination=pagination,
    )

@admin.route('/group/<int:group_id>/assistants', methods=['GET'])
def group_assistants(group_id):
    group = Groups.query.get_or_404(group_id)
    
    # Check if user has access to this group
    if current_user.role != "super_admin":
        group_ids = get_user_scope_ids()
        if group_id not in group_ids:
            flash("You don't have access to this group.", "danger")
            return redirect(url_for("admin.index"))
    
    page = request.args.get('page', 1, type=int)
    per_page = 51
    search = request.args.get('search', '', type=str).strip()
    
    # Filter users who are admins or super_admins and have this group in managed_groups
    query = Users.query.filter(
        Users.role.in_(['admin', 'super_admin'])
    ).filter(
        Users.managed_groups.any(Groups.id == group_id)
    )
    
    # Apply search filter
    if search:
        search_like = f"%{search}%"
        query = query.filter(
            (Users.name.ilike(search_like)) |
            (Users.email.ilike(search_like)) |
            (Users.phone_number.ilike(search_like))
        )
    
    query = query.distinct()
    pagination = query.order_by(Users.name.asc()).paginate(page=page, per_page=per_page, error_out=False)
    users = pagination.items
    
    return render_template(
        'admin/group_assistants.html',
        users=users,
        group=group,
        pagination=pagination,
    )


#=================================================================
#=================================================================
# SUBMISSION REVIEW SYSTEM (Super Admin Only)
#=================================================================
#=================================================================

# Approve a submission (sends WhatsApp notifications)
@admin.route("/submissions/review/approve/<int:submission_id>", methods=["POST"])
def approve_submission(submission_id):
    """Head approves a corrected submission and sends WhatsApp notifications"""
    if current_user.role != "super_admin":
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({"success": False, "message": "Access denied. Only Heads can approve submissions."}), 403
        flash("Access denied. Only Heads can approve submissions.", "danger")
        return redirect(url_for("admin.home"))
    
    submission = Submissions.query.get_or_404(submission_id)
    
    if not submission.corrected:
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({"success": False, "message": "This submission hasn't been corrected yet."}), 400
        flash("This submission hasn't been corrected yet.", "warning")
        return redirect(request.referrer or url_for("admin.home"))
    
    if submission.reviewed:
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({"success": False, "message": "This submission has already been reviewed."}), 400
        flash("This submission has already been reviewed.", "info")
        return redirect(request.referrer or url_for("admin.home"))
    
    # Mark as reviewed
    submission.reviewed = True
    submission.reviewed_by_id = current_user.id
    submission.review_date = datetime.now(GMT_PLUS_2)
    
    try:
        db.session.commit()
        
        # Send WhatsApp notifications
        assignment = submission.assignment
        mark = submission.mark or "Graded"
        
        try:
            if assignment.type == "Exam":
                # Student notification (unchanged, or optional: You may skip or keep)
                send_whatsapp_message(
                    submission.student.phone_number,
                    f"Hi *{submission.student.name}*👋,\n\n"
                    f"*{assignment.title}*\n"
                    f"You're correction is returned please check your account\n\n"
                    f"You scored : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                    "_For further inquiries send to Dr. Adham_"
                )
                # Parent notification (formatted as requested)
                send_whatsapp_message(
                    submission.student.parent_phone_number,
                    f"Dear Parent,\n"
                    f"*{submission.student.name}*\n\n"
                    f"Quiz *{assignment.title}* on *{assignment.deadline_date.strftime('%d/%m/%Y') if assignment.deadline_date else 'N/A'}* correction is returned on the student's account on website\n\n"
                    f"Scored *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                    f"Dr. Adham will send the gradings on the group\n"
                    "_For further inquiries send to Dr. Adham_"
                )
            else:
                # Student notification (unchanged, or optional: You may skip or keep)
                send_whatsapp_message(
                    submission.student.phone_number,
                    f"Hi *{submission.student.name}*👋,\n\n"
                    f"*{assignment.title}*\n"
                    f"You're correction is returned please check your account\n\n"
                    f"You scored : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                    "_For further inquiries send to Dr. Adham_"
                )
                # Parent notification for homework, as requested
                send_whatsapp_message(
                    submission.student.parent_phone_number,
                    f"Dear Parent,\n"
                    f"*{submission.student.name}*\n\n"
                    f"Homework *{assignment.title}* due on *{assignment.deadline_date.strftime('%d/%m/%Y') if assignment.deadline_date else 'N/A'}* is returned on the student's account on website \n\n"
                    f"Score : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                    "_For further inquiries send to Dr. Adham_"
                )
        except Exception as e:
            pass  # Don't fail if WhatsApp fails
        
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({"success": True, "message": "Submission approved and notifications sent!"}), 200
        flash("Submission approved and notifications sent!", "success")
    except Exception as e:
        db.session.rollback()
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({"success": False, "message": f"Error approving submission: {str(e)}"}), 500
        flash(f"Error approving submission: {str(e)}", "danger")
    
    return redirect(request.referrer or url_for("admin.home"))


# Reject a submission (request re-correction)
@admin.route("/submissions/review/reject/<int:submission_id>", methods=["POST"])
def reject_submission(submission_id):
    """Head rejects a corrected submission and requests re-correction"""
    if current_user.role != "super_admin":
        flash("Access denied. Only Heads can reject submissions.", "danger")
        return redirect(url_for("admin.home"))
    
    submission = Submissions.query.get_or_404(submission_id)
    
    if not submission.corrected:
        flash("This submission hasn't been corrected yet.", "warning")
        return redirect(request.referrer or url_for("admin.home"))
    
    if submission.reviewed:
        flash("This submission has already been approved.", "info")
        return redirect(request.referrer or url_for("admin.home"))
    
    # Reset correction status to request re-correction
    rejection_reason = request.form.get("rejection_reason", "")
    
    submission.corrected = False
    submission.mark = None
    submission.correction_date = None
    # Keep corrected_by_id for tracking
    
    try:
        db.session.commit()
        
        # Notify the assistant who corrected it
        if submission.corrector and submission.corrector.phone_number:
            try:
                send_whatsapp_message(
                    submission.corrector.phone_number,
                    f"Your correction for {submission.student.name}'s {submission.assignment.type} '{submission.assignment.title}' was rejected. Reason: {rejection_reason or 'No reason provided'}"
                )
            except Exception as e:
                pass
        
        flash("Submission rejected. Assistant has been notified.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error rejecting submission: {str(e)}", "danger")
    
    return redirect(request.referrer or url_for("admin.home"))


# View all pending reviews (awaiting approval)
@admin.route("/submissions/reviews")
def view_all_reviews():
    """View all submissions awaiting review"""
    if current_user.role != "super_admin":
        flash("Access denied. Only Heads can view reviews.", "danger")
        return redirect(url_for("admin.home"))
    
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Get all corrected but not reviewed submissions
    pending_reviews = Submissions.query.filter_by(
        corrected=True, 
        reviewed=False
    ).order_by(Submissions.correction_date.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template(
        "admin/submissions/reviews_all.html",
        submissions=pending_reviews.items,
        pagination=pending_reviews,
        title="All Pending Reviews"
    )


# View reviews by specific assignment
@admin.route("/submissions/reviews/assignment/<int:assignment_id>")
def view_reviews_by_assignment(assignment_id):
    """View submissions awaiting review for a specific assignment"""
    if current_user.role != "super_admin":
        flash("Access denied. Only Heads can view reviews.", "danger")
        return redirect(url_for("admin.home"))
    
    assignment = Assignments.query.get_or_404(assignment_id)
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Get all corrected but not reviewed submissions for this assignment
    pending_reviews = Submissions.query.filter_by(
        assignment_id=assignment_id,
        corrected=True, 
        reviewed=False
    ).order_by(Submissions.correction_date.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template(
        "admin/submissions/reviews_by_assignment.html",
        submissions=pending_reviews.items,
        pagination=pending_reviews,
        assignment=assignment,
        title=f"Reviews for {assignment.title}"
    )




# View reviews by specific assistant (who corrected them)
@admin.route("/submissions/reviews/assistant/<int:assistant_id>")
def view_reviews_by_assistant(assistant_id):
    """View submissions corrected by a specific assistant awaiting review"""
    if current_user.role != "super_admin":
        flash("Access denied. Only Heads can view reviews.", "danger")
        return redirect(url_for("admin.home"))
    
    assistant = Users.query.get_or_404(assistant_id)
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Get all corrected but not reviewed submissions by this assistant
    pending_reviews = Submissions.query.filter_by(
        corrected_by_id=assistant_id,
        corrected=True, 
        reviewed=False
    ).order_by(Submissions.correction_date.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template(
        "admin/submissions/reviews_by_assistant.html",
        submissions=pending_reviews.items,
        pagination=pending_reviews,
        assistant=assistant,
        title=f"Reviews for corrections by {assistant.name}"
    )


# Approve all corrected submissions for an assignment
@admin.route("/assignments/<int:assignment_id>/approve-all-submissions", methods=["POST"])
def approve_all_submissions(assignment_id):
    """Head approves all corrected but not reviewed submissions for an assignment"""
    if current_user.role != "super_admin":
        return jsonify({"success": False, "message": "Access denied. Only Heads can approve submissions."}), 403
    
    assignment = Assignments.query.get_or_404(assignment_id)
    
    # Get all corrected but not reviewed submissions for this assignment
    pending_submissions = Submissions.query.filter_by(
        assignment_id=assignment_id,
        corrected=True,
        reviewed=False
    ).all()
    
    if not pending_submissions:
        return jsonify({"success": False, "message": "No submissions awaiting review."}), 400
    
    approved_count = 0
    
    try:
        for submission in pending_submissions:
            submission.reviewed = True
            submission.reviewed_by_id = current_user.id
            submission.review_date = datetime.now(GMT_PLUS_2)
            
            # Send WhatsApp notifications
            try:
                if assignment.type == "Exam":
                    # Student notification (unchanged, or optional: You may skip or keep)
                    send_whatsapp_message(
                        submission.student.phone_number,
                        f"Hi *{submission.student.name}*👋,\n\n"
                        f"*{assignment.title}*\n"
                        f"You're correction is returned please check your account\n\n"
                        f"You scored : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
                    # Parent notification (formatted as requested)
                    send_whatsapp_message(
                        submission.student.parent_phone_number,
                        f"Dear Parent,\n"
                        f"*{submission.student.name}*\n\n"
                        f"Quiz *{assignment.title}* on *{assignment.deadline_date.strftime('%d/%m/%Y') if assignment.deadline_date else 'N/A'}* correction is returned on the student's account on website\n\n"
                        f"Scored *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        f"Dr. Adham will send the gradings on the group\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
                else:
                    # Student notification (unchanged, or optional: You may skip or keep)
                    send_whatsapp_message(
                        submission.student.phone_number,
                        f"Hi *{submission.student.name}*👋,\n\n"
                        f"*{assignment.title}*\n"
                        f"You're correction is returned please check your account\n\n"
                        f"You scored : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
                    # Parent notification for homework, as requested
                    send_whatsapp_message(
                        submission.student.parent_phone_number,
                        f"Dear Parent,\n"
                        f"*{submission.student.name}*\n\n"
                        f"Homework *{assignment.title}* due on *{assignment.deadline_date.strftime('%d/%m/%Y') if assignment.deadline_date else 'N/A'}* is returned on the student's account on website \n\n"
                        f"Score : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
            except Exception as e:
                pass  # Don't fail if WhatsApp fails
            approved_count += 1
        
        db.session.commit()
        return jsonify({
            "success": True, 
            "message": f"Approved {approved_count} submissions",
            "approved_count": approved_count
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error approving submissions: {str(e)}"}), 500


# Approve bulk submissions by IDs
@admin.route("/submissions/approve-bulk", methods=["POST"])
def approve_bulk_submissions():
    """Head approves multiple selected submissions at once"""
    if current_user.role != "super_admin":
        return jsonify({"success": False, "message": "Access denied. Only Heads can approve submissions."}), 403
    
    data = request.get_json()
    submission_ids = data.get('submission_ids', [])
    
    if not submission_ids or not isinstance(submission_ids, list):
        return jsonify({"success": False, "message": "No submissions selected."}), 400
    
    approved_count = 0
    
    try:
        for submission_id in submission_ids:
            submission = Submissions.query.get(submission_id)
            
            if not submission:
                continue
                
            if not submission.corrected:
                continue
                
            if submission.reviewed:
                continue
            
            # Mark as reviewed
            submission.reviewed = True
            submission.reviewed_by_id = current_user.id
            submission.review_date = datetime.now(GMT_PLUS_2)
            
            # Send WhatsApp notifications
            assignment = submission.assignment
            mark = submission.mark or "Graded"
            
            try:
                if assignment.type == "Exam":
                    # Student notification (unchanged, or optional: You may skip or keep)
                    send_whatsapp_message(
                        submission.student.phone_number,
                        f"Hi *{submission.student.name}*👋,\n\n"
                        f"*{assignment.title}*\n"
                        f"You're correction is returned please check your account\n\n"
                        f"You scored : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
                    # Parent notification (formatted as requested)
                    send_whatsapp_message(
                        submission.student.parent_phone_number,
                        f"Dear Parent,\n"
                        f"*{submission.student.name}*\n\n"
                        f"Quiz *{assignment.title}* on *{assignment.deadline_date.strftime('%d/%m/%Y') if assignment.deadline_date else 'N/A'}* correction is returned on the student's account on website\n\n"
                        f"Scored *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        f"Dr. Adham will send the gradings on the group\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
                else:
                    # Student notification (unchanged, or optional: You may skip or keep)
                    send_whatsapp_message(
                        submission.student.phone_number,
                        f"Hi *{submission.student.name}*👋,\n\n"
                        f"*{assignment.title}*\n"
                        f"You're correction is returned please check your account\n\n"
                        f"You scored : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
                    # Parent notification for homework, as requested
                    send_whatsapp_message(
                        submission.student.parent_phone_number,
                        f"Dear Parent,\n"
                        f"*{submission.student.name}*\n\n"
                        f"Homework *{assignment.title}* due on *{assignment.deadline_date.strftime('%d/%m/%Y') if assignment.deadline_date else 'N/A'}* is returned on the student's account on website \n\n"
                        f"Score : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
            except Exception as e:
                pass  # Don't fail if WhatsApp fails
            
            approved_count += 1
        
        db.session.commit()
        return jsonify({
            "success": True, 
            "message": f"Approved {approved_count} submission(s)",
            "approved_count": approved_count
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error approving submissions: {str(e)}"}), 500


# Approve submissions by index range
@admin.route("/submissions/approve-range", methods=["POST"])
def approve_range_submissions():
    """Head approves submissions by index range (from pagination page)"""
    if current_user.role != "super_admin":
        return jsonify({"success": False, "message": "Access denied. Only Heads can approve submissions."}), 403
    
    data = request.get_json()
    from_index = data.get('from_index')
    to_index = data.get('to_index')
    page = data.get('page', 1)
    
    if not from_index or not to_index:
        return jsonify({"success": False, "message": "Invalid range specified."}), 400
    
    try:
        from_index = int(from_index)
        to_index = int(to_index)
        page = int(page)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid range values."}), 400
    
    if from_index < 1 or to_index < 1 or from_index > to_index:
        return jsonify({"success": False, "message": "Invalid range."}), 400
    
    per_page = 20
    
    try:
        # Get all corrected but not reviewed submissions with same pagination as view
        all_pending = Submissions.query.filter_by(
            corrected=True, 
            reviewed=False
        ).order_by(Submissions.correction_date.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        submissions_list = all_pending.items
        
        # Validate range against actual page items
        if to_index > len(submissions_list):
            return jsonify({
                "success": False, 
                "message": f"Range exceeds available submissions. Max index: {len(submissions_list)}"
            }), 400
        
        # Get submissions in range (convert to 0-indexed)
        submissions_to_approve = submissions_list[from_index-1:to_index]
        
        approved_count = 0
        
        for submission in submissions_to_approve:
            if submission.reviewed:
                continue
                
            # Mark as reviewed
            submission.reviewed = True
            submission.reviewed_by_id = current_user.id
            submission.review_date = datetime.now(GMT_PLUS_2)
            
            # Send WhatsApp notifications
            assignment = submission.assignment
            mark = submission.mark or "Graded"
            
            try:
                if assignment.type == "Exam":
                    # Student notification (unchanged, or optional: You may skip or keep)
                    send_whatsapp_message(
                        submission.student.phone_number,
                        f"Hi *{submission.student.name}*👋,\n\n"
                        f"*{assignment.title}*\n"
                        f"You're correction is returned please check your account\n\n"
                        f"You scored : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
                    # Parent notification (formatted as requested)
                    send_whatsapp_message(
                        submission.student.parent_phone_number,
                        f"Dear Parent,\n"
                        f"*{submission.student.name}*\n\n"
                        f"Quiz *{assignment.title}* on *{assignment.deadline_date.strftime('%d/%m/%Y') if assignment.deadline_date else 'N/A'}* correction is returned on the student's account on website\n\n"
                        f"Scored *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        f"Dr. Adham will send the gradings on the group\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
                else:
                    # Student notification (unchanged, or optional: You may skip or keep)
                    send_whatsapp_message(
                        submission.student.phone_number,
                        f"Hi *{submission.student.name}*👋,\n\n"
                        f"*{assignment.title}*\n"
                        f"You're correction is returned please check your account\n\n"
                        f"You scored : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
                    # Parent notification for homework, as requested
                    send_whatsapp_message(
                        submission.student.parent_phone_number,
                        f"Dear Parent,\n"
                        f"*{submission.student.name}*\n\n"
                        f"Homework *{assignment.title}* due on *{assignment.deadline_date.strftime('%d/%m/%Y') if assignment.deadline_date else 'N/A'}* is returned on the student's account on website \n\n"
                        f"Score : *{submission.mark if submission.mark else 'N/A'}* / {assignment.out_of if hasattr(assignment, 'out_of') else 'N/A'}\n\n"
                        "_For further inquiries send to Dr. Adham_"
                    )
            except Exception as e:
                pass  # Don't fail if WhatsApp fails
            
            approved_count += 1
        
        db.session.commit()
        return jsonify({
            "success": True, 
            "message": f"Approved {approved_count} submission(s) from #{from_index} to #{to_index}",
            "approved_count": approved_count
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error approving submissions: {str(e)}"}), 500


    
#=================================================================
#=================================================================
# ASSISTANT TRACKING SYSTEM
#=================================================================
#=================================================================

@admin.route('/track/assistants/<int:group_id>', methods=['GET'])
def track_assistants(group_id):
    group = Groups.query.get_or_404(group_id)
    
    # Check if user has access to this group
    if current_user.role != "super_admin":
        group_ids = get_user_scope_ids()
        if group_id not in group_ids:
            flash("You don't have access to this group.", "danger")
            return redirect(url_for("admin.index"))
    
    # Get all assistants who manage this group
    assistants = Users.query.filter(
        Users.role.in_(['admin', 'super_admin']),
        Users.managed_groups.any(Groups.id == group_id)
    ).all()
    
    # Get all assignments for this group (both legacy and MM)
    assignments = Assignments.query.filter(
        or_(
            Assignments.groupid == group_id,
            Assignments.groups_mm.any(Groups.id == group_id)
        )
    ).order_by(Assignments.deadline_date.desc()).all()
    
    # Build tracking data for each assistant
    tracking_data = {}
    for assistant in assistants:
        assistant_data = {
            'id': assistant.id,
            'name': assistant.name,
            'email': assistant.email,
            'assignment_stats': {}
        }
        
        # Calculate stats for each assignment
        for assignment in assignments:
            # Count submissions assigned to this assistant
            assigned_count = Submissions.query.filter(
                Submissions.assignment_id == assignment.id,
                Submissions.assigned_to_id == assistant.id
            ).count()
            
            # Count submissions corrected by this assistant
            corrected_count = Submissions.query.filter(
                Submissions.assignment_id == assignment.id,
                Submissions.corrected_by_id == assistant.id,
                Submissions.corrected == True
            ).count()
            
            # Count submissions corrected but pending review
            pending_review_count = Submissions.query.filter(
                Submissions.assignment_id == assignment.id,
                Submissions.corrected_by_id == assistant.id,
                Submissions.corrected == True,
                Submissions.reviewed == False
            ).count()
            
            # Only add assignments with activity for this assistant
            if assigned_count > 0 or corrected_count > 0:
                assistant_data['assignment_stats'][assignment.id] = {
                    'assignment': assignment,
                    'assigned_count': assigned_count,
                    'corrected_count': corrected_count,
                    'pending_review_count': pending_review_count
                }
        
        # Add assistant data if they have any assignment activity
        if assistant_data['assignment_stats']:
            tracking_data[assistant.id] = assistant_data
    
    return render_template(
        'admin/assistant/track_assistants.html',
        group=group,
        assistants=assistants,
        assignments=assignments,
        tracking_data=tracking_data
    )

    
