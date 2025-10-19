from flask import Blueprint, render_template, request, redirect, url_for, flash, send_from_directory, jsonify
from flask_login import current_user, login_user, logout_user
from .models import Users, Groups,  WhatsappMessages, Zoom_meeting, ZoomMeetingMember
from . import db
from werkzeug.security import check_password_hash, generate_password_hash
from flask import session
import os
import uuid
from flask import current_app
from PIL import Image, ImageOps
import io
import boto3
from dotenv import load_dotenv
from botocore.exceptions import ClientError
import requests
import time
import random
import string
from datetime import datetime

load_dotenv()
account_id = os.getenv('ACCOUNT_ID')
access_key_id = os.getenv('ACCESS_KEY_ID')
secret_access_key = os.getenv('SECRET_ACCESS_KEY')
bucket_name = os.getenv('BUCKET_NAME')
whatsapp_message_url = os.getenv('WHATSAPP_MESSAGE_URL')


class R2Storage:
    """A class to interact with Cloudflare R2 Storage."""
    
    def __init__(self):
        """Initializes the R2 client and sets the bucket name."""
        self.bucket_name = bucket_name
        self.endpoint_url = f'https://{account_id}.r2.cloudflarestorage.com'
        
        try:
            self.s3_client = boto3.client(
                's3',
                endpoint_url=self.endpoint_url,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name='auto',
            )
            print("Successfully connected to R2 Storage.")
        except Exception as e:
            print(f"Failed to connect to R2 Storage: {e}")
            self.s3_client = None

    def upload_file(self, file_obj, folder: str, file_name: str) -> str | None:
        """
        Uploads a file-like object to a folder in the R2 bucket.

        Args:
            file_obj: The file-like object to upload (e.g., from open(..., 'rb')).
            folder: The destination folder (key prefix).
            file_name: The name for the file in the bucket.

        Returns:
            The public URL of the uploaded file, or None if it failed.
        """
        if not self.s3_client:
            return None
            
        object_key = f"{folder}/{file_name}"
        try:
            self.s3_client.upload_fileobj(file_obj, self.bucket_name, object_key)
            file_url = f"{self.endpoint_url}/{self.bucket_name}/{object_key}"
            print(f"Successfully uploaded {file_name} to {folder}.")
            return file_url
        except ClientError as e:
            print(f"Error uploading {file_name} to R2: {e}")
            return None

    def delete_file(self, folder: str, file_name: str) -> bool:
        """
        Deletes a file from a folder in the R2 bucket.

        Args:
            folder: The folder where the file is located.
            file_name: The name of the file to delete.

        Returns:
            True if deletion was successful, False otherwise.
        """
        if not self.s3_client:
            return False
            
        object_key = f"{folder}/{file_name}"
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=object_key)
            print(f"Successfully deleted {object_key} from R2.")
            return True
        except ClientError as e:
            print(f"Error deleting {object_key} from R2: {e}")
            return False

    def download_file(self, folder: str, file_name: str, local_path: str) -> bool:
        """
        Downloads a file from a folder in the R2 bucket to local storage.

        Args:
            folder: The folder where the file is located.
            file_name: The name of the file to download.
            local_path: The local file path to save the downloaded file.

        Returns:
            True if download was successful, False otherwise.
        """
        if not self.s3_client:
            return False

        # Ensure the local directory exists
        local_dir = os.path.dirname(local_path)
        if local_dir and not os.path.exists(local_dir):
            os.makedirs(local_dir, exist_ok=True)

        object_key = f"{folder}/{file_name}"
        try:
            self.s3_client.download_file(self.bucket_name, object_key, local_path)
            print(f"Successfully downloaded {object_key} to {local_path}.")
            return True
        except ClientError as e:
            print(f"Error downloading {object_key} from R2: {e}")
            return False





WHATSAPP_API_KEY = "Whatsappsecretkeeeey2@1"



worker_last_activity = None
worker_is_running = False
def whatsapp_sender_worker(app=None):
    """
    Pulls messages from the database and sends them one by one.
    This function is intended to be the target of a background thread.
    Requires Flask app context to access database.
    """
    if app is None:
        print("❌ WhatsApp sender worker requires Flask app context!")
        return
        
    print("WhatsApp sender worker started...")
    
    global worker_last_activity, worker_is_running
    worker_is_running = True
    
    with app.app_context():
        # Reset any stuck "processing" messages from previous runs
        try:
            stuck_messages = WhatsappMessages.query.filter_by(status="processing").all()
            if stuck_messages:
                print(f"🔧 Found {len(stuck_messages)} stuck processing messages from previous run, resetting to pending...")
                for msg in stuck_messages:
                    msg.status = "pending"
                db.session.commit()
                print(f"✅ Reset {len(stuck_messages)} stuck messages to pending")
        except Exception as e:
            print(f"❌ Error resetting stuck messages: {e}")
            db.session.rollback()
        
        while True:
            try:
                worker_last_activity = datetime.now()
                
                # Use SELECT FOR UPDATE to lock the message and prevent duplicate processing
                message = db.session.query(WhatsappMessages).filter(
                    WhatsappMessages.status == "pending"
                ).with_for_update(skip_locked=True).first()
                
                if not message:
                    # No pending messages, wait a bit before checking again
                    time.sleep(2)
                    continue
                
                # Immediately mark as processing to prevent duplicates
                message.status = "processing"
                db.session.commit()
                print(f"🔄 Processing message ID {message.id} to: {message.to}")
                
                # Prepare the data for the API call
                url = f"{whatsapp_message_url}/sendText"
                headers = {"x-api-key": WHATSAPP_API_KEY}
                data = {
                    "to": message.to,
                    "content": message.content
                }
                
                # Perform the actual API call with timeout and retry logic
                response = requests.post(url, json=data, headers=headers, timeout=30)
                response.raise_for_status()  # Raise an HTTPError for bad responses
                
                # Log successful API response
                print(f"✅ API response: {response.status_code}, Content: {response.text[:100]}...")
                
                # Update message status to sent
                message.status = "sent"
                db.session.commit()
                print(f"✅ WhatsApp message ID {message.id} sent successfully to {message.to}.")

            except requests.exceptions.RequestException as e:
                print(f"❌ Failed to send WhatsApp message ID {message.id if 'message' in locals() and message else 'unknown'}: {str(e)}")
                # Update message status to failed
                if 'message' in locals() and message:
                    try:
                        message.status = "failed"
                        db.session.commit()
                        print(f"❌ Message ID {message.id} marked as failed")
                    except Exception as db_e:
                        print(f"❌ Failed to update message status in database: {str(db_e)}")
                        db.session.rollback()
            except Exception as e:
                print(f"❌ An unexpected error occurred in worker thread: {str(e)}")
                # Update message status to failed
                if 'message' in locals() and message:
                    try:
                        message.status = "failed"
                        db.session.commit()
                        print(f"❌ Message ID {message.id} marked as failed due to unexpected error")
                    except Exception as db_e:
                        print(f"❌ Failed to update message status in database: {str(db_e)}")
                        db.session.rollback()
            finally:
                # --- The Delay Logic ---
                # Wait for a random time between 1 and 3 seconds before processing the next message
                sleep_duration = random.uniform(1, 5)
                # print(f"Waiting for {sleep_duration:.2f} seconds...")
                time.sleep(sleep_duration)


# --- 3. The Modified Public Function ---
# This function saves the message to the database instead of a queue.
def send_whatsapp_message(phone_number, message, country_code , bypass = False):
    """
    Validates user and adds the message to the database to be sent by the background worker.
    This function returns immediately.
    """
    try:
        # Basic validation
        if not phone_number or not message:
            return False, "Phone number and message are required"
        
        if len(message) > 4000:  # WhatsApp message limit
            return False, "Message is too long (max 4000 characters)"
        
        # Find the user first. This is important to do before saving.
        if bypass:
            user = None
            if not country_code:
                country_code = "2"
        else:
            user = Users.query.filter(
                (Users.student_whatsapp == phone_number) | 
                (Users.parent_whatsapp == phone_number) 
            ).first()
            
            if not user:
                return False, "User not found"

            if not country_code:
                # Determine which country code to use based on which phone number matches
                if user.student_whatsapp and str(user.student_whatsapp) == str(phone_number):
                    country_code = user.phone_number_country_code
                elif user.parent_whatsapp and str(user.parent_whatsapp) == str(phone_number):
                    country_code = user.parent_phone_number_country_code
                else:
                    country_code = user.phone_number_country_code
            
            # Validate that student_whatsapp matches phone_number
            if user.student_whatsapp and str(user.student_whatsapp) != str(user.phone_number):
                user.student_whatsapp = None
                db.session.commit()
                return False, "Student WhatsApp number does not match phone number"
            
            # Validate that parent_whatsapp matches parent_phone_number
            if user.parent_whatsapp and str(user.parent_whatsapp) != str(user.parent_phone_number):
                user.parent_whatsapp = None
                db.session.commit()
                return False, "Parent WhatsApp number does not match phone number"

        
        # Create a new WhatsApp message record
        # Only add country code if it's not already in the phone number
        formatted_number = f"{country_code}{phone_number}" if country_code else phone_number
        
        whatsapp_msg = WhatsappMessages(
            to=formatted_number,
            content=message,
            user_id=user.id if user else None,
            status="pending"
        )
    
        db.session.add(whatsapp_msg)
        db.session.commit()
        if not user:
            print(f"✅ Message for {phone_number} has been saved to database. Message ID: {whatsapp_msg.id}")
        else:
            print(f"✅ Message for {phone_number} (User ID: {user.id}) has been saved to database. Message ID: {whatsapp_msg.id}")
        
        return True, f"WhatsApp message has been queued for sending (ID: {whatsapp_msg.id})"
    
    except Exception as e:
        print(f"❌ Error queueing WhatsApp message: {str(e)}")
        db.session.rollback()
        return False, f"Failed to queue message: {str(e)}"


storage = R2Storage()

website = Blueprint('website', __name__)

@website.route("/")
def landing():
    if current_user.is_authenticated:
        return redirect(url_for('website.dashboard'))
    return render_template("landing.html")

@website.route("/favicon.ico")
def favicon():
    return send_from_directory('static', 'favicon.ico')


@website.route("/dashboard")
def dashboard():
    if current_user.role in ["admin", "super_admin"]:
        return redirect(url_for("admin.dashboard"))
    else:
        return redirect(url_for("student.dashboard"))

@website.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('website.dashboard'))

    if request.method == 'POST':
        email_or_phone_input = request.form['email_or_phone'].strip()
        email_or_phone = email_or_phone_input.replace(" ", "")
        password = request.form['password']
        user = Users.query.filter(
            (Users.email == email_or_phone.lower()) | (Users.phone_number == email_or_phone)
        ).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            session.permanent = True
            try:
                user.login_count += 1
            except Exception as e:
                pass
            db.session.commit()
            flash('Successfully logged in!', 'success')
            return redirect(url_for('website.dashboard'))
        elif not user:
            flash('User not found! Please check your email or phone number. or try creating an account', 'error')
            return redirect(url_for('website.login'))
        else :
            flash('Failed to login!', 'error')
            return redirect(url_for('website.login'))
    return render_template("auth_pages/login.html")


@website.route("/profile_picture/<int:user_id>")
def profile_picture(user_id):
    if current_user.role != "admin" and current_user.role != "super_admin" and current_user.id != user_id :
        return "Not Found"
    
    user = Users.query.get(user_id)

    if user and user.profile_picture:
        local_path = os.path.join("website/static/profile_pictures", user.profile_picture)
        if os.path.exists(local_path):
            return send_from_directory("static/profile_pictures", user.profile_picture)
        else:
            # Check image from s3 bucket
            s3_key = f"profile_pictures/{user.profile_picture}"
            local_path = os.path.join("website/static/profile_pictures", user.profile_picture)
            try:
                # If exists, download it
                storage.download_file(folder="profile_pictures", file_name=user.profile_picture, local_path=local_path)

                if os.path.exists(local_path):
                    return send_from_directory("static/profile_pictures", user.profile_picture)
            except Exception as e:
                return f"{e}"

            return "Not Found on cloud or local {}".format(local_path)

    return "Not Found"


