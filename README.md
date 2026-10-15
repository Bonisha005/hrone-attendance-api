**# Employee Attendance & Analytics API

A FastAPI and MongoDB based backend for employee management, attendance tracking, attendance regularization, and attendance analytics.

## Tech Stack

- Python
- FastAPI
- MongoDB
- PyMongo
- Uvicorn

## Local Setup

### 1. Create a virtual environment
python -m venv .venv

2. Activate the virtual environment
For Windows PowerShell:
.venv\Scripts\Activate.ps1

3. Install dependencies
pip install -r requirements.txt

4. Configure MongoDB
Set the required environment variables before starting the application:
$env:MONGO_URI="mongodb://localhost:27017"
$env:MONGO_DB="your_database_name"

MongoDB 6.0 or newer is required.
5. Start the application
uvicorn app.main:app --port 8000

The API will be available at:
http://127.0.0.1:8000

API Documentation
Swagger UI
http://127.0.0.1:8000/docs

Health Check
http://127.0.0.1:8000/health

Features
- Employee listing and filtering
- Attendance listing and filtering
- Employee attendance tracking
- Punch-in and punch-out
- Attendance regularization
- Attendance correction history
- Employee monthly attendance analytics
- Department attendance summary
- Late attendance leaderboard
- Department attendance trends
- MongoDB query-plan explanation
Implementation Notes
- All application code is contained in app/main.py.
- MongoDB indexes are created automatically when the application starts.
- Employee codes are uniquely indexed.
- Attendance records enforce one record per employee per attendance date.
- Attendance timestamps are handled as epoch milliseconds in the API.
- Calendar dates use the YYYY-MM-DD format.
- Stored attendance values are used for analytics.
- Analytics are implemented using MongoDB aggregation pipelines.
- Attendance regularization records the actual changes in the attendance history.
- The application validates employee existence and attendance status values.
- Pagination is supported for employee and attendance listing endpoints.
- The explain endpoint is available to review MongoDB query plans.
Repository Structure
.
├── app/
│   ├── __init__.py
│   └── main.py
├── requirements.txt
├── README.md
├── REVIEW.md
├── DECISIONS.md
└── .gitignore

Submission Files
The repository contains the required submission files:
- app/main.py - Application code
- requirements.txt - Python dependencies
- README.md - Setup and project information
- REVIEW.md - Defects identified and fixes
- DECISIONS.md - Implementation decisions and reasoning
Submission Notes
The repository does not contain:
- .env files
- Secrets or credentials
- Virtual environments
- Dockerfiles
- Database dumps
The application can be started on a clean clone after installing the dependencies:
pip install -r requirements.txt
uvicorn app.main:app --port 8000
