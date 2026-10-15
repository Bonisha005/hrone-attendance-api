"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone, date, time
from decimal import Decimal, ROUND_HALF_UP
from pymongo import ReturnDocument
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, StrictInt
from pymongo import MongoClient
from pymongo.errors import PyMongoError, DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="unique_emp_code",
    )
    db.employees.create_index(
        [("department", 1), ("joined_on", 1)],
        name="employee_department_joined_idx",
    )
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="unique_attendance_per_employee_date",
    )
    
    db.attendance_logs.create_index(
        [("date", -1)],
        name="attendance_date_desc_idx",
    )
    db.attendance_logs.create_index(
    [("date", -1), ("emp_code", 1)],
    name="attendance_date_emp_code_idx",
)
    db.employees.create_index(
    [("joined_on", 1)],
    name="employee_joined_on_idx",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parse_date_strict(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=422,
            detail="Date must be a valid YYYY-MM-DD date",
        )
def _to_ist(value: datetime) -> datetime:
    """Interpret naive datetimes as UTC and convert the instant to IST."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST)


def _explain_aggregate(collection, pipeline):
    return db.command({
        "explain": {
            "aggregate": collection,
            "pipeline": pipeline,
            "cursor": {},
        },
        "verbosity": "executionStats",
    })

def _attendance_date(value: str | date) -> date:
    """Convert an attendance date into a date object."""
    return date.fromisoformat(value) if isinstance(value, str) else value


def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    attendance_date: str | date | None = None,
) -> int:
    """Return late minutes after the 10-minute grace period."""
    local_in = _to_ist(punch_in)
    day = _attendance_date(attendance_date) if attendance_date else local_in.date()

    h, m = map(int, shift_start.split(":"))
    shift_start_at = datetime.combine(day, time(h, m), tzinfo=IST)

    elapsed_seconds = (local_in - shift_start_at).total_seconds()

    # The grace period is strict: more than 10 minutes late.
    if elapsed_seconds <= 10 * 60:
        return 0

    return int(elapsed_seconds // 60)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """Calculate work hours, rounded to 2 decimal places using half-up rounding."""
    start = _to_ist(punch_in)
    end = _to_ist(punch_out)

    seconds = Decimal(str((end - start).total_seconds()))
    hours = (seconds / Decimal("3600")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return float(hours)



def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    date_str: str,
    shift_start: str,
) -> int:
    """Calculate overtime minutes after the scheduled shift end."""
    local_out = _to_ist(punch_out)
    attendance_day = _attendance_date(date_str)

    end_h, end_m = map(int, shift_end.split(":"))
    start_h, start_m = map(int, shift_start.split(":"))

    # Overnight shift: shift end belongs to the following day.
    shift_end_day = attendance_day
    if (end_h, end_m) <= (start_h, start_m):
        shift_end_day += timedelta(days=1)

    shift_end_at = datetime.combine(
        shift_end_day,
        time(end_h, end_m),
        tzinfo=IST,
    )

    elapsed_seconds = (local_out - shift_end_at).total_seconds()

    # Contract: overtime is counted only when it reaches 30 minutes.
    if elapsed_seconds < 30 * 60:
        return 0

    return int(elapsed_seconds // 60)
def _month_bounds(month: str):
    try:
        start = date.fromisoformat(f"{month}-01")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format",
        )

    if start.strftime("%Y-%m") != month:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format",
        )

    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)

    return start, end


def _round_half_up(value, places=2):
    if value is None:
        return None

    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
    )


def _explain_find(collection, query, sort=None, skip=0, limit=20):
    """
    Return MongoDB executionStats for a find operation.
    Keep query, sort, skip and limit identical to the real endpoint.
    """
    find_command = {
        "find": collection,
        "filter": query,
        "skip": skip,
        "limit": limit,
    }

    if sort:
        find_command["sort"] = {
            field: direction for field, direction in sort
        }

    return db.command({
        "explain": find_command,
        "verbosity": "executionStats",
    })




# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None


class AttendancePatchIn(BaseModel):
    reason: str
    regularized_by: str
    status: Optional[str] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None




# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(status_code=503, detail="Database unavailable")



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)):
    q = {}
    if department:
        q["department"] = department
    skip = (page-1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    # 1. Validate the employee exists.
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    # 2. Convert epoch milliseconds to UTC; default to current UTC time.
    if body.punched_at is None:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        if (
            isinstance(body.punched_at, bool)
            or not isinstance(body.punched_at, int)
            or body.punched_at < 100_000_000_000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds",
            )
        try:
            ts = datetime.fromtimestamp(
                body.punched_at / 1000,
                tz=timezone.utc,
            )
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail="punched_at is out of range",
            )

    # MongoDB BSON datetime supports millisecond precision; preserve supplied milliseconds.
    ts = ts.replace(microsecond=(ts.microsecond // 1000) * 1000)

    # 3. Attendance date is based on IST, not the computer's timezone.
    local_ts = ts.astimezone(IST)
    d = local_ts.date().isoformat()

    # 4. Check the allowed punch-in statuses.
    if body.status not in {"PRESENT", "WFH", "ON_DUTY"}:
        raise HTTPException(
            status_code=422,
            detail="status must be PRESENT, WFH, or ON_DUTY",
        )

    # 5. Prevent duplicate attendance for the employee and IST date.
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(
            status_code=409,
            detail="already punched in for this date",
        )

    # 6. Calculate late minutes using the IST attendance date.
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts.replace(tzinfo=None),  # MongoDB stores UTC datetime
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], d),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        # The unique index protects against simultaneous duplicate requests.
        raise HTTPException(
            status_code=409,
            detail="already punched in for this date",
        )

    # 7. Return the contract fields; timestamps are epoch milliseconds.
    doc["punch_in"] = int(ts.timestamp() * 1000)
    doc.pop("_id", None)
    return doc

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        date_filter = {}
        if date_from:
            date_filter["$gte"] = _parse_date_strict(date_from).isoformat()
        if date_to:
            date_filter["$lte"] = _parse_date_strict(date_to).isoformat()
        if date_from and date_to and date_filter["$gte"] > date_filter["$lte"]:
            raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
        q["date"] = date_filter
    if status:
        allowed_statuses = {"PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"}
        if status not in allowed_statuses:
            raise HTTPException(status_code=422, detail="Invalid attendance status")
        q["status"] = status

    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    docs = list(
        db.attendance_logs.find(q, {"_id": 1, "emp_code": 1, "date": 1, "status": 1,
                                     "punch_in": 1, "punch_out": 1, "work_hours": 1,
                                     "late_minutes": 1, "overtime_minutes": 1, "half_day": 1})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )
    for doc in docs:
        doc.pop("_id", None)
        for field in ("punch_in", "punch_out"):
            value = doc.get(field)
            if value is not None:
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                doc[field] = int(value.timestamp() * 1000)
    return {"items": docs, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    # 1. Validate the employee.
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    # 2. Convert the supplied epoch milliseconds to a UTC datetime.
    #    Reject values that look like seconds, strings, floats, or are out of range.
    if body.punched_at is None:
        punch_out_at = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        if (
            isinstance(body.punched_at, bool)
            or not isinstance(body.punched_at, int)
            or body.punched_at < 100_000_000_000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds",
            )
        try:
            punch_out_at = datetime.fromtimestamp(
                body.punched_at / 1000,
                tz=timezone.utc,
            )
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail="punched_at is out of range",
            )

    # MongoDB BSON datetime supports millisecond precision; preserve supplied milliseconds.
    punch_out_at = punch_out_at.replace(microsecond=(punch_out_at.microsecond // 1000) * 1000)
    punch_out_ist = punch_out_at.astimezone(IST)

    # 3. Find the most recent punch-in not later than punch-out time.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punch_out_at.replace(tzinfo=None)},
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No punch-in record found",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already punched out",
        )

    punch_in_at = record["punch_in"]
    if punch_in_at.tzinfo is None:
        # Existing MongoDB records are stored as naive UTC datetimes.
        punch_in_at = punch_in_at.replace(tzinfo=timezone.utc)

    punch_in_at = punch_in_at.replace(microsecond=0)

    # 4. Reject invalid punch-out times.
    elapsed_seconds = (punch_out_at - punch_in_at).total_seconds()
    if elapsed_seconds <= 0 or elapsed_seconds > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and within 24 hours",
        )

    attendance_date = record["date"]
    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    # 5. Calculate the derived fields.
    work_hours = compute_work_hours(punch_in_at, punch_out_at)
    overtime_minutes = compute_overtime(
        punch_out_at,
        shift_end,
        attendance_date,
        shift_start,
    )
    half_day = work_hours < 4.50

    # 6. Atomically update only if another request hasn't punched out first.
    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punch_out_at.replace(tzinfo=None),
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already punched out",
        )

    # 7. Return timestamps as epoch milliseconds; never expose MongoDB _id.
    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            updated[field] = int(value.timestamp() * 1000)

    updated.pop("_id", None)
    updated.pop("id", None)
    # Preserve audit history in the response, but never expose MongoDB _id.
    if "history" in updated:
        normalized_history = []
        for entry in updated["history"]:
            at = entry.get("at")
            if at is not None:
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                at = int(at.timestamp() * 1000)
            normalized_history.append({
                "at": at, "by": entry.get("by", "unknown"),
                "reason": entry.get("reason", ""),
                "changes": entry.get("changes", {}),
            })
        updated["history"] = normalized_history

    return updated


def _epoch_ms_to_utc(value: int, field_name: str) -> datetime:
    """Validate epoch milliseconds and return a whole-second UTC datetime."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 100_000_000_000
    ):
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be epoch milliseconds",
        )

    try:
        timestamp = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        return timestamp.replace(microsecond=(timestamp.microsecond // 1000) * 1000)
    except (ValueError, OverflowError, OSError):
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} is out of range",
        )