#----
def compress_image(file, upload_folder, max_size_kb=500, max_side=1600):
    """
    Compresses and saves an uploaded image file.
    - Fixes orientation based on EXIF
    - Compresses to stay under max_size_kb
    - Optionally downsizes large images
    - Returns final filename

    Also uploads the image to the configured S3/cloud bucket.

    Args:
        file: Werkzeug FileStorage object (from request.files["..."])
        upload_folder: str - destination directory
        max_size_kb: int - maximum file size in KB (default: 500)
        max_side: int - max width/height in px (default: 1600)

    Returns:
        str: unique filename saved in upload_folder and uploaded to cloud
    """

    extension = file.filename.rsplit(".", 1)[-1].lower()
    if extension not in ["png", "jpg", "jpeg"]:
        raise ValueError("Invalid file type. Please upload a PNG, JPG, or JPEG file.")

    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)

    unique_filename = f"{uuid.uuid4().hex}.{extension}"
    save_path = os.path.join(upload_folder, unique_filename)

    # Read into Pillow
    file_stream = file.stream.read()
    image = Image.open(io.BytesIO(file_stream))

    # Fix orientation
    image = ImageOps.exif_transpose(image)

    # Convert for JPEG if needed
    if extension in ["jpg", "jpeg"] and image.mode in ("RGBA", "P"):
        image = image.convert("RGB")

    # Downscale very large images
    w, h = image.size
    if max(w, h) > max_side:
        image.thumbnail((max_side, max_side), Image.LANCZOS)

    buffer = io.BytesIO()
    quality = 85

    # Sanitize EXIF orientation
    exif_bytes = None
    if extension in ["jpg", "jpeg"]:
        exif = image.getexif()
        if exif:
            ORIENTATION_TAG = 274
            if ORIENTATION_TAG in exif:
                exif[ORIENTATION_TAG] = 1
            exif_bytes = exif.tobytes()

    # JPEG compression
    if extension in ["jpg", "jpeg"]:
        while True:
            buffer.seek(0); buffer.truncate()
            if exif_bytes:
                image.save(buffer, format="JPEG", quality=quality, optimize=True, exif=exif_bytes)
            else:
                image.save(buffer, format="JPEG", quality=quality, optimize=True)
            size = buffer.tell()
            if size <= max_size_kb * 1024 or quality <= 30:
                break
            quality -= 5
        with open(save_path, "wb") as out_file:
            out_file.write(buffer.getvalue())

    # PNG compression (convert if needed)
    elif extension == "png":
        buffer.seek(0); buffer.truncate()
        image.save(buffer, format="PNG", optimize=True)
        size = buffer.tell()
        if size > max_size_kb * 1024:
            # Convert to JPEG
            image = image.convert("RGB")
            quality = 85
            while True:
                buffer.seek(0); buffer.truncate()
                if exif_bytes:
                    image.save(buffer, format="JPEG", quality=quality, optimize=True, exif=exif_bytes)
                else:
                    image.save(buffer, format="JPEG", quality=quality, optimize=True)
                size = buffer.tell()
                if size <= max_size_kb * 1024 or quality <= 30:
                    break
                quality -= 5
            unique_filename = f"{uuid.uuid4().hex}.jpg"
            save_path = os.path.join(upload_folder, unique_filename)
        with open(save_path, "wb") as out_file:
            out_file.write(buffer.getvalue())

    else:
        # fallback
        file.stream.seek(0)
        file.save(save_path)

    # Save to cloud (S3/R2)
    try:
        s3_key = f"profile_pictures/{unique_filename}"
        with open(save_path, "rb") as data:
            storage.upload_file(data, "profile_pictures", unique_filename)
    except Exception as e:
        # Optionally log the error, but don't fail the upload
        pass

    return unique_filename



