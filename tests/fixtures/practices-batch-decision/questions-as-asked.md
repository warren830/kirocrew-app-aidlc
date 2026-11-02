# Practices Discovery — Questions

The repo has one commit, no CI, no linter config, and no packaging, so these
choices cannot be read from the code. Each question shows the lead's
suggested answer.

## Question 1: How should changes reach `main`?
There is one branch (`main`) and one commit, so no team habit shows yet.

A. Short-lived branches, squash-merged to `main`, one commit per piece of work, subject `<component>: <summary>`, no deploy step beyond the merge (suggested)
B. Commit straight to `main`, no branches
C. Short-lived branches, normal merge commits (keep every commit)
X. Other (please specify)

[Answer]:

## Question 2: Build a thin end-to-end slice first?
A walking skeleton is a minimal version that runs the whole way through, built first to prove the pieces connect before the real features go in. The CLI already runs end to end today.

A. No — the app already runs end to end (suggested)
B. Yes — build a thin due-date slice first and check it before the rest
X. Other (please specify)

[Answer]:

## Question 3: When are tests written, and how much is required?
The classic default is 80% line coverage plus CI before merge, but the repo has no coverage tool and no CI.

A. Write the code, then its pytest tests; skip the 80% coverage and CI rule for this small CLI; the whole suite must pass before merge (suggested)
B. Same order, and keep the 80% coverage floor (add coverage tooling)
C. Test first (TDD): write a failing test, then the code
X. Other (please specify)

[Answer]:

## Question 4: Which Python, and how is pytest provided?
`tasklist.py` uses `list[str] | None`, which needs Python 3.10 or newer. This Mac has Python 3.9.6 and no pytest, so the tests cannot run here today.

A. Require Python 3.10+, install pytest in a local virtual env, and prove changes with `python -m pytest` from the repo root (suggested)
B. Lower the bar to Python 3.9 (change the type hints) and install pytest globally
X. Other (please specify)

[Answer]:

## Question 5: Add a formatter or linter?
There is no Black, Ruff, or flake8 config.

A. No tool for now; follow PEP 8 by habit (suggested)
B. Add Black and Ruff
X. Other (please specify)

[Answer]:

## Question 6: Which of these become firm rules? (pick any)
These are proposals from the scan; only the ones you pick become rules.

A. Old `tasks.json` files (no due date) must always still load
B. Use only the Python standard library — no new packages in the app
C. `list` output stays exactly the same for tasks with no due date
D. Tests set "today" themselves instead of reading the real clock
E. The whole test suite must pass before merge
X. Other (please specify)

[Answer]:
