# Quick Start Guide - Google Drive Picker

## For System Administrators

### Initial Setup (One-Time)

1. **Get Google Cloud Credentials**:

   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a project or select existing one
   - Enable "Google Drive API" and "Google Picker API"
   - Create OAuth 2.0 Client ID (Web application)
   - Create API Key
   - Configure OAuth consent screen

2. **Update Configuration**:

   - Open `website/templates/admin/assignments/assignments.html`
   - Find these lines (around line 2050):
     ```javascript
     const GOOGLE_CLIENT_ID = "YOUR-CLIENT-ID...";
     const GOOGLE_API_KEY = "YOUR-API-KEY";
     const GOOGLE_APP_ID = "YOUR-PROJECT-NUMBER";
     ```
   - Replace with your actual credentials

3. **Repeat for Edit Page**:

   - Open `website/templates/admin/assignments/edit_assignment.html`
   - Update the same three constants (around line 795)

4. **Test**:
   - Restart your Flask application
   - Navigate to assignments page
   - Try creating an assignment with a Drive attachment

---

## For Assignment Creators

### How to Attach a Google Drive File

#### When Creating a New Assignment:

1. Click **"+ Create Assignment"** button
2. Fill in assignment details (title, description, deadline, etc.)
3. Scroll to **"Attachments"** section
4. Click **"+ Add Attachment"** button
5. A new attachment block appears with three options:

   - **📁 File Upload** - Upload from computer
   - **🔗 External Link** - Add any web URL
   - **📂 Google Drive** - Select from Drive ← Click this!

6. After clicking **📂 Google Drive**:

   - **First time only**: Google will ask for permission - click "Allow"
   - Google Drive file picker opens
   - Browse and select your file
   - Click "Select" in the picker

7. File details appear:

   - Attachment name auto-fills with file name
   - Green checkmark shows file selected
   - You can change the display name if needed

8. Add more attachments if needed (repeat steps 4-7)
9. Click **"Add Assignment"** to save

#### When Editing an Assignment:

1. Navigate to assignment and click **"Edit"**
2. Scroll to **"Existing Attachments"** section

   - View current attachments with type indicators
   - Delete unwanted attachments if needed

3. Scroll to **"Add New Attachments"**
4. Click **"+ Add Attachment"**
5. Select **📂 Google Drive** option
6. Follow steps 6-7 from above
7. Click **"Save Changes"**

---

## Common Scenarios

### Scenario 1: Sharing a Document from My Drive

```
1. Create/Edit assignment
2. Add attachment → Google Drive
3. Browse to your document
4. Select it
5. Name it (e.g., "Lecture Notes Chapter 5")
6. Save assignment
```

### Scenario 2: Multiple Attachments (Mixed Types)

```
Attachment 1: PDF from computer (File Upload)
Attachment 2: YouTube video (External Link)
Attachment 3: Google Slides presentation (Google Drive)
Attachment 4: Shared document (Google Drive)
```

### Scenario 3: Replacing an Attachment

```
1. Edit assignment
2. Find attachment in "Existing Attachments"
3. Click "Delete" on old attachment
4. Add new attachment via "Add New Attachments"
5. Save changes
```

---

## Tips & Best Practices

### ✅ DO:

- **Share files properly**: Ensure students can view the Drive file
  - Set sharing to "Anyone with the link can view"
  - Or share directly with student email addresses
- **Use descriptive names**: Change default name to something meaningful
- **Test access**: View the assignment as a student to verify links work
- **Organize Drive**: Keep assignment files in dedicated folders
- **Regular cleanup**: Remove outdated attachments when editing

### ❌ DON'T:

- Don't attach private files (students won't be able to access)
- Don't delete Drive files after attaching (link will break)
- Don't use file names with special characters
- Don't attach extremely large files (slow for students)

---

## Troubleshooting

### Problem: "Google Drive API is still loading"

**Solution**: Wait 2-3 seconds after page loads, then try again

### Problem: Authorization popup doesn't appear

**Solution**:

1. Check if popup was blocked (browser notification)
2. Disable popup blocker for your site
3. Try again

### Problem: "Could not retrieve file link"

**Solution**:

1. Verify file sharing settings in Drive
2. Ensure you have at least "view" access to the file
3. Try selecting a different file

### Problem: Selected file doesn't show

**Solution**:

1. Check browser console for errors (F12)
2. Verify Google API credentials are correct
3. Ensure Drive API is enabled in Cloud Console

### Problem: Students can't access Drive file

**Solution**:

1. Open the file in Google Drive
2. Click "Share" button
3. Change to "Anyone with the link can view"
4. Update assignment with new sharing settings

### Problem: Authorization expires frequently

**Solution**: Normal behavior - tokens expire after 1 hour. Re-authorize when prompted.

---

## Features at a Glance

| Feature                     | Description                                        |
| --------------------------- | -------------------------------------------------- |
| **One-Click Authorization** | Google OAuth - only needed once per session        |
| **Browse Entire Drive**     | Access all files and folders in your Drive         |
| **Auto-Name Fill**          | File name automatically fills attachment name      |
| **Type Indicators**         | Clear icons show attachment type (File/Link/Drive) |
| **Easy Management**         | Add, edit, or delete attachments anytime           |
| **Mixed Attachments**       | Combine files, links, and Drive items freely       |
| **Mobile Friendly**         | Works on tablets and phones                        |

---

## Student View

When students view the assignment, Drive attachments appear as:

- **Name**: The display name you set
- **Icon**: 📂 Google Drive indicator
- **Link**: Clickable to open file in new tab
- **Access**: Based on file's sharing settings

Students do NOT need to authorize - they just click and view (if permissions allow).

---

## Quick Reference Card

```
┌─────────────────────────────────────────┐
│  ATTACHING FROM GOOGLE DRIVE            │
├─────────────────────────────────────────┤
│  1. Click "+ Add Attachment"            │
│  2. Select "📂 Google Drive"            │
│  3. Authorize (first time only)         │
│  4. Browse and select file              │
│  5. Confirm selection                   │
│  6. Save assignment                     │
└─────────────────────────────────────────┘

ATTACHMENT TYPES:
  📁 File Upload   → From your computer
  🔗 External Link → Any web URL
  📂 Google Drive  → From your Drive

REMEMBER:
  ✓ Set file sharing to "Anyone with link"
  ✓ Use descriptive attachment names
  ✓ Test links before publishing
```

---

Need help? Check `GOOGLE_DRIVE_SETUP.md` for detailed setup instructions or `IMPLEMENTATION_SUMMARY.md` for technical details.
