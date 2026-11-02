# Requirements Analysis Questions

Context: today `add` takes only a title, and `list` prints lines like `1. [ ] buy milk`. Intent Capture settled that due dates are optional, dates only (no time of day), and that there are no reminders.

## Q1. How should a due date be given when adding a task?

A. An optional flag: `python tasklist.py add "pay rent" --due 2026-11-30` (date as YYYY-MM-DD)
B. An optional second argument: `python tasklist.py add "pay rent" 2026-11-30`
X. Other (please specify)

[Answer]: A

## Q2. Is a task due today already overdue?

A. No; it becomes overdue the day after its due date
B. Yes; it is overdue from the start of its due date
X. Other (please specify)

[Answer]: A — "Not overdue on its due date; it shows as overdue from the next calendar day"

## Q3. Can a task that is done be marked overdue?

A. No; done tasks are never marked overdue
B. Yes; a done task past its due date is still marked
X. Other (please specify)

[Answer]: A

## Q4. What should `list` print for tasks with a due date?

Tasks without a due date keep printing exactly as today, for example `1. [ ] buy milk`.

A. Show the due date, and add `OVERDUE` when late: `1. [ ] pay rent (due 2026-11-01) OVERDUE`
B. Show nothing extra unless late, then add `OVERDUE`: `1. [ ] pay rent OVERDUE`
C. Put a `!` in front of late tasks and show the date: `! 1. [ ] pay rent (due 2026-11-01)`
X. Other (please specify)

[Answer]: A

## Q5. What should happen if the due date is not a real date (for example `2026-13-40` or `tomorrow`)?

A. Print an error, exit with code 1, and save nothing
B. Save the task without a due date and print a warning
X. Other (please specify)

[Answer]: A

## Q6. Which of these also apply to this first version? (select all that apply)

The intent left these open.

A. Task files saved before this change must still load and list unchanged
B. The existing tests must still pass
C. Sorting or filtering by due date is out of scope
D. Changing or removing a due date after adding is out of scope
E. None of these
X. Other (please specify)

[Answer]: A, B, C, D
