"""
Script to manually fix the zoom_meeting_members table
Run this with: python fix_zoom_table.py
"""
from website import create_app, db
from website.models import ZoomMeetingMember

app = create_app()

with app.app_context():
    print("Dropping zoom_meeting_members table...")
    ZoomMeetingMember.__table__.drop(db.engine, checkfirst=True)
    
    print("Creating zoom_meeting_members table with correct primary key...")
    ZoomMeetingMember.__table__.create(db.engine)
    
    print("✅ Done! The table now has composite primary key (zoom_meeting_id, zoom_id)")