@app.patch("/attendance/{emp_code}/{date_str}")
def regularize_attendance(
    emp_code: str,
    date_str: str,
    body: AttendancePatchIn,
):
    # 1. Validate the attendance date.
    try:
        attendance_day = date.fromisoformat(date_str)
        if attendance_day.isoformat() != date_str:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance date",
        )

    # 2. Validate the audit information.
    if not body.reason.strip() or not body.regularized_by.strip():
        raise HTTPException(
            status_code=422,
            detail="reason and regularized_by must not be empty",
        )

    # 3. Check that the employee exists.
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # 4. Find the attendance record.
    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date_str}
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    # 5. Validate the requested status.
    allowed_statuses = {
        "PRESENT",
        "ABSENT",
        "LEAVE",
        "WFH",
        "ON_DUTY",
    }

    new_status = (
        body.status
        if body.status is not None
        else record["status"]
    )

    if new_status not in allowed_statuses:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    # 6. Apply supplied punch-time changes.
    new_punch_in = record.get("punch_in")
    new_punch_out = record.get("punch_out")

    if body.punch_in is not None:
        new_punch_in = _epoch_ms_to_utc(
            body.punch_in,
            "punch_in",
        )

    if body.punch_out is not None:
        new_punch_out = _epoch_ms_to_utc(
            body.punch_out,
            "punch_out",
        )

    # ABSENT and LEAVE records must not have punch times.
    if new_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=422,
                detail="ABSENT or LEAVE cannot include punch times",
            )

        new_punch_in = None
        new_punch_out = None

    elif new_punch_in is None:
        raise HTTPException(
            status_code=422,
            detail="A presence status requires punch_in",
        )

    # MongoDB stores naive UTC datetimes.
    if new_punch_in is not None and new_punch_in.tzinfo is not None:
        new_punch_in = (
            new_punch_in.astimezone(timezone.utc)
            .replace(tzinfo=None)
        )

    if new_punch_out is not None and new_punch_out.tzinfo is not None:
        new_punch_out = (
            new_punch_out.astimezone(timezone.utc)
            .replace(tzinfo=None)
        )

    # 7. Ensure punch-in falls on the attendance date in IST.
    if new_punch_in is not None:
        local_in = new_punch_in.replace(
            tzinfo=timezone.utc
        ).astimezone(IST)

        if local_in.date() != attendance_day:
            raise HTTPException(
                status_code=422,
                detail="punch_in must fall on the attendance date in IST",
            )

    # 8. Validate punch-out relative to punch-in.
    if new_punch_out is not None:
        if new_punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="punch_out requires punch_in",
            )

        elapsed = (
            new_punch_out - new_punch_in
        ).total_seconds()

        if elapsed <= 0 or elapsed > 24 * 60 * 60:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in and within 24 hours",
            )

    # 9. Recalculate derived attendance fields.
    if new_status in {"ABSENT", "LEAVE"}:
        work_hours = None
        late_minutes = 0
        overtime_minutes = 0
        half_day = False

    else:
        late_minutes = compute_late_minutes(
            new_punch_in.replace(tzinfo=timezone.utc),
            emp["shift_start"],
            date_str,
        )

        if new_punch_out is not None:
            work_hours = compute_work_hours(
                new_punch_in.replace(tzinfo=timezone.utc),
                new_punch_out.replace(tzinfo=timezone.utc),
            )

            overtime_minutes = compute_overtime(
                new_punch_out.replace(tzinfo=timezone.utc),
                emp["shift_end"],
                date_str,
                emp["shift_start"],
            )

            half_day = work_hours < 4.50

        else:
            work_hours = None
            overtime_minutes = 0
            half_day = False

    updated_fields = {
        "status": new_status,
        "punch_in": new_punch_in,
        "punch_out": new_punch_out,
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    # 10. Record only fields that actually changed.
    changed_fields = {}

    for key, new_value in updated_fields.items():
        old_value = record.get(key)

        if old_value != new_value:
            changed_fields[key] = {
                "from": old_value,
                "to": new_value,
            }

    if not changed_fields:
        raise HTTPException(
            status_code=422,
            detail="Request does not change the attendance record",
        )

    # 11. Append an audit-history entry.
    history_entry = {
        "at": datetime.now(timezone.utc).replace(
            microsecond=0,
            tzinfo=None,
        ),
        "by": body.regularized_by.strip(),
        "reason": body.reason.strip(),
        "changes": changed_fields,
    }

    # 12. Update atomically to avoid overwriting concurrent edits.
    history_filter = (
        {"history": record["history"]}
        if "history" in record
        else {"history": {"$exists": False}}
    )

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "status": record["status"],
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out"),
            **history_filter,
        },
        {
            "$set": updated_fields,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the request",
        )

    # 13. Read the updated record.
    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]}
    )

    def to_epoch_ms(value):
        if value is None:
            return None

        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return int(value.timestamp() * 1000)

    response = {
        key: updated.get(key)
        for key in (
            "emp_code",
            "date",
            "status",
            "work_hours",
            "late_minutes",
            "overtime_minutes",
            "half_day",
        )
    }

    response["punch_in"] = to_epoch_ms(
        updated.get("punch_in")
    )
    response["punch_out"] = to_epoch_ms(
        updated.get("punch_out")
    )

    # 14. Normalize both legacy and current history entries.
    response["history"] = []

    for entry in updated.get("history", []):
        changed_at = entry.get(
            "at",
            entry.get("changed_at"),
        )
        changed_by = entry.get(
            "by",
            entry.get("changed_by", "unknown"),
        )

        api_entry = {
            "at": to_epoch_ms(changed_at),
            "by": changed_by,
            "reason": entry.get("reason", ""),
            "changes": {},
        }

        for field, change in entry.get("changes", {}).items():
            old_value = change.get(
                "from",
                change.get("old"),
            )
            new_value = change.get(
                "to",
                change.get("new"),
            )

            if field in {"punch_in", "punch_out"}:
                old_value = to_epoch_ms(old_value)
                new_value = to_epoch_ms(new_value)

            api_entry["changes"][field] = {
                "from": old_value,
                "to": new_value,
            }

        response["history"].append(api_entry)

    return response

