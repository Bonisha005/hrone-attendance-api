# REVIEW.md

The starter `app/main.py` was reviewed against `openapi.yaml`, `DATA_MODEL.md`, and `PROBLEM_STATEMENT.docx`. The following defects were identified and fixed.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `EmployeeIn`, `PunchInIn`, `PunchOutIn` models | Normal Pydantic `int` fields can coerce strings/floats instead of enforcing strict integer epoch milliseconds. | Send `"1792071000000"` or `1792071000000.5` as `punched_at`. | Added strict integer validation and rejected non-integer timestamps with 422. |
| 2 | Punch-in / punch-out timestamp validation | Seconds-looking Unix timestamps can be accepted as milliseconds. | Send a 10-digit timestamp such as `1792071000`. | Reject values below `100000000000`. |
| 3 | Punch-in / punch-out timestamp conversion | Out-of-range epoch values need controlled validation rather than producing server errors. | Send an extremely large epoch-millisecond value. | Added range validation and converted conversion failures into 422 responses. |
| 4 | `create_indexes()` | Starter indexes were insufficient for uniqueness and the required query performance. | Inspect indexes or run explain against larger attendance data. | Added unique employee index, unique `(emp_code, date)` attendance index, date index, and employee department/joined-date index. |
| 5 | `list_employees()` | Pagination parameters were not fully constrained by the API contract. | Request `page=0` or `page_size=101`. | Added `ge=1` and `le=100` validation. |
| 6 | `create_employee()` / `EmployeeIn` | Employee input did not enforce the contract's validation rules such as employee-code, email, date and shift formats. | Submit malformed employee data. | Added validation matching the API contract. |
| 7 | `punch_in()` | Check-then-insert alone is race-prone for simultaneous requests. | Send two identical punch-ins concurrently. | Added a unique `(emp_code, date)` database index and handled `DuplicateKeyError` as 409. |
| 8 | `punch_in()` attendance-date calculation | Overnight shifts were assigned using the punch-in calendar date instead of the shift-start date. | Test an overnight shift such as `22:00-06:00` with a punch-in after midnight. | Added IST overnight-shift date handling according to R1. |
| 9 | `punch_in()` time handling | Timestamp precision and timezone handling could produce inconsistent stored values. | Send timestamps containing milliseconds and inspect the stored/returned value. | Truncate instants to whole seconds before calculation and storage. |
| 10 | `list_attendance()` | Starter implementation loads all matching attendance documents into Python and sorts/paginates there. This does not scale to approximately 100,000 records. | Run a late-page request against a large collection. | Moved filtering, sorting, pagination and counting into MongoDB. |
| 11 | `list_attendance()` | Date filters were not strictly validated and invalid ranges could be accepted. | Send malformed dates or `date_from > date_to`. | Added strict `YYYY-MM-DD` validation and range validation with 422. |
| 12 | `list_attendance()` | Status filtering did not reject unsupported status values. | Send `status=INVALID`. | Validate status against the five allowed values and return 422. |
| 13 | `list_attendance()` response | MongoDB's internal `_id` was exposed/converted to an API `id`, although `_id` is not part of the contract. | Inspect the JSON response for `id`/`_id`. | Removed MongoDB identifiers from API responses. |
| 14 | `compute_late_minutes()` / punch-in | Late-minute calculation must use the strict 10-minute grace period and floor whole minutes from shift start. | Test exactly 10 minutes late and values such as 10:15:59. | Apply `> 10 minutes` rule and floor elapsed minutes from shift start. |
| 15 | `compute_overtime()` | Overtime must start only at 30 minutes and use whole-minute floor calculation. | Test 29:59 and exactly 30:00 overtime. | Return 0 below 30 minutes and floor the elapsed overtime minutes. |
| 16 | `compute_overtime()` | Overnight shifts require the shift end to fall on the following calendar day. | Test a `22:00-06:00` shift and punch out after midnight. | Detect `shift_end <= shift_start` and move shift end to the next day. |
| 17 | `compute_work_hours()` | Work hours must use elapsed seconds and half-up rounding rather than ordinary binary-float/banker's rounding. | Test values exactly at a rounding boundary. | Calculate seconds / 3600 using `Decimal` and `ROUND_HALF_UP`. |
| 18 | Punch-out | Punch-out must select the correct open attendance record and reject invalid punch-out times. | Punch out before/equal to punch-in or more than 24 hours later. | Validate elapsed time and return 422 for invalid values. |
| 19 | Punch-out concurrency | Two simultaneous punch-outs must not both update the same record successfully. | Send two punch-outs concurrently for the same attendance record. | Use an atomic update requiring `punch_out=None`; only one request succeeds. |
| 20 | Regularization | A correction must recompute all derived fields from the final punch times. | Change `punch_in` or `punch_out` and compare work/late/overtime/half-day values. | Recalculate `late_minutes`, `work_hours`, `overtime_minutes`, and `half_day`. |
| 21 | Regularization | A successful correction must append audit history instead of overwriting it. | Perform multiple PATCH requests and inspect `history`. | Append exactly one history entry containing only actually changed fields. |
| 22 | Regularization concurrency | Read-modify-write can cause concurrent corrections to silently overwrite each other's history. | Submit two PATCH requests based on the same old record. | Use optimistic concurrency conditions and return 409 to the losing update. |
| 23 | Regularization | A no-op PATCH should not create unnecessary history. | Submit a PATCH with exactly the existing values. | Detect unchanged fields and return 422. |
| 24 | Employee monthly analytics | `present_days` must count only Monday-Friday attendance and treat half-days as 0.5. | Add weekend attendance or a half-day record. | Apply weekday filtering and half-day weighting in the aggregation. |
| 25 | Employee monthly analytics | Working days must start from the employee's `joined_on` date for mid-month joiners. | Create an employee who joins halfway through the month. | Calculate working days from `max(joined_on, month_start)`. |
| 26 | Department summary | Starting from attendance logs can exclude employees who have no attendance records. | Add an employee with no logs. | Start from eligible employees and `$lookup` their attendance records. |
| 27 | Department summary | Employees who joined after the reporting period must not be counted in headcount. | Add a future employee and request the summary. | Filter employees using `joined_on <= period_end`. |
| 28 | Department summary | Average work hours must be calculated over attendance records, not as an average of employee averages. | Use employees with different numbers of attendance records. | Aggregate total qualifying work hours and record count, then divide once. |
| 29 | Late leaderboard | Simple sorting plus limiting produces incorrect results when employees tie at the cutoff. | Create a tie at the requested rank limit. | Use MongoDB `$rank` and filter by rank after ranking. |
| 30 | Department trend | Missing calendar days must still appear in the result. | Request a range containing weekdays with no attendance records. | Use MongoDB `$densify` to generate one row per calendar day. |
| 31 | Department trend | Weekend attendance must not be treated as a working-day attendance rate. | Add weekend attendance. | Calculate `attendance_rate` only for Monday-Friday; weekend rate is null. |
| 32 | Department trend | Headcount changes with employee joining dates and must include employees without logs. | Add a mid-period employee with no attendance. | Calculate daily headcount from employee `joined_on` values. |
| 33 | Department trend | The 7-day moving average must use the preceding six rows plus the current row and allow a shorter initial window. | Inspect the first several dates and a range with missing attendance. | Use `$setWindowFields` with `documents: [-6, 0]`. |
| 34 | Analytics rounding | Reported values require half-up rounding; rates require four decimal places. | Test values exactly at rounding boundaries. | Added explicit `Decimal` half-up rounding for reported metrics. |
| 35 | `/admin/explain/{endpoint}` | Explain output must use the same filters, sort, pagination and aggregation pipeline as the actual endpoint. | Compare explain output with the corresponding endpoint request. | Built explain queries/pipelines from the same endpoint parameters and used `executionStats`. |

## Reviewed and not treated as defects

- Missing `history` in older seeded attendance documents is allowed by the data model and is treated as an empty history.
- Missing `half_day` in older attendance records is treated as `false`, as permitted by the data model.
- A weekday with no attendance document is different from an `ABSENT` record and is therefore not treated as an attendance record.
- MongoDB `_id` is retained internally because MongoDB requires it, but it is never used as the public employee or attendance identifier.
- Calendar dates remain `YYYY-MM-DD` strings and shift times remain `HH:MM` strings because that is the API/data-model contract.
- Weekend attendance records are retained and still contribute to late/overtime totals; only `present_days` and working-day attendance rates exclude weekends.