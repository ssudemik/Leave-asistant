from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import parser, services
from app.db import Base, engine, get_session
from app.models import Employee, LeaveRequest
from app.schemas import (
    Balance,
    EmployeeCreate,
    EmployeeOut,
    FromTextRequest,
    LeaveDraft,
    LeaveRequestCreate,
    LeaveRequestOut,
    ParseRequest,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


SessionDep = Annotated[Session, Depends(get_session)]


app = FastAPI(
    title="Leave Assistant API",
    description="Leave request management with balance rules and natural-language input.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(services.LeaveError)
async def leave_error_handler(_: Request, exc: services.LeaveError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------- Employees ----------


@app.post("/employees", response_model=EmployeeOut, status_code=201)
def create_employee(data: EmployeeCreate, session: SessionDep) -> Employee:
    employee = Employee(**data.model_dump())
    session.add(employee)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="An employee with this email already exists"
        ) from None
    session.refresh(employee)
    return employee


@app.get("/employees/{employee_id}", response_model=EmployeeOut)
def read_employee(employee_id: int, session: SessionDep) -> Employee:
    return services.get_employee(session, employee_id)


@app.get("/employees/{employee_id}/balance", response_model=Balance)
def read_balance(employee_id: int, session: SessionDep, year: int | None = None) -> Balance:
    employee = services.get_employee(session, employee_id)
    return services.get_balance(session, employee, year or date.today().year)


@app.get("/employees/{employee_id}/leave-requests", response_model=list[LeaveRequestOut])
def list_employee_requests(employee_id: int, session: SessionDep) -> list[LeaveRequest]:
    services.get_employee(session, employee_id)
    return list(
        session.scalars(
            select(LeaveRequest)
            .where(LeaveRequest.employee_id == employee_id)
            .order_by(LeaveRequest.start_date)
        )
    )


# ---------- Leave requests ----------


@app.post("/leave-requests", response_model=LeaveRequestOut, status_code=201)
def create_leave_request(data: LeaveRequestCreate, session: SessionDep) -> LeaveRequest:
    return services.create_leave_request(session, data)


@app.post("/leave-requests/parse", response_model=LeaveDraft)
def parse_leave_text(data: ParseRequest) -> LeaveDraft:
    """Preview what the assistant understood, without saving anything."""
    try:
        return parser.parse(data.text, data.today)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/leave-requests/from-text", response_model=LeaveRequestOut, status_code=201)
def create_from_text(data: FromTextRequest, session: SessionDep) -> LeaveRequest:
    """Parse free text and create the request; the same business rules apply."""
    try:
        draft = parser.parse(data.text, data.today)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return services.create_leave_request(
        session,
        LeaveRequestCreate(
            employee_id=data.employee_id,
            leave_type=draft.leave_type,
            start_date=draft.start_date,
            end_date=draft.end_date,
            reason=draft.reason,
        ),
    )


@app.post("/leave-requests/{request_id}/approve", response_model=LeaveRequestOut)
def approve(request_id: int, session: SessionDep) -> LeaveRequest:
    return services.decide(session, request_id, approve=True)


@app.post("/leave-requests/{request_id}/reject", response_model=LeaveRequestOut)
def reject(request_id: int, session: SessionDep) -> LeaveRequest:
    return services.decide(session, request_id, approve=False)