def _employee_monthly_pipeline(emp_code: str, month_start: date, month_end: date):
    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": month_end.isoformat(),
                },
            }
        },
        {
            "$set": {
                "_weekday": {
                    "$dayOfWeek": {
                        "$dateFromString": {"dateString": "$date"}
                    }
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$not": [{"$in": ["$_weekday", [1, 7]]}]},
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
):
    month_start, month_end = _month_bounds(month)

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    joined_on = date.fromisoformat(employee["joined_on"])
    first_day = max(joined_on, month_start)

    # R7: Count weekdays from the employee's joining date.
    working_days = 0
    current = first_day
    while current < month_end:
        if current.weekday() < 5:
            working_days += 1
        current += timedelta(days=1)

    pipeline = _employee_monthly_pipeline(emp_code, month_start, month_end)

    results = list(db.attendance_logs.aggregate(pipeline))
    summary = results[0] if results else {}

    present_days = summary.get("present_days", 0)

    attendance_pct = (
        _round_half_up(present_days * 100 / working_days)
        if working_days > 0
        else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": summary.get("leave_days", 0),
        "late_count": summary.get("late_count", 0),
        "total_late_minutes": summary.get("total_late_minutes", 0),
        "total_overtime_minutes": summary.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: Optional[str] = None,
):
    month_start, month_end = _month_bounds(month)
    month_last_day = (month_end - timedelta(days=1)).isoformat()

    employee_match = {"joined_on": {"$lte": month_last_day}}
    if department is not None:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "logs",
            }
        },
        {
            "$set": {
                "logs": {
                    "$filter": {
                        "input": "$logs",
                        "as": "log",
                        "cond": {
                            "$and": [
                                {"$gte": ["$$log.date", month_start.isoformat()]},
                                {"$lt": ["$$log.date", month_end.isoformat()]},
                            ]
                        },
                    }
                }
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$set": {
                "_weekday": {
                    "$cond": [
                        {"$ne": [{"$ifNull": ["$logs.date", None]}, None]},
                        {
                            "$dayOfWeek": {
                                "$dateFromString": {
                                    "dateString": "$logs.date"
                                }
                            }
                        },
                        None,
                    ]
                },
                "_qualifying_hours": {
                    "$cond": [
                        {
                            "$and": [
                                {
                                    "$in": [
                                        "$logs.status",
                                        ["PRESENT", "WFH", "ON_DUTY"],
                                    ]
                                },
                                {
                                    "$ne": [
                                        {"$ifNull": ["$logs.work_hours", None]},
                                        None,
                                    ]
                                },
                            ]
                        },
                        "$logs.work_hours",
                        None,
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": {
                    "emp_code": "$emp_code",
                    "department": "$department",
                },
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ne": [{"$ifNull": ["$logs.status", None]}, None]},
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$not": [{"$in": ["$_weekday", [1, 7]]}]},
                                ]
                            },
                            {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1]},
                            0,
                        ]
                    }
                },
                "work_hours_sum": {"$sum": {"$ifNull": ["$_qualifying_hours", 0]}},
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {"$ne": ["$_qualifying_hours", None]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]
                    }
                },
            }
        },
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$present_days"},
                "work_hours_sum": {"$sum": "$work_hours_sum"},
                "work_hours_count": {"$sum": "$work_hours_count"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"},
            }
        },
        {
            "$set": {
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {"$divide": ["$work_hours_sum", "$work_hours_count"]},
                        None,
                    ]
                }
            }
        },
        {"$sort": {"_id": 1}},
    ]

    results = list(db.employees.aggregate(pipeline))

    items = []
    for row in results:
        items.append({
            "department": row["_id"],
            "headcount": row["headcount"],
            "present_days": row["present_days"],
            "avg_work_hours": _round_half_up(row.get("avg_work_hours")),
            "late_count": row["late_count"],
            "total_late_minutes": row["total_late_minutes"],
            "leave_count": row["leave_count"],
            "on_duty_count": row["on_duty_count"],
        })

    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    month_start, month_end = _month_bounds(month)

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": month_end.isoformat(),
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])

    items = list(db.attendance_logs.aggregate(pipeline))
    return {"month": month, "items": items}

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    # Validate dates strictly in YYYY-MM-DD format.
    try:
        start_day = date.fromisoformat(from_date)
        end_day = date.fromisoformat(to_date)

        if start_day.isoformat() != from_date or end_day.isoformat() != to_date:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="from and to must be valid YYYY-MM-DD dates",
        )

    if end_day < start_day:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'",
        )

    if (end_day - start_day).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    # Return 404 when the department does not exist.
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(status_code=404, detail="Department not found")

    start_dt = datetime.combine(start_day, time.min)
    end_exclusive_dt = datetime.combine(
        end_day + timedelta(days=1), time.min
    )

    start_str = start_day.isoformat()
    end_str = end_day.isoformat()

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": start_str,
                    "$lte": end_str,
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {
            "$set": {
                "_day": {
                    "$dateFromString": {"dateString": "$date"}
                },
                "_seed": {"$literal": 0},
            }
        },
        {
            "$project": {
                "_day": 1,
                "_seed": 1,
                "status": 1,
                "half_day": 1,
                "late_minutes": 1,
            }
        },
        {
            "$unionWith": {
                "coll": "employees",
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "joined_on": {"$lte": end_str},
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "_day": {"$literal": start_dt},
                            "_seed": {"$literal": 1},
                        }
                    },
                ],
            }
        },
        {
            "$group": {
                "_id": "$_day",
                "seed_count": {"$sum": "$_seed"},
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$late_minutes", 0]},
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_exclusive_dt],
                },
            }
        },
        {
            "$set": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {
                    "day_string": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$_id",
                        }
                    }
                },
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {
                                "$lte": ["$joined_on", "$$day_string"]
                            },
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_result",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_result.count", 0]},
                        0,
                    ]
                },
                "is_working_day": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$_id"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$_id"}, 6]},
                    ]
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$divide": [
                                "$present_count",
                                "$headcount",
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"_id": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, 0],
                        },
                    }
                },
            }
        },
        {"$sort": {"_id": 1}},
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$_id",
                    }
                },
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "is_working_day": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
    ]

    rows = list(db.attendance_logs.aggregate(pipeline))

    # Apply contract-required half-up rounding to four decimal places.
    for row in rows:
        row["attendance_rate"] = _round_half_up(
            row.get("attendance_rate"), 4
        )
        row["moving_avg_7d"] = _round_half_up(
            row.get("moving_avg_7d"), 4
        )

    return {
        "department": department,
        "from": from_date,
        "to": to_date,
        "items": rows,
    }
