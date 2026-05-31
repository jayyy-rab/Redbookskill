from __future__ import annotations

import enum
from datetime import datetime, date
from sqlalchemy import (
    String,
    Integer,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Date,
    Boolean,
    Enum,
    JSON,
    Numeric,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class Gender(str, enum.Enum):
    M = "M"
    F = "F"


class StudentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    GRADUATED = "GRADUATED"


class ClassRoom(Base):
    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    class_code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    class_name: Mapped[str] = mapped_column(String(100))
    grade_year: Mapped[int] = mapped_column(Integer)
    major: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    students: Mapped[list["Student"]] = relationship(back_populates="class_room")


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(50), index=True)
    gender: Mapped[Gender] = mapped_column(Enum(Gender))
    birthday: Mapped[date | None] = mapped_column(Date, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"))
    status: Mapped[StudentStatus] = mapped_column(Enum(StudentStatus), default=StudentStatus.ACTIVE)
    version: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    class_room: Mapped["ClassRoom"] = relationship(back_populates="students")
    enrollments: Mapped[list["Enrollment"]] = relationship(back_populates="student")


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    course_code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    course_name: Mapped[str] = mapped_column(String(100))
    credit: Mapped[float] = mapped_column(Numeric(3, 1))
    teacher_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    semester: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    enrollments: Mapped[list["Enrollment"]] = relationship(back_populates="course")


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("student_id", "course_id", name="uk_student_course"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    student: Mapped["Student"] = relationship(back_populates="enrollments")
    course: Mapped["Course"] = relationship(back_populates="enrollments")
    score: Mapped["Score | None"] = relationship(back_populates="enrollment", uselist=False)


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id"), unique=True)
    usual_score: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    final_score: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    total_score: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    grade_point: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    enrollment: Mapped["Enrollment"] = relationship(back_populates="score")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_code: Mapped[str] = mapped_column(String(30), unique=True)
    role_name: Mapped[str] = mapped_column(String(50))


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uk_user_role"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    target_table: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