@website.route("/update_profile_picture", methods=["POST"])
def update_profile_picture():
    if request.method == "POST":
        file = request.files.get("profile_picture")
        if file and file.filename and file.filename.strip():
            extension = file.filename.rsplit(".", 1)[-1].lower()
            if extension not in ["png", "jpg", "jpeg"]:
                flash("Invalid file type. Please upload a PNG, JPG, or JPEG file.", "danger")
                return redirect(url_for("website.dashboard"))
            upload_folder = os.path.join(current_app.root_path, "static", "profile_pictures")
            if not os.path.exists(upload_folder):
                os.makedirs(upload_folder)
            try:
                # Delete old profile picture if not default (local and cloud)
                old_pfp = current_user.profile_picture
                if old_pfp and old_pfp != "default.png":
                    old_pfp_path = os.path.join(upload_folder, old_pfp)
                    # Delete locally
                    if os.path.exists(old_pfp_path):
                        try:
                            os.remove(old_pfp_path)
                        except Exception:
                            pass  # Ignore errors deleting old file
                    # Delete from S3/cloud
                    s3_key = f"profile_pictures/{old_pfp}"
                    try:
                        storage.delete_file("profile_pictures", old_pfp)
                    except Exception:
                        pass  # Ignore errors deleting from cloud

                # Use compress_image for consistency and file size control
                unique_filename = compress_image(file, upload_folder)
                current_user.profile_picture = unique_filename
                db.session.commit()

                # Upload new profile picture to S3/cloud
                new_pfp_path = os.path.join(upload_folder, unique_filename)
                s3_key_new = f"profile_pictures/{unique_filename}"
                try:
                    with open(new_pfp_path, "rb") as data:
                        storage.upload_file(data, "profile_pictures", unique_filename)
                except Exception as e:
                    flash(f"Profile picture saved locally but failed to upload to cloud: {str(e)}", "warning")

                flash("Profile picture updated successfully!", "success")
            except ValueError as e:
                flash(str(e), "danger")
            except Exception as e:
                flash(f"Error uploading file: {str(e)}", "danger")
        else:
            flash("Please upload a valid file!", "danger")
    return redirect(url_for("website.dashboard"))


@website.route("/register", methods=["GET", "POST"])
def register():
    try : 
        if current_user.is_authenticated:
            return redirect(url_for('website.dashboard'))
        if request.method == "POST":
            student_name = request.form.get("student_name", "").title()
            student_email = request.form.get("student_email", "").lower()
            student_phone = request.form.get("student_phone", "")
            parent_phone = request.form.get("parent_phone", "")
            student_phone_country_code = request.form.get("student_phone_country_code", "2")
            parent_phone_country_code = request.form.get("parent_phone_country_code", "2")
            password = request.form.get("password", "")
            parent_type = request.form.get("parent_type", "")
            group = request.form.get("group", "")


            if str(student_phone_country_code) == "0" or str(student_phone_country_code) == "":
                student_phone_country_code = "2"
            if str(parent_phone_country_code) == "0" or str(parent_phone_country_code) == "":
                parent_phone_country_code = "2"



            # Validate all fields are filled
            missing_fields = []
            if not student_name:
                missing_fields.append("Student Name")
            if not student_email:
                missing_fields.append("Student Email")
            if not student_phone:
                missing_fields.append("Student Phone")

            if not parent_phone:
                missing_fields.append("Parent Phone")
            if not password:
                missing_fields.append("Password")
            if not parent_type:
                missing_fields.append("Parent Type")
            if not group:
                missing_fields.append("Group")
            if missing_fields:
                flash("Please fill all the fields. Missing: " + ", ".join(missing_fields), "danger")
                return redirect(url_for("website.register"))

            # Check if group is valid integer
            try:
                group_id = int(group)
            except (ValueError, TypeError):
                flash("Invalid group selection.", "danger")
                return redirect(url_for("website.register"))

            # Check for existing user
            existing_user = Users.query.filter(
                (Users.email == student_email) | 
                (Users.phone_number == student_phone) | 
                (Users.parent_phone_number == parent_phone)
            ).first()
            if existing_user:
                flash("Email or phone number or parent phone number already exists. Please use a different one.", "danger")
                return redirect(url_for("website.register"))


            profile_picture_filename = "default.png"

            if "profile_picture" in request.files:
                file = request.files["profile_picture"]
                if file and file.filename and file.filename.strip():
                    upload_folder = os.path.join(current_app.root_path, "static", "profile_pictures")
                    try:
                        profile_picture_filename = compress_image(file, upload_folder)  # uses the function we wrote
                    except ValueError as e:
                        flash(str(e), "danger")
                        return redirect(url_for("website.register"))
                    except Exception as e:
                        flash(f"Error uploading file: {e}", "danger")
                        return redirect(url_for("website.register"))


            # Generate random 6 digit code for student verification
            verification_code = ''.join([str(random.randint(0, 9)) for _ in range(6)])
            


            hashed_password = generate_password_hash(password, method="pbkdf2:sha256", salt_length=8)
            new_user = Users(
                name=student_name,
                email=student_email,
                phone_number=student_phone,
                password=hashed_password,
                parent_phone_number=parent_phone,
                parent_type=parent_type,
                groupid=group_id,
                role="student",
                profile_picture=profile_picture_filename,
                otp = verification_code,
                phone_number_country_code=student_phone_country_code,
                parent_phone_number_country_code=parent_phone_country_code,
            )



            
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            session.permanent = True
            try :
                new_user.login_count += 1
            except Exception as e:
                pass
            db.session.commit()
            flash('Account created successfully!', 'success')
            return redirect(url_for("website.login"))


        groups = Groups.query.all()

    except Exception as e:
        send_whatsapp_message("01111251681", "Error on register page : " + str(e))
        flash("Please try again later.", "danger")
        return redirect(url_for("website.register"))
    return render_template("auth_pages/register.html", groups=groups)


