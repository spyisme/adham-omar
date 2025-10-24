# Google Drive Picker Integration Setup

## Overview

This integration allows admins to attach Google Drive files directly when creating or editing assignments. The system uses Google's Picker API and OAuth 2.0 for secure access.

## Prerequisites

You need a Google Cloud Console project with the following APIs enabled:

1. Google Drive API
2. Google Picker API

## Setup Instructions

### 1. Google Cloud Console Configuration

#### A. Create/Configure OAuth 2.0 Client ID

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (or create a new one)
3. Navigate to **APIs & Services** > **Credentials**
4. Click **Create Credentials** > **OAuth client ID**
5. Choose **Web application** as the application type
6. Configure:
   - **Name**: "Assignment Drive Picker"
   - **Authorized JavaScript origins**: Add your domain(s)
     - `http://localhost:5000` (for development)
     - `https://yourdomain.com` (for production)
   - **Authorized redirect URIs**: Same as above
7. Click **Create** and save your **Client ID**

#### B. Create/Configure API Key

1. In the same **Credentials** page
2. Click **Create Credentials** > **API key**
3. (Optional but recommended) Click **Restrict Key**:
   - **Application restrictions**: HTTP referrers
   - Add your domains: `localhost:5000/*`, `yourdomain.com/*`
   - **API restrictions**: Restrict to:
     - Google Drive API
     - Google Picker API
4. Save your **API Key**

#### C. Enable Required APIs

1. Navigate to **APIs & Services** > **Library**
2. Search and enable:
   - **Google Drive API**
   - **Google Picker API**

### 2. Update Your Application

#### Update JavaScript Configuration

In both `assignments.html` and `edit_assignment.html`, update these constants:

```javascript
// Replace with your actual values
const GOOGLE_CLIENT_ID = "YOUR-CLIENT-ID.apps.googleusercontent.com";
const GOOGLE_API_KEY = "YOUR-API-KEY";
const GOOGLE_APP_ID = "YOUR-PROJECT-NUMBER"; // Just the numeric part of your project
```

**To find your Project Number (APP_ID):**

1. Go to Google Cloud Console
2. Click on **IAM & Admin** > **Settings**
3. Copy the **Project number**

### 3. OAuth Consent Screen Configuration

1. Navigate to **APIs & Services** > **OAuth consent screen**
2. Configure:
   - **App name**: Your app name
   - **User support email**: Your email
   - **Developer contact information**: Your email
3. Add scopes:
   - `https://www.googleapis.com/auth/drive.readonly`
4. Add test users (if in testing mode)
5. Save and continue

### 4. Testing the Integration

1. Start your Flask application
2. Navigate to the assignments page
3. Click "Create Assignment" or "Edit Assignment"
4. Add an attachment and select "📂 Google Drive"
5. Click "Select from Google Drive"
6. You'll be prompted to authorize (first time only)
7. After authorization, the Google Drive picker will open
8. Select a file and it will be attached to the assignment

## How It Works

### Frontend Flow

1. User clicks "📂 Google Drive" button on an attachment
2. If not authorized, Google OAuth consent screen appears
3. User authorizes the application
4. Access token is stored in memory (client-side)
5. Google Picker opens with user's Drive files
6. User selects a file
7. File metadata (name, link) is retrieved via Drive API
8. File link and token are stored in hidden form fields
9. On form submission, data is sent to backend

### Backend Processing

1. Backend receives attachment data including:
   - `name`: Display name for the attachment
   - `type`: "drive"
   - `url`: Google Drive file link (webViewLink)
   - `drive_token`: OAuth access token (optional, for future server-side access)
2. Data is stored in the assignment's `attachments` JSON field
3. Students can later click the link to view the file

### Data Structure

Attachments are stored as JSON in the database:

```json
[
  {
    "name": "Chapter 5 Notes",
    "type": "drive",
    "url": "https://drive.google.com/file/d/FILE_ID/view",
    "drive_token": "ya29.a0..." // Optional
  }
]
```

## Security Notes

1. **Access Token Storage**: Tokens are currently stored client-side during the session. They expire after ~1 hour.
2. **Token Backend Storage**: If you store `drive_token` in the database, ensure it's encrypted.
3. **Scope**: Uses `drive.readonly` scope - minimal permissions for reading files only.
4. **File Access**: Users must have permission to view the Drive file. Shared links work best.
5. **CORS**: Ensure your Flask app has proper CORS configuration if frontend/backend are on different domains.

## Troubleshooting

### "Authorization failed" error

- Verify your Client ID is correct
- Check that your domain is in "Authorized JavaScript origins"
- Clear browser cache and cookies

### "Google Drive API is still loading"

- Wait a few seconds after page load
- Check browser console for API loading errors
- Verify Google API scripts are loaded (check Network tab)

### Picker doesn't open

- Verify API Key is correct and not restricted too heavily
- Check that Picker API is enabled in Cloud Console
- Look for JavaScript errors in browser console

### "Could not retrieve file link"

- Ensure the file has appropriate sharing settings
- The user must have at least "view" access to the file
- Some file types may not have webViewLink

## File Structure

### Modified Files

1. `website/templates/admin/assignments/assignments.html`

   - Added Google API scripts
   - Added Drive picker styles
   - Added Drive button to attachment system
   - Added Drive picker JavaScript logic

2. `website/templates/admin/assignments/edit_assignment.html`

   - Same changes as assignments.html
   - Updated attachment display for existing Drive attachments

3. `website/admin.py`
   - Updated assignment creation route to handle Drive attachments
   - Updated assignment editing route to handle Drive attachments
   - Added support for `drive_token` storage

## Future Enhancements

1. **Server-side Token Storage**: Encrypt and store tokens server-side for batch operations
2. **File Caching**: Cache Drive file metadata to reduce API calls
3. **Batch Selection**: Allow selecting multiple files at once
4. **Direct Download**: Add option to download Drive files to local storage
5. **Permissions Check**: Verify file permissions before attaching
6. **Token Refresh**: Implement automatic token refresh for long-lived sessions

## Support

For issues related to:

- **Google APIs**: Check [Google Picker API documentation](https://developers.google.com/drive/picker)
- **OAuth 2.0**: See [Google OAuth 2.0 guide](https://developers.google.com/identity/protocols/oauth2)
- **Drive API**: Visit [Google Drive API reference](https://developers.google.com/drive/api/v3/reference)