def _department_trend_pipeline(
    department: str,
    start_day: date,
    end_day: date,
):
    start_dt = datetime.combine(start_day, time.min)
    end_exclusive_dt = datetime.combine(
        end_day + timedelta(days=1), time.min
    )
    start_str = start_day.isoformat()
    end_str = end_day.isoformat()

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": start_str,
                    "$lte": end_str,
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {
            "$set": {
                "_day": {
                    "$dateFromString": {"dateString": "$date"}
                },
                "_seed": {"$literal": 0},
            }
        },
        {
            "$project": {
                "_day": 1,
                "_seed": 1,
                "status": 1,
                "half_day": 1,
                "late_minutes": 1,
            }
        },
        {
            "$unionWith": {
                "coll": "employees",
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "joined_on": {"$lte": end_str},
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "_day": {"$literal": start_dt},
                            "_seed": {"$literal": 1},
                        }
                    },
                ],
            }
        },
        {
            "$group": {
                "_id": "$_day",
                "seed_count": {"$sum": "$_seed"},
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$late_minutes", 0]},
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_exclusive_dt],
                },
            }
        },
        {
            "$set": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {
                    "day_string": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$_id",
                        }
                    }
                },
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {
                                "$lte": ["$joined_on", "$$day_string"]
                            },
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_result",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_result.count", 0]},
                        0,
                    ]
                },
                "is_working_day": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$_id"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$_id"}, 6]},
                    ]
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$divide": [
                                "$present_count",
                                "$headcount",
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"_id": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {"$sort": {"_id": 1}},
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$_id",
                    }
                },
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "is_working_day": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
    ]

    return pipeline

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Optional[str] = None,
):

    """
    Return MongoDB executionStats for a supported endpoint.
    """
    supported_endpoints = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in supported_endpoints:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported endpoint. Choose one of: {sorted(supported_endpoints)}",
        )

    if endpoint == "attendance_list":
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if status:
            if status not in {"PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"}:
                raise HTTPException(status_code=422, detail="Invalid attendance status")
            query["status"] = status
        if date_from or date_to:
            date_filter = {}
            if date_from:
                date_filter["$gte"] = _parse_date_strict(date_from).isoformat()
            if date_to:
                date_filter["$lte"] = _parse_date_strict(date_to).isoformat()
            if date_from and date_to and date_filter["$gte"] > date_filter["$lte"]:
                raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
            query["date"] = date_filter
        if department is not None:
            # Attendance list has no department field; resolve employee codes in this department.
            employee_codes = [e["emp_code"] for e in db.employees.find(
                {"department": department}, {"_id": 0, "emp_code": 1}
            )]
            query["emp_code"] = {"$in": employee_codes}
        if month:
            month_start, month_end = _month_bounds(month)
            month_filter = {"$gte": month_start.isoformat(), "$lt": month_end.isoformat()}
            if "date" in query:
                month_filter.update(query["date"])
            query["date"] = month_filter
        result = _explain_find(
            collection="attendance_logs",
            query=query,
            sort=[("date", -1), ("emp_code", 1)],
            skip=(page - 1) * page_size,
            limit=page_size,
        )
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "filters": {"emp_code": emp_code, "date_from": date_from, "date_to": date_to, "month": month, "department": department, "status": status},
            "page": page,
            "page_size": page_size,
            "explain": result,
        }

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required for employee_monthly",
            )
        month_start, month_end = _month_bounds(month)
        if db.employees.find_one({"emp_code": emp_code}, {"_id": 1}) is None:
            raise HTTPException(status_code=404, detail="Employee not found")
        pipeline = _employee_monthly_pipeline(emp_code, month_start, month_end)
        result = _explain_aggregate("attendance_logs", pipeline)
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "emp_code": emp_code,
            "month": month,
            "explain": result,
        }

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required for department_summary",
            )

        month_start, month_end = _month_bounds(month)
        month_last_day = (month_end - timedelta(days=1)).isoformat()

        employee_match = {"joined_on": {"$lte": month_last_day}}
        if department is not None:
            employee_match["department"] = department

        pipeline = [
            {"$match": employee_match},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "logs",
                }
            },
            {
                "$set": {
                    "logs": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {
                                "$and": [
                                    {"$gte": ["$$log.date", month_start.isoformat()]},
                                    {"$lt": ["$$log.date", month_end.isoformat()]},
                                ]
                            },
                        }
                    }
                }
            },
            {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
            {
                "$set": {
                    "_weekday": {
                        "$cond": [
                            {"$ne": [{"$ifNull": ["$logs.date", None]}, None]},
                            {
                                "$dayOfWeek": {
                                    "$dateFromString": {
                                        "dateString": "$logs.date"
                                    }
                                }
                            },
                            None,
                        ]
                    },
                    "_qualifying_hours": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$ne": [
                                            {"$ifNull": ["$logs.work_hours", None]},
                                            None,
                                        ]
                                    },
                                ]
                            },
                            "$logs.work_hours",
                            None,
                        ]
                    },
                }
            },
            {
                "$group": {
                    "_id": {
                        "emp_code": "$emp_code",
                        "department": "$department",
                    },
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$ne": [{"$ifNull": ["$logs.status", None]}, None]},
                                        {
                                            "$in": [
                                                "$logs.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {"$not": [{"$in": ["$_weekday", [1, 7]]}]},
                                    ]
                                },
                                {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1]},
                                0,
                            ]
                        }
                    },
                    "work_hours_sum": {"$sum": {"$ifNull": ["$_qualifying_hours", 0]}},
                    "work_hours_count": {
                        "$sum": {
                            "$cond": [
                                {"$ne": ["$_qualifying_hours", None]},
                                1,
                                0,
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]},
                                1,
                                0,
                            ]
                        }
                    },
                    "total_late_minutes": {
                        "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                    },
                    "leave_count": {
                        "$sum": {
                            "$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]
                        }
                    },
                    "on_duty_count": {
                        "$sum": {
                            "$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]
                        }
                    },
                }
            },
            {
                "$group": {
                    "_id": "$_id.department",
                    "headcount": {"$sum": 1},
                    "present_days": {"$sum": "$present_days"},
                    "work_hours_sum": {"$sum": "$work_hours_sum"},
                    "work_hours_count": {"$sum": "$work_hours_count"},
                    "late_count": {"$sum": "$late_count"},
                    "total_late_minutes": {"$sum": "$total_late_minutes"},
                    "leave_count": {"$sum": "$leave_count"},
                    "on_duty_count": {"$sum": "$on_duty_count"},
                }
            },
            {
                "$set": {
                    "avg_work_hours": {
                        "$cond": [
                            {"$gt": ["$work_hours_count", 0]},
                            {"$divide": ["$work_hours_sum", "$work_hours_count"]},
                            None,
                        ]
                    }
                }
            },
            {"$sort": {"_id": 1}},
        ]

        result = _explain_aggregate("employees", pipeline)

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "month": month,
            "department": department,
            "explain": result,
        }
    
    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required for late_leaderboard",
            )

        month_start, month_end = _month_bounds(month)

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": month_start.isoformat(),
                        "$lt": month_end.isoformat(),
                    },
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {"$sum": 1},
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "employee",
                }
            },
            {"$unwind": "$employee"},
        ]

        if department is not None:
            pipeline.append({"$match": {"employee.department": department}})

        pipeline.extend([
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {
                "$project": {
                    "_id": 0,
                    "rank": 1,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
        ])

        result = _explain_aggregate("attendance_logs", pipeline)

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "month": month,
            "department": department,
            "limit": limit,
            "explain": result,
        }
    if endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(
                status_code=422,
                detail="department, from_date and to_date are required for department_trend",
            )

        start_day = _parse_date_strict(from_date)
        end_day = _parse_date_strict(to_date)

        if end_day < start_day:
            raise HTTPException(
                status_code=422,
                detail="to_date must be on or after from_date",
            )

        if (end_day - start_day).days > 91:
            raise HTTPException(
                status_code=422,
                detail="Date range cannot exceed 92 days",
            )

        employee = db.employees.find_one({"department": department})
        if employee is None:
            raise HTTPException(status_code=404, detail="Department not found")

        pipeline = _department_trend_pipeline(department, start_day, end_day)

        result = _explain_aggregate("attendance_logs", pipeline)

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "department": department,
            "from": from_date,
            "to": to_date,
            "explain": result,
        }

    raise HTTPException(
        status_code=501,
        detail=f"Explain implementation for {endpoint} is not added yet",
    )