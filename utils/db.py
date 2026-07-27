import pymongo
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DATABASE_NAME = "hrms_db"

try:
    client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    client.server_info()  # Check if connection is successful
except (pymongo.errors.ServerSelectionTimeoutError, pymongo.errors.ConnectionFailure):
    print("Warning: Local MongoDB not found. Falling back to mongomock for demonstration.")
    import mongomock
    client = mongomock.MongoClient()

db = client[DATABASE_NAME]

# Collections
users = db["users"]
leaves = db["leaves"]
salaries = db["salaries"]
timetable = db["timetable"]
messages = db["messages"]
staff_conversations = db["staff_conversations"]
staff_messages = db["staff_messages"]
staff_socket_sessions = db["staff_socket_sessions"]

# New collections for class assignment feature
leave_class_allocations = db["leave_class_allocations"]  # Tracks class assignments for leaves
faculty_notifications = db["faculty_notifications"]  # Notifications for faculty about class assignments
timetable_history = db["timetable_history"]  # Stores original timetables before changes
leave_drafts = db["leave_drafts"]  # Stores unfinished leave applications
leave_types = db["leave_types"]  # Dynamic leave types management
hod_requests = db["hod_requests"]  # HOD permission requests for leaves
department_hods = db["department_hods"]  # Department-wise HOD assignments
permissions = db["permissions"]  # Dedicated collection for Permission Leave requests
broadcast_notifications = db["broadcast_notifications"]  # Global notifications with images
system_settings = db["system_settings"]  # Payroll SMTP and other admin settings
login_attempts = db["login_attempts"]  # Account security lockout tracking
password_resets = db["password_resets"]  # Password reset tokens tracking


def init_db():
    # Create unique index for username
    users.create_index("username", unique=True)
    login_attempts.create_index("username", unique=True)
    password_resets.create_index("token", unique=True)
    # Create default admin if not exists
    if not users.find_one({"role": "admin"}):
        from flask_bcrypt import generate_password_hash
        admin_data = {
            "username": "admin",
            "password": generate_password_hash("admin123").decode('utf-8'),
            "role": "admin",
            "name": "System Administrator",
            "email": "admin@college.edu"
        }
        users.insert_one(admin_data)
        print("Default admin created: admin / admin123")
    
    # Create default lecturer if not exists
    if not users.find_one({"role": "lecturer"}):
        from flask_bcrypt import generate_password_hash
        lecturer_data = {
            "username": "lecturer",
            "password": generate_password_hash("lect123").decode('utf-8'),
            "role": "lecturer",
            "name": "Dr. Rajesh Kumar",
            "email": "rajesh@college.edu",
            "department": "Computer Science"
        }
        users.insert_one(lecturer_data)
        print("Default lecturer created: lecturer / lect123")
    
    # Initialize default leave types if collection is empty
    if leave_types.count_documents({}) == 0:
        defaults = ["Casual Leave", "Medical Leave", "Earned Leave", "Normal Leave", "Short Leave", "Duty Leave", "Special Leave"]
        leave_types.insert_many([{"name": t} for t in defaults])
        print("Default leave types initialized.")
