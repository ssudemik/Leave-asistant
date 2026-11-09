from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models import LeaveStatus, LeaveType


class EmployeeCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    annual_leave_days: int = Field(default=14, ge=0, le=60)


class EmployeeOut(EmployeeCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class LeaveRequestCreate(BaseModel):
    employee_id: int
    leave_type: LeaveType = LeaveType.ANNUAL
    start_date: date
    end_date: date
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_dates(self) -> "LeaveRequestCreate":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class LeaveRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    employee_id: int
    leave_type: LeaveType
    start_date: date
    end_date: date
    working_days: int
    status: LeaveStatus
    reason: str | None


class Balance(BaseModel):
    employee_id: int
    year: int
    entitlement: int
    approved: int
    pending: int
    remaining: int


class ParseRequest(BaseModel):
    text: str = Field(min_length=3, max_length=1000)
    today: date | None = Field(
        default=None, description="Reference date for relative expressions; defaults to today."
    )


class LeaveDraft(BaseModel):
    """A structured leave request extracted from free text, before it is saved."""

    leave_type: LeaveType
    start_date: date
    end_date: date
    reason: str | None = None
    source: str = Field(description="'llm' or 'rules': which parser produced the draft")


class FromTextRequest(ParseRequest):
    employee_id: int