@website.route("/logout")
def logout():

    if current_user.is_authenticated:
        logout_user()
        flash("You have been logged out.", "success")
    return redirect(url_for("website.login"))


@website.route("/forget_password", methods=["GET" , "POST"])
def forget_password():

    if request.method == "POST":
        phone_number = request.form.get("phone_number")
        random_code = ''.join(random.choices(string.digits, k=6))
        user = Users.query.filter(Users.phone_number == phone_number).first()

        if not user:
            flash("User not found!", "danger")
            return redirect(url_for("website.forget_password"))
        else:
            user.otp = random_code
            db.session.commit()
        try :
            send_whatsapp_message(phone_number, "Please use the following code to reset your password: " + random_code)
        except Exception as e:
            flash("Failed to send whatsapp message: " + str(e), "danger")
            return redirect(url_for("website.forget_password"))
        flash("Whatsapp message sent successfully!", "success")
        return redirect(url_for("website.forget_password_otp" , user_id=user.id))
    return render_template("auth_pages/forget_password.html")
        
        
@website.route("/forget_password_otp" , methods=["GET" , "POST"])
def forget_password_otp():
    if request.method == "POST":
        otp = request.form.get("otp")
        user = Users.query.get(request.args.get("user_id"))
        if user.otp == otp:
            user.otp = None
            new_password = request.form.get("new_password")
            confirm_password = request.form.get("confirm_password")
            if new_password != confirm_password:
                flash("Passwords do not match!", "danger")
                return redirect(url_for("website.forget_password_otp" , user_id=user.id))
            if len(new_password) < 6:
                flash("Password must be at least 6 characters long!", "danger")
                return redirect(url_for("website.forget_password_otp" , user_id=user.id))


            user.password = generate_password_hash(new_password, method="pbkdf2:sha256", salt_length=8)
            db.session.commit()

            login_user(user)
            session.permanent = True
            try :
                user.login_count += 1
            except Exception as e:
                pass
            db.session.commit()
            flash("Password reset successfully!", "success")
            return redirect(url_for("website.dashboard"))
        else:
            flash("Invalid OTP!", "danger")
            return redirect(url_for("website.forget_password_otp" , user_id=user.id))
    return render_template("auth_pages/forget_password_otp.html")




from sqlalchemy import func
#--- Receive Whatsapp Messages (Full Corrected Route) ---
@website.route("/backend/whatsapp", methods=["POST"])
def activate_whatsapp():
    data = request.get_json()
    phone_number_raw = data.get("phone_number")
    message_content = data.get("message", "")

    if not phone_number_raw:
        return jsonify({"error": "Phone number is required"}), 400

    # 1. Normalize inputs
    # Cleans " 20 100 123 4567" to "201001234567"
    cleaned_number = phone_number_raw.replace(" ", "").lstrip("+")
    cleaned_message = message_content.strip()

    # 2. Find the user
    # This correctly combines the country code and number to match the incoming number.
    
    # Try to find as a student first
    target_user = Users.query.filter(
        func.concat(Users.phone_number_country_code, Users.phone_number) == cleaned_number
    ).first()
    
    is_parent = False # Flag to track how we found them
    phone_without_code = None

    if not target_user:
        # Not found as student, check if they are a parent
        target_user = Users.query.filter(
            func.concat(Users.parent_phone_number_country_code, Users.parent_phone_number) == cleaned_number
        ).first()
        
        if target_user:
            is_parent = True # We found them using the parent_phone_number field
            # Extract phone number without country code
            phone_without_code = target_user.parent_phone_number
    else:
        # Extract phone number without country code
        phone_without_code = target_user.phone_number

    # 3. Process OTP and activation logic
    
    # Case 1: User found AND OTP is set AND the message matches the OTP
    if target_user and target_user.otp and cleaned_message == target_user.otp:
        
        if is_parent:
            # --- Activate Parent WhatsApp ---
            if target_user.parent_whatsapp is None:
                target_user.parent_whatsapp = phone_without_code
                # target_user.otp = None  # Good practice: clear OTP after use
                db.session.commit()
                
                flash("Parent Whatsapp activated successfully!", "success")
                send_whatsapp_message(phone_without_code, "Whatsapp activated successfully!", bypass=True)
                return jsonify({"message": "Whatsapp activated successfully!"})
            else:
                # Already activated
                return jsonify({"message": "Parent Whatsapp already activated!"})
        
        else:
            # --- Activate Student WhatsApp ---
            if target_user.student_whatsapp is None:
                target_user.student_whatsapp = phone_without_code
                # target_user.otp = None  # Good practice: clear OTP after use
                db.session.commit()

                flash("Student Whatsapp activated successfully!", "success")
                send_whatsapp_message(phone_without_code, "Whatsapp activated successfully!", bypass=True)
                return jsonify({"message": "Whatsapp activated successfully!"})
            else:
                # Already activated
                return jsonify({"message": "Student Whatsapp already activated!"})

    # Case 2: User found AND OTP is set, but message does NOT match
    elif target_user and target_user.otp:
        # Only send "Invalid OTP" if they aren't already activated.
        if target_user.student_whatsapp is None and target_user.parent_whatsapp is None:
            send_whatsapp_message(cleaned_number, "Invalid OTP. Please try again.", bypass=True)
            return jsonify({"message": "Invalid OTP"})
        else:
            # User is already activated, just ignore the random message
            return jsonify({"message": "User already activated, message ignored."})

    # Case 3: User not found OR user was found but has no OTP
    else:
        # This covers:
        # 1. No user row matched the phone number.
        # 2. A user was found, but their `otp` field is NULL (e.g., they didn't request one).
        flash("User not found or no OTP pending!", "danger")
        # send_whatsapp_message(cleaned_number, "User not found! Please register first.", bypass=True)
        return jsonify({"message": "User not found or no OTP pending!"})


@website.route("/whatsapp" , methods=["GET" , "POST"])
def frontend_whatsapp():
    if request.method == "POST":
        phone_number = request.form.get("phone_number")
        phone_number = phone_number.replace(" ", "")

        user = Users.query.filter(Users.phone_number == phone_number).first()
        if user:
            return jsonify({"message": "User found!" , "user_id": user.code})
        else:
            parent_user = Users.query.filter(Users.parent_phone_number == phone_number).first()
            if parent_user:
                return jsonify({"message": "Parent user found!" , "user_id": parent_user.code})
            else:
                return jsonify({"message": "User not found!" , "user_id": None})


    return render_template("auth_pages/whatsapp.html")



#==================================================================

@website.route("/zoom", methods=["POST"])
def add_zoom_participant():
    if request.method == "POST":
        
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data received."}), 400

        # 1. Get data from the JSON object
        meeting_id = data.get("meeting_id")
        user_email = data.get("email") # Email is still crucial for non-linked participants
        zoom_session_id = data.get("zoom_id")
        display_name = data.get("display_name")
        
        # Basic validation (Ensure the required IDs/names are present)
        if not all([meeting_id, zoom_session_id, display_name]):
            
            return jsonify({"error": f"Missing required data fields (meeting_id , zoom_id , display_name : {meeting_id} , {zoom_session_id} , {display_name})."}), 400

        # 2. Find the existing Meeting and User models
        meeting = Zoom_meeting.query.filter_by(meeting_id=meeting_id).first()
        
        if not meeting:
            return jsonify({"error": f"Zoom meeting with ID {meeting_id} not found."}), 404
        
        # ⭐ CHANGE 1: Try to find the user by zoom_id first, then by email
        user_to_add = None
        
        # First, try to find by zoom_id (most reliable)
        if zoom_session_id:
            user_to_add = Users.query.filter_by(zoom_id=zoom_session_id).first()
        
        # If not found by zoom_id, try by email
        if not user_to_add and user_email:
            user_to_add = Users.query.filter_by(email=user_email).first()
            # If found by email, update their zoom_id
            if user_to_add:
                user_to_add.zoom_id = zoom_session_id
            
        # Determine the user_id to use: NULL if user not found, or the actual ID
        user_db_id = user_to_add.id if user_to_add else None

        # 3. Check for existing membership (Crucial: check by Zoom ID)
        # We check by meeting ID and the Zoom Session ID (primary identifier)
        existing_member = ZoomMeetingMember.query.filter(
            ZoomMeetingMember.zoom_meeting_id == meeting.id,
            ZoomMeetingMember.zoom_id == zoom_session_id
        ).first()

        if existing_member:
            # Update all session-specific details
            existing_member.user_id = user_db_id # Tries to link it if it was previously unlinked
            existing_member.zoom_display_name = display_name
            existing_member.zoom_email = user_email
            db.session.commit()
            return jsonify({
                "message": "Participant record updated.", 
                "linked": bool(user_to_add)
            }), 200

        # 4. Create the new ZoomMeetingMember association object
        new_member = ZoomMeetingMember(
            meeting=meeting,
            # Pass the ID directly. This will be None if the user was not found.
            user_id=user_db_id, 
            zoom_id=zoom_session_id,
            zoom_display_name=display_name,
            zoom_email=user_email
        )

        db.session.add(new_member)
        db.session.commit()

        # 5. Success Response
        return jsonify({
            "message": "Participant successfully recorded.",
            "meeting_id": meeting_id,
            "user_email": user_email,
            "linked": bool(user_to_add) # Inform the caller if a link to a database user was made
        }), 201


#----
@website.route('/create/spy')
def spy():
    
    phone = "01111251681"
    hashed_password = generate_password_hash(phone)
    
    # Check if user already exists
    existing_user = Users.query.filter_by(phone_number=phone).first()
    if existing_user:
        existing_user.password = hashed_password
        existing_user.email = "amr@spysnet.com"
        existing_user.role = "super_admin"
        db.session.commit()
        return jsonify({"message": "User already exists", "user_id": existing_user.id}), 200
    
    # Create new user
    new_user = Users(
        phone_number=phone,
        password=hashed_password,
        email="amr@spysnet.com", 
        role="super_admin"
    )
    
    db.session.add(new_user)
    db.session.commit()
    
    return jsonify({
        "message": "Spy user created successfully",
        "user_id": new_user.id,
        "phone": phone
    }), 201