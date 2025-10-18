from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from .models import Users, Parent, Assignments, Submissions, QuizGrades, Announcements, NextQuiz    
from . import db
from werkzeug.security import check_password_hash, generate_password_hash
import pytz
from datetime import datetime
from functools import wraps
from .student import get_all
import os
import json



parent = Blueprint('parent', __name__, template_folder='templates')

# Define the timezone
gmt_plus_2 = pytz.timezone('Etc/GMT-3')

def update_last_website_access():
    """Update the parent's last_website_access field if logged in, but only if at least 1 minute has passed."""
    if 'parent_id' in session:
        parent_user = Parent.query.get(session['parent_id'])
        if parent_user:
            now = datetime.now(gmt_plus_2)
            last_access = parent_user.last_website_access
            
            # Only proceed if last_access is not None
            if last_access:
                # FIX: Make the naive datetime from the DB aware of its timezone
                last_access = gmt_plus_2.localize(last_access)

            # Now the comparison will work correctly
            if not last_access or (now - last_access).total_seconds() > 60:
                parent_user.last_website_access = now.replace(tzinfo=None) # Store as naive in DB
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
def parent_login_required(f):
    """Decorator to require parent login and update last_website_access."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'parent_id' not in session:
            flash('Please login to access the parent portal.', 'error')
            return redirect(url_for('parent.login'))
        update_last_website_access()
        return f(*args, **kwargs)
    return decorated_function

def get_current_parent():
    """Get the current logged-in parent."""
    if 'parent_id' in session:
        return Parent.query.get(session['parent_id'])
    return None

@parent.route('/')
def parent_home():
    return redirect(url_for('parent.login'))

@parent.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone_number = request.form['parent_number']
        phone_number = phone_number.replace(" ", "")

        # Try to find existing parent
        parent_user = Parent.query.filter_by(phone_number=phone_number).first()

        if not parent_user:
            # Try to find a student who has this phone as a parent
            student = Users.query.filter_by(parent_phone_number=phone_number, role='student').first()
            if student:
                # Create parent and associate with student, login auto
                parent_user = Parent(
                    phone_number=phone_number,
                    student_id=student.id
                )
                db.session.add(parent_user)
                db.session.commit()
                parent_user.login_count += 1
                parent_user.last_website_access = datetime.now(gmt_plus_2)
                db.session.commit()
                session['parent_id'] = parent_user.id
                session.permanent = True
                flash('Parent account created and logged in automatically!', 'success')
                return redirect(url_for('parent.dashboard'))
            else:
                # No matching student, cannot create parent
                flash('Phone number not found. Please check with your student first.', 'error')
                return redirect(url_for('parent.login'))
        else:
            # Parent exists, log in auto
            parent_user.login_count += 1
            parent_user.last_website_access = datetime.now(gmt_plus_2)
            db.session.commit()
            session['parent_id'] = parent_user.id
            session.permanent = True
            flash('Successfully logged in!', 'success')
            return redirect(url_for('parent.dashboard'))

    return render_template('parent/login.html')




@parent.route('/dashboard')
@parent_login_required
def dashboard():
    update_last_website_access()
    current_parent = get_current_parent()
    if not current_parent:
        flash('Access denied. Please login as a parent.', 'error')
        return redirect(url_for('parent.login'))
    
    student = Users.query.get(current_parent.student_id)
    if not student:
        flash('Student record not found!', 'error')
        return redirect(url_for('parent.login'))
    
    # Get student's assignments and submissions
    assignments = Assignments.query.filter(
        (Assignments.groupid == student.groupid) |
        (Assignments.stageid == student.stageid) |
        (Assignments.schoolid == student.schoolid)
    ).order_by(Assignments.deadline_date.desc()).limit(10).all()
    
    submissions = Submissions.query.filter_by(student_id=student.id).all()
    submitted_assignment_ids = [sub.assignment_id for sub in submissions]
    
    # Get student's quiz grades
    quiz_grades = QuizGrades.query.filter_by(student_id=student.id).order_by(QuizGrades.id.desc()).limit(10).all()
    
    # Calculate statistics
    total_assignments = len(assignments)
    submitted_assignments = len([a for a in assignments if a.id in submitted_assignment_ids])
    pending_assignments = total_assignments - submitted_assignments
    
    graded_quizzes = len([q for q in quiz_grades if q.mark and q.mark != 0])
    
    # Get last quiz mark
    last_quiz_mark = None
    if quiz_grades:
        latest_quiz = quiz_grades[0]
        if latest_quiz.mark and latest_quiz.mark != 0:
            try:
                mark = float(latest_quiz.mark)
                full_mark = float(latest_quiz.quiz.full_mark)
                percentage = (mark / full_mark) * 100
                last_quiz_mark = f"{latest_quiz.mark}/{latest_quiz.quiz.full_mark} ({percentage:.1f}%)"
            except (ValueError, ZeroDivisionError):
                last_quiz_mark = latest_quiz.mark
        else:
            last_quiz_mark = "0"
    
    # Get last assignment status
    last_assignment_status = None
    if assignments:
        latest_assignment = assignments[0]
        if latest_assignment.id in submitted_assignment_ids:
            last_assignment_status = "Submitted"
        else:
            last_assignment_status = "Pending"

    # Get next quiz
    next_quiz = NextQuiz.query.filter_by(stageid=student.stageid ,groupid=student.groupid ,schoolid=student.schoolid).order_by(NextQuiz.quiz_date.asc()).first()





    return render_template('parent/dashboard.html',
                         student=student,
                         total_assignments=total_assignments,
                         submitted_assignments=submitted_assignments,
                         pending_assignments=pending_assignments,
                         graded_quizzes=graded_quizzes,
                         last_quiz_mark=last_quiz_mark,
                         last_assignment_status=last_assignment_status,
                         next_quiz=next_quiz)


                         

@parent.route('/assignments')
@parent_login_required
def assignments():
    current_parent = get_current_parent()
    if not current_parent:
        flash('Access denied. Please login as a parent.', 'error')
        return redirect(url_for('parent.login'))
    
    student = Users.query.get(current_parent.student_id)
    if not student:
        flash('Student record not found!', 'error')
        return redirect(url_for('parent.login'))
    
    # Get all assignments for the student
    assignments = get_all(Assignments, student.id).order_by(Assignments.id.desc()).filter(Assignments.type == "Assignment").all()
    
    # Get submissions
    submissions = Submissions.query.filter_by(student_id=student.id).all()
    submissions_dict = {sub.assignment_id: sub for sub in submissions}
    submitted_assignment_ids = [sub.assignment_id for sub in submissions]
    
    # Current time for deadline comparison
    current_date = datetime.now(gmt_plus_2)
    
    # Process assignments to fix timezone issues and add status
    for assignment in assignments:
        try:
            if assignment.deadline_date:
                assignment.deadline_date = gmt_plus_2.localize(assignment.deadline_date)
                assignment.is_overdue = assignment.deadline_date < current_date
            else:
                assignment.is_overdue = False
        except Exception:
            assignment.is_overdue = False
        
        # Check if submitted
        assignment.is_submitted = assignment.id in submitted_assignment_ids
    
    # Calculate counts
    total_count = len(assignments)
    submitted_count = len(submitted_assignment_ids)
    pending_count = total_count - submitted_count
    
    return render_template('parent/assignments.html',
                         student=student,
                         assignments=assignments,
                         submissions_dict=submissions_dict,
                         submitted_assignment_ids=submitted_assignment_ids,
                         total_count=total_count,
                         submitted_count=submitted_count,
                         pending_count=pending_count)

@parent.route('/grades')
@parent_login_required
def grades():
    current_parent = get_current_parent()
    if not current_parent:
        flash('Access denied. Please login as a parent.', 'error')
        return redirect(url_for('parent.login'))

    student = Users.query.get(current_parent.student_id)
    if not student:
        flash('Student record not found!', 'error')
        return redirect(url_for('parent.login'))

    # Get all "Exam" assignment marks for the student using Submissions and Assignments join
    exam_submissions_query = (
        Submissions.query
        .join(Assignments, Submissions.assignment_id == Assignments.id)
        .filter(
            Submissions.student_id == student.id,
            Assignments.type.ilike('Exam')  # case-insensitive match
        )
        .order_by(Assignments.id.desc())
    )
    exam_submissions_raw = exam_submissions_query.all()

    # Will contain list of dictionaries, each with precomputed fields
    exam_submissions = []
    numeric_grades = []
    absent_count = 0

    for submission in exam_submissions_raw:
        mark_raw = submission.mark

        # Ensure mark is always a visible string for template, always pass what is in DB (even if 0 or empty) as "mark"
        mark_display = mark_raw if mark_raw not in (None, "") else "-"

        # Compute numeric value for grading statistics and derived fields
        try:
            mark_val = float(mark_raw) if mark_raw not in (None, "", "-") else None
        except Exception:
            mark_val = None

        # Get assignment points (out_of)
        try:
            full_mark = float(getattr(submission.assignment, "out_of", 0) or 0)
        except Exception:
            full_mark = 0

        percentage = None
        grade_letter = "-"
        status = "Absent"

        # Status and further derived info
        # Mark of "0" (string or int) means absent or "zero", but still should be rendered in the table
        if mark_val is not None and full_mark > 0:
            percentage = round((mark_val / full_mark) * 100, 1)
            # Letter grade logic
            if percentage >= 90:
                grade_letter = "A*"
            elif percentage >= 80:
                grade_letter = "A"
            elif percentage >= 70:
                grade_letter = "B"
            elif percentage >= 60:
                grade_letter = "C"
            elif percentage >= 50:
                grade_letter = "D"
            else:
                grade_letter = "-"
            # Consider 0 as absent
            if str(mark_raw) == "0" or mark_raw == 0:
                absent_count += 1
                status = "Absent"
            else:
                numeric_grades.append(percentage)
                status = "Submitted"
        elif str(mark_raw) == "0" or mark_raw == 0:
            absent_count += 1
            percentage = None
            grade_letter = "-"
            status = "Absent"
        else:
            percentage = None
            grade_letter = "-"
            status = "Absent"

        exam_submissions.append({
            "assignment": submission.assignment,
            "mark": mark_display,  # always the raw DB value!
            "full_mark": int(full_mark) if full_mark else None,
            "percentage": percentage,
            "grade_letter": grade_letter,
            "status": status
        })

    graded_exams_count = len([s for s in exam_submissions if s["status"] == "Submitted"])
    average_grade = round(sum(numeric_grades) / len(numeric_grades), 1) if numeric_grades else 0
    highest_grade = round(max(numeric_grades), 1) if numeric_grades else 0

    # Get last exam mark (latest, non-absent, as "mark"/"full_mark")
    last_exam_mark = "N/A"
    if exam_submissions:
        for latest in exam_submissions:
            # Find latest with mark not empty and not "0"
            if latest['mark'] is not None and latest['mark'] not in ("", "-", "0", 0):
                try:
                    last_exam_mark = f"{latest['mark']}/{latest['full_mark']}" if latest['full_mark'] is not None else str(latest['mark'])
                except Exception:
                    last_exam_mark = str(latest['mark'])
                break
        else:
            # All marks are "0" or "-", so find if any are "0"
            for latest in exam_submissions:
                if str(latest['mark']) == "0":
                    last_exam_mark = "0"
                    break
            else:
                last_exam_mark = "N/A"

    return render_template(
        'parent/grades.html',
        student=student,
        exam_submissions=exam_submissions,
        graded_exams_count=graded_exams_count,
        absent_count=absent_count,
        average_grade=average_grade,
        highest_grade=highest_grade,
        last_exam_mark=last_exam_mark
    )

@parent.route('/logout')
@parent_login_required
def logout():
    session.pop('parent_id', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('parent.login'))
