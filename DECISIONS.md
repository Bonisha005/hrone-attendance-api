@"
# DECISIONS.md

## 1. Indexes
I created a unique index on `employees.emp_code` so employee codes cannot be duplicated. I created a unique `(emp_code, date)` index on attendance to guarantee one record per employee per day. I also added an attendance date index for date-based queries. For employee analytics, I created `(department, joined_on)` and a separate `joined_on` index because department summary filters employees by joining date. I rejected relying only on `(department, joined_on)` because a query starting with `joined_on` alone cannot efficiently use that compound index.

## 2. Punch-in race
Two identical requests may both initially find no record. The unique `(emp_code, date)` index is the final protection. One insert succeeds; the other gets `DuplicateKeyError`, which the API converts to HTTP 409.

## 3. Ties
The late leaderboard uses MongoDB `$rank`. Employees tied at the cutoff receive the same rank and are all returned.

## 4. Headcount
The department summary starts from employees, filters by department and `joined_on <= period_end`, then looks up attendance. Employees with zero logs therefore remain included.

## 5. 100x data
I would introduce pre-aggregated daily/monthly summaries and stronger database monitoring to reduce repeated analytics work.
"@ | Set-Content DECISIONS.md