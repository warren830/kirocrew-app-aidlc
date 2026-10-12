# Requirements Analysis — Clarifying Questions

**Request**: Add a farewell(name) function next to greet in hello.py, with a unit test.

The request already says *what* to add. These four questions cover the details the existing code leaves open: the exact wording the test checks, the code style, how unusual input behaves, and how the project's first test is set up.

For each question, write the letter of your choice after `[Answer]:`. For **X**, add your own answer after the letter.

## Question 1: Farewell wording

`greet("Ada")` returns `"Hello, Ada!"`. Nothing in the code sets the farewell text yet, and whatever `farewell` returns is the exact value the unit test will check.

What should `farewell("Ada")` return?

A. `"Goodbye, Ada!"` — template `"Goodbye, {name}!"`, the same shape as `greet` (Recommended)
B. `"Farewell, Ada!"` — template `"Farewell, {name}!"`, matching the function's name
C. `"Bye, Ada!"` — template `"Bye, {name}!"`
D. `"See you later, Ada!"` — template `"See you later, {name}!"`
X. Other (please specify)

[Answer]:

## Question 2: Type hints and docstring

`greet` has no type hints and no docstring. Whatever `farewell` does here also sets the style for functions added later.

Should `farewell` have type hints and a docstring?

A. No — match `greet` exactly (`def farewell(name):`), so the change is only the new function and its test (Recommended)
B. Yes, on `farewell` only — `def farewell(name: str) -> str:` plus a one-line docstring; `greet` stays as it is, so the two functions will look different
C. Yes, on both — also add the same type hints and docstring to `greet` so the file is consistent (this edits `greet`, which the request didn't ask for)
X. Other (please specify)

[Answer]:

## Question 3: Empty or non-text names

`greet` does no checking and puts whatever it receives straight into the text: `greet("")` returns `"Hello, !"` and `greet(42)` returns `"Hello, 42!"`. The project's testing rules require the test to cover at least two edge cases besides the normal case, so this choice decides what those edge-case tests check. The examples below use the "Goodbye" wording; they follow whatever you pick in Question 1.

How should `farewell` handle an empty name or a value that isn't text?

A. Same as `greet` — no checks; `farewell("")` returns `"Goodbye, !"` and `farewell(42)` returns `"Goodbye, 42!"`, and the edge-case tests lock that behaviour in (Recommended)
B. Reject non-text values — raise `TypeError` when `name` isn't a string; an empty string is still used as-is
C. Reject non-text values and blank names — raise `TypeError` when `name` isn't a string, and `ValueError` when it is empty or only spaces
D. Use a generic farewell for blank names — `farewell("")` returns `"Goodbye!"`; any other value is used as-is, like `greet`
X. Other (please specify)

[Answer]:

## Question 4: How the first test is set up

This is the project's first test, so it also decides where tests live and how they are run. On this machine the `pytest` command works, but `python3 -m pytest` does not (pytest isn't installed for that Python).

How should the unit test be written and run?

A. pytest-style test functions in `test_hello.py` at the project root, run with `pytest -q` (Recommended)
B. A standard-library `unittest` test case in `test_hello.py` at the project root, run with `python3 -m unittest discover -v` — needs no extra tool, and `pytest` can run it too
C. pytest-style tests in a `tests/` folder, run with `pytest -q` — needs one extra setup file (such as an empty root `conftest.py`) so the test can import `hello.py`
X. Other (please specify)

[Answer]:
