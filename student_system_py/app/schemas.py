from __future__ import annotations

from datetime import date, datetime
from pydantic import BaseModel, Field
from .models import Gender, StudentStatus


class ClassCreate(BaseModel):
    class_code: str
    class_name: str
    grade_year: int
    major: str


class ClassOut(ClassCreate):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class StudentCreate(BaseModel):
    student_no: str
    name: str
    gender: Gender
    birthday: date | None = None
    phone: str | None = None
    email: str | None = None
    class_id: int
    status: StudentStatus = StudentStatus.ACTIVE


class StudentUpdate(BaseModel):
    name: str | None = None
    gender: Gender | None = None
    birthday: date | None = None
    phone: str | None = None
    email: str | None = None
    class_id: int | None = None
    status: StudentStatus | None = None
    version: int = Field(..., description="乐观锁版本号，必须和数据库一致")


class StudentOut(BaseModel):
    id: int
    student_no: str
    name: str
    gender: Gender
    birthday: date | None
    phone: str | None
    email: str | None
    class_id: int
    status: StudentStatus
    version: int
    deleted: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CourseCreate(BaseModel):
    course_code: str
    course_name: str
    credit: float
    teacher_name: str | None = None
    semester: str


class CourseOut(CourseCreate):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class EnrollmentCreate(BaseModel):
    student_id: int
    course_id: int


class EnrollmentOut(BaseModel):
    id: int
    student_id: int
    course_id: int
    enrolled_at: datetime

    model_config = {"from_attributes": True}


class ScoreUpsert(BaseModel):
    usual_score: float = Field(ge=0, le=100)
    final_score: float = Field(ge=0, le=100)


class ScoreOut(BaseModel):
    id: int
    enrollment_id: int
    usual_score: float
    final_score: float
    total_score: float
    grade_point: float | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class AuditLogOut(BaseModel):
    id: int
    user_id: int | None
    action: str
    target_table: str | None
    target_id: int | None
    detail_json: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}

