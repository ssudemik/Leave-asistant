# Leave Assistant API

![CI](https://github.com/ssudemik/Leave-asistant/actions/workflows/ci.yml/badge.svg)

A small backend for managing employee leave requests, with a natural-language
endpoint that turns messages like *"haftaya pazartesi 3 gün izin, çünkü taşınıyorum"*
or *"next Monday to Wednesday off for a family trip"* into a validated leave request.

Built with **Python, FastAPI, SQLAlchemy 2, SQLite, Pydantic, pytest, Docker and GitHub Actions**.
The idea comes from my day job: I work on an enterprise HR platform, and leave
validation (overlaps, balances, working days) is where most of the edge cases live.

## Features

- **Employees & leave requests** – create, list, approve, reject
- **Business rules** enforced in one service layer:
  - only working days (Mon–Fri) are counted
  - requests that overlap an active (pending or approved) request are rejected – `409`
  - annual leave cannot exceed the remaining balance; **pending requests reserve balance**, so two pending requests cannot overspend it – `422`
  - sick and unpaid leave do not consume annual balance
  - a request can be decided only once – `409`
- **Natural-language input**
  - `POST /leave-requests/parse` – preview what was understood, saves nothing
  - `POST /leave-requests/from-text` – parse and create in one step (same rules apply)
  - uses an LLM when `LLM_API_KEY` is set (any OpenAI-compatible API), with output
    validated by Pydantic
  - falls back to a deterministic **English + Turkish rule-based parser** when there is no key,
    the network fails, or the model returns invalid JSON – the endpoint never depends on an
    external service to work
- Interactive API docs at `/docs` (OpenAPI)

## Architecture

```
app/
├── main.py      HTTP layer: routes, dependency injection, error mapping
├── services.py  business rules (no HTTP code → unit-testable)
├── parser.py    LLM parser + rule-based fallback
├── models.py    SQLAlchemy ORM models
├── schemas.py   Pydantic request/response models and validation
└── db.py        engine and per-request session
tests/           33 tests: API, business rules, parser (LLM mocked)
```

Request flow for `from-text`:

```
text ──► parser.parse() ──► LLM (if configured) ──valid?──► LeaveDraft
                │                                   │ no / error
                └──────────► rule-based parser ◄────┘
LeaveDraft ──► services.create_leave_request() ──► overlap check ──► balance check ──► DB
```

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
# open http://localhost:8000/docs
```

With Docker:

```bash
docker build -t leave-assistant .
docker run -p 8000:8000 -v leave-data:/data leave-assistant
```

To enable the LLM parser, copy `.env.example` to `.env` and set `LLM_API_KEY`
(plus `LLM_BASE_URL` / `LLM_MODEL` for non-OpenAI providers).

## Example

```bash
curl -X POST localhost:8000/employees -H 'content-type: application/json' \
  -d '{"full_name": "Sude Emik", "email": "sude@example.com"}'

curl -X POST localhost:8000/leave-requests/from-text -H 'content-type: application/json' \
  -d '{"employee_id": 1, "text": "haftaya pazartesi 3 gün izin istiyorum çünkü taşınıyorum"}'
# {"id":1,"leave_type":"annual","start_date":"2026-11-16","end_date":"2026-11-18",
#  "working_days":3,"status":"pending","reason":"taşınıyorum", ...}

curl localhost:8000/employees/1/balance
# {"entitlement":14,"approved":0,"pending":3,"remaining":11, ...}
```

## Tests

```bash
pytest -q        # 33 passed
ruff check .     # lint
```

Each test runs against a fresh in-memory SQLite database. The LLM is never called in tests:
its responses are mocked, including broken JSON, invalid values and network errors, to prove
the fallback path works.

## Design decisions

- **Rules live in `services.py`, not in route handlers.** The same rules apply whether a
  request comes from the JSON endpoint or from free text, and they can be tested directly.
- **LLM output is untrusted input.** It goes through the same Pydantic validation as user
  input, and any failure falls back to the rule parser instead of returning a 500.
- **Pending requests reserve balance.** Otherwise an employee could submit several pending
  requests that are each valid alone but exceed the balance together.
- **SQLite for simplicity.** `DATABASE_URL` is configurable, so moving to PostgreSQL is a
  config change plus a driver.

## What I would add next

- Public holidays (Türkiye calendar) in the working-day calculation
- Authentication and a manager role for approvals
- Alembic migrations and PostgreSQL in `docker-compose`
- Row-level locking around the balance check for concurrent requests
- Half-day leave

## Development notes

I used AI coding tools (Claude Code) during development and reviewed, ran and tested the
generated code before committing it. Tests for the parser were written to catch the cases where
generated date logic was wrong (for example "next Monday to Wednesday" across a week boundary).
