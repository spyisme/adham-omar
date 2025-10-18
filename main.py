import logging, requests, json, os
from flask import request, redirect, url_for, flash, render_template
from website import db, create_app
from flask_login import current_user
from logging.handlers import RotatingFileHandler
import os 
from dotenv import load_dotenv
import threading

load_dotenv()

# Configure logging
logging.getLogger('werkzeug').setLevel(logging.ERROR)

def send_whatsapp_message_spy(phone_number, message , country_code = "2"):
    url = "https://whatsapp.spysnet.com/sendText"
    headers = {
        "x-api-key": "Whatsappsecretkeeeey2@1"
    }
    data = {

        "to": f"{country_code}{phone_number}",
        "content": message 
    
    }

    response = requests.post(url, json=data , headers = headers)

    if response.status_code != 200:
        return 'Failed to send whatsapp message'
    
    return 'Whatsapp message sent successfully'

# Initialize the app
app = create_app()


# Import and start the WhatsApp sender worker
from website.website import whatsapp_sender_worker

# Start the WhatsApp sender worker in a background thread
def start_whatsapp_worker():
    """Start the WhatsApp sender worker in a background thread."""
    worker_thread = threading.Thread(target=whatsapp_sender_worker, args=(app,), daemon=True)
    worker_thread.start()
    print("WhatsApp sender worker thread started successfully!")

# Start the worker when the app is created
start_whatsapp_worker()

# Load webhook URL from environment variable
ERROR_WEBHOOK = "https://discord.com/api/webhooks/1418899208328446033/h24AIXJmgMku9USPlgq9P26f_Fu3VWFf79IyfBkhJWdNNw6iYZ2T_SUPQWm4nDnuGBOA"

# Configure logging for production
if not app.debug:
    file_handler = RotatingFileHandler('python.log', maxBytes=1024 * 1024 * 100, backupCount=20)
    file_handler.setLevel(logging.ERROR)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    app.logger.addHandler(file_handler)


def discord_send(link , data):
    message = {'content': data}
    payload = json.dumps(message)
    headers = {'Content-Type': 'application/json'}
    requests.post( link , data=payload , headers=headers)


@app.errorhandler(404)
def page_not_found(error):
    if request.path.endswith('/'):
        return redirect(request.path[:-1])
    
    if not request.path.startswith('/static/'):
        path = request.path
        flash('Page not found !', 'danger')
    
    return render_template('used_pages/404.html'), 404

@app.errorhandler(500)
def internal_server_error(error):
    # Log the full error and traceback to the file first
    app.logger.error(error)

    # Get IP from Cloudflare if available, else X-Forwarded-For, else remote_addr
    user_ip = (
        request.headers.get('CF-Connecting-IP')
        or request.headers.get('X-Forwarded-For', request.remote_addr)
    )


    
    user_agent = request.headers.get('User-Agent', 'N/A')
    user_id = current_user.id if current_user.is_authenticated else 'Anonymous'
    user_name = current_user.name if current_user.is_authenticated else 'Anonymous'

    def get_most_recent_non_generic_error():
        try:
            with open('python.log', 'r') as log_file:
                lines = log_file.readlines()
                # Iterate backwards to find the most recent non-generic error
                for i in range(len(lines) - 1, -1, -1):
                    line = lines[i]
                    if "ERROR" in line:
                        message = " - ".join(line.split(" - ")[3:]).strip()
                        if message != "500 Internal Server Error: The server encountered an internal error and was unable to complete your request. Either the server is overloaded or there is an error in the application.":
                            # Collect traceback lines after this error
                            traceback_lines = []
                            j = i + 1
                            while j < len(lines) and "ERROR" not in lines[j]:
                                traceback_lines.append(lines[j])
                                j += 1
                            full_error = f"{message}\n{''.join(traceback_lines)}"
                            return f"```\n{full_error}\n```\n"
        except FileNotFoundError:
            return "```\nNo recent non-generic error found.\n```\n"
        return "```\nNo recent non-generic error found.\n```\n"

    discord_message = (
        f"<@709799648143081483> 🚨 **500 Internal Server Error!** 🚨\n\n"
        f"**User Info:**\n"
        f"- **Page:** `{request.path}`\n"
        f"- **User ID:** `{user_id}`\n"
        f"- **User Name:** `{user_name}`\n"
        f"- **IP Address:** `{user_ip}`\n"
        f"- **User Agent:** `{user_agent}`\n\n"
        f"**Error Details:**\n"
        f"\n{get_most_recent_non_generic_error()}\n\n"
        f"Check the server logs for the full traceback."
    )

    # Send the detailed message to your Discord webhook

    send_whatsapp_message_spy('01111251681' , f"An error on Sally webstie. on {request.path} page")

    discord_send(ERROR_WEBHOOK, discord_message)



    # Keep the original logic for super admins
    if current_user.is_authenticated and current_user.role == "super_admin":
        error_details = str(error)  # Pass the actual error to the template
        return render_template('used_pages/500.html', error=error_details), 500

    return render_template('used_pages/500.html', error="An unexpected error has occurred."), 500




@app.route('/errors')
def errors():
    if not current_user.is_authenticated or current_user.role != "super_admin":
        return redirect('/')
        
    errors = []
    try:
        with open('python.log', 'r') as log_file:
            for line in log_file:
                if "ERROR" in line:
                    # Parse the log line
                    timestamp = line.split(" - ")[0]
                    message = " - ".join(line.split(" - ")[3:]).strip()
                    # Skip generic 500 error messages
                    if message != "500 Internal Server Error: The server encountered an internal error and was unable to complete your request. Either the server is overloaded or there is an error in the application.":
                        errors.append({
                            'timestamp': timestamp,
                            'message': message,
                            'id': len(errors) + 1  # Add an ID for each error
                        })
    except FileNotFoundError:
        errors = []
    
    # Sort errors by timestamp, most recent first
    errors.sort(key=lambda x: x['timestamp'], reverse=True)
    return render_template('errors/errors.html', errors=errors)

# Error detail page for admin
@app.route('/error/<int:error_id>')
def error_detail(error_id):
    if not current_user.is_authenticated or current_user.role != "super_admin":
        return redirect('/')
        
    try:
        with open('python.log', 'r') as log_file:
            current_error = None
            traceback_lines = []
            error_count = 0
            
            for line in log_file:
                if "ERROR" in line and "Traceback" not in line:
                    # Skip generic 500 error messages
                    message = " - ".join(line.split(" - ")[3:]).strip()
                    if message != "500 Internal Server Error: The server encountered an internal error and was unable to complete your request. Either the server is overloaded or there is an error in the application.":
                        error_count += 1
                        if error_count == error_id:
                            timestamp = line.split(" - ")[0]
                            current_error = {
                                'timestamp': timestamp,
                                'message': message,
                                'id': error_id,
                                'role': line.split(" - ")[2]
                            }
                            # Reset traceback lines when we find our target error
                            traceback_lines = []
                        elif error_count > error_id:
                            # Stop collecting traceback when we hit the next error
                            break
                else:
                    # Only collect traceback lines after we've found our target error
                    if current_error:
                        traceback_lines.append(line)
                        
            if current_error:
                current_error['traceback'] = ''.join(traceback_lines) if traceback_lines else None
                return render_template('errors/error_detail.html', error=current_error)
                
    except FileNotFoundError:
        pass
        
    return redirect('/errors')

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True , host="0.0.0.0" , port=5000)
    # app.run(host="0.0.0.0" ,port=80 , debug=True)
