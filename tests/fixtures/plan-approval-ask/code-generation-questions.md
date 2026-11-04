# Code Generation Plan Approval

AI-DLC writes this file when it asks you to approve the plan. To answer here
instead of in chat, write your answer after `[Answer]:` and tell me in chat when you are done.

## Plan Approval

Approve the code plan for u1-due-date-list?

- Builds: `add --due`, the saved `due` entry, and the date on `list`'s screen line
- Touches: `tasklist.py` (changed), `tests/test_due_dates.py` (new)
- Tests: about 30 pytest tests: functions, command line, both views, old files

Full plan: aidlc/spaces/default/intents/261104-add-an-optional/construction/u1-due-date-list/code-generation/code-generation-plan.md
Test instructions: aidlc/spaces/default/intents/261104-add-an-optional/construction/u1-due-date-list/code-generation/unit-test-instructions.md

[Approval Fingerprint]: sha256:v3:db1b2788710dd76aa1f51c5070d8ec61ca467b2d88a940cf41562a7df188cf41
[Planned Source]: 7e069d4f53da66d526c027e7199baf46d27b60d2d453b1fb4df53c82a34087ab

- A. Approve Plan
- B. Request Changes
- C. I'll edit the files

[Answer]:
