"""Business rules for leave requests.

Kept separate from the HTTP layer so the rules can be unit-tested without a server.
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee, LeaveRequest, LeaveStatus, LeaveType
from app.schemas import Balance, LeaveRequestCreate


class LeaveError(Exception):
    """A request that breaks a business rule. `status_code` maps it to HTTP."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


ACTIVE_STATUSES = (LeaveStatus.PENDING, LeaveStatus.APPROVED)


def count_working_days(start: date, end: date) -> int:
    """Mon–Fri days between start and end, inclusive. Public holidays are not handled yet."""
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def get_employee(session: Session, employee_id: int) -> Employee:
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise LeaveError(f"Employee {employee_id} not found", status_code=404)
    return employee


def get_balance(session: Session, employee: Employee, year: int) -> Balance:
    rows = session.execute(
        select(LeaveRequest.status, LeaveRequest.working_days).where(
            LeaveRequest.employee_id == employee.id,
            LeaveRequest.leave_type == LeaveType.ANNUAL,
            LeaveRequest.status.in_(ACTIVE_STATUSES),
            LeaveRequest.start_date >= date(year, 1, 1),
            LeaveRequest.start_date <= date(year, 12, 31),
        )
    ).all()
    approved = sum(days for status, days in rows if status == LeaveStatus.APPROVED)
    pending = sum(days for status, days in rows if status == LeaveStatus.PENDING)
    return Balance(
        employee_id=employee.id,
        year=year,
        entitlement=employee.annual_leave_days,
        approved=approved,
        pending=pending,
        remaining=employee.annual_leave_days - approved - pending,
    )


def create_leave_request(session: Session, data: LeaveRequestCreate) -> LeaveRequest:
    employee = get_employee(session, data.employee_id)

    working_days = count_working_days(data.start_date, data.end_date)
    if working_days == 0:
        raise LeaveError("The selected range contains no working days")

    if data.start_date.year != data.end_date.year:
        raise LeaveError("A leave request cannot span two calendar years; split it into two")

    # Two active requests may not overlap: [a.start, a.end] ∩ [b.start, b.end] ≠ ∅
    overlap = session.scalar(
        select(LeaveRequest.id).where(
            LeaveRequest.employee_id == employee.id,
            LeaveRequest.status.in_(ACTIVE_STATUSES),
            LeaveRequest.start_date <= data.end_date,
            LeaveRequest.end_date >= data.start_date,
        )
    )
    if overlap is not None:
        raise LeaveError(f"Overlaps with existing leave request {overlap}", status_code=409)

    # Pending requests also reserve balance, so two pending requests cannot overspend it.
    if data.leave_type == LeaveType.ANNUAL:
        balance = get_balance(session, employee, data.start_date.year)
        if working_days > balance.remaining:
            raise LeaveError(
                f"Not enough annual leave: requested {working_days}, remaining {balance.remaining}"
            )

    request = LeaveRequest(
        employee_id=employee.id,
        leave_type=data.leave_type,
        start_date=data.start_date,
        end_date=data.end_date,
        working_days=working_days,
        reason=data.reason,
        status=LeaveStatus.PENDING,
    )
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


def decide(session: Session, request_id: int, approve: bool) -> LeaveRequest:
    request = session.get(LeaveRequest, request_id)
    if request is None:
        raise LeaveError(f"Leave request {request_id} not found", status_code=404)
    if request.status != LeaveStatus.PENDING:
        raise LeaveError(f"Request is already {request.status}", status_code=409)
    request.status = LeaveStatus.APPROVED if approve else LeaveStatus.REJECTED
    session.commit()
    session.refresh(request)
    return request
