# Domain Design — Questions

## Q1. Should the persistence concern become its own building block?

A. Keep one component
B. Split into store and scheduler

[Answer]: B — Store owns persistence.

## Q2. How should a job's owning project be represented?

A. One store file per project
B. One shared file

[Answer]: A — One authoritative store per project.

## Q3. How should the resume eligibility watermark be represented?

A. A persisted field
B. Derived at load time

[Answer]: A — Optional on legacy reads.

## Q4. Where should the system-origin identity for a scheduled run live?

A. A new component
B. Existing engine

[Answer]: B — Typed internal invocation.

## Q5. Which component detects an ambiguous legacy multi-project file?

A. The store
B. The engine

[Answer]: A — Refuse before writes or dispatch.

# Follow-up Questions

## F1. What is a project's identity, and what exactly does "reject duplicate project identities" reject?

A. Configured name
B. Resolved working directory
X. Other (please specify)

[Answer]:

## F2. In a single-project configuration, what happens when a nonempty legacy file and a project-scoped file both exist?

A. Refuse initialization
B. Legacy wins
X. Other (please specify)

[Answer]: ___

## F3. The refusal message points at a "documented storage layout" — where does that documentation live?

A. Existing README
B. Design note
X. Other (please specify)

[Answer]:
