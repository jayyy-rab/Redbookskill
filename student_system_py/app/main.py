from __future__ import annotations

from typing import Any
from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from .database import Base, engine
from .deps import get_actor_id, get_db, require_role
from .models import AuditLog, ClassRoom, Course, Enrollment, Score, Student
from .schemas import (
    AuditLogOut,
    ClassCreate,
    ClassOut,
    CourseCreate,
    CourseOut,
    EnrollmentCreate,
    EnrollmentOut,
    ScoreOut,
    ScoreUpsert,
    StudentCreate,
    StudentOut,
    StudentUpdate,
)

app = FastAPI(
    title="学生管理系统 API",
    version="1.0.0",
    description=(
        "一个教学演示用的学生管理系统后端，支持学生/班级/课程的增删改查、"
        "选课与成绩管理、班级排名报表、软删除、乐观锁和审计日志。"
    ),
)
Base.metadata.create_all(bind=engine)


def write_audit(
    db: Session,
    *,
    user_id: int | None,
    action: str,
    target_table: str | None,
    target_id: int | None,
    detail_json: dict[str, Any] | None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            target_table=target_table,
            target_id=target_id,
            detail_json=detail_json,
        )
    )


@app.get("/health", summary="健康检查", tags=["系统"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/classes",
    response_model=ClassOut,
    dependencies=[Depends(require_role("admin"))],
    summary="新增班级",
    tags=["班级管理"],
)
def create_class(
    payload: ClassCreate,
    db: Session = Depends(get_db),
    actor_id: int | None = Depends(get_actor_id),
):
    obj = ClassRoom(**payload.model_dump())
    db.add(obj)
    db.flush()
    write_audit(
        db,
        user_id=actor_id,
        action="class.create",
        target_table="classes",
        target_id=obj.id,
        detail_json=payload.model_dump(),
    )
    db.commit()
    db.refresh(obj)
    return obj


@app.get("/api/classes", response_model=list[ClassOut], summary="班级列表", tags=["班级管理"])
def list_classes(db: Session = Depends(get_db)):
    return db.scalars(select(ClassRoom).order_by(ClassRoom.id.desc())).all()


@app.post(
    "/api/students",
    response_model=StudentOut,
    dependencies=[Depends(require_role("teacher"))],
    summary="新增学生",
    tags=["学生管理"],
)
def create_student(
    payload: StudentCreate,
    db: Session = Depends(get_db),
    actor_id: int | None = Depends(get_actor_id),
):
    class_exists = db.get(ClassRoom, payload.class_id)
    if not class_exists:
        raise HTTPException(status_code=404, detail="班级不存在")
    obj = Student(**payload.model_dump())
    db.add(obj)
    db.flush()
    write_audit(
        db,
        user_id=actor_id,
        action="student.create",
        target_table="students",
        target_id=obj.id,
        detail_json=payload.model_dump(mode="json"),
    )
    db.commit()
    db.refresh(obj)
    return obj


@app.get("/api/students", response_model=list[StudentOut], summary="学生分页查询", tags=["学生管理"])
def list_students(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    name: str | None = Query(default=None),
    class_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    stmt = select(Student).where(Student.deleted.is_(False))
    if name:
        stmt = stmt.where(Student.name.like(f"%{name}%"))
    if class_id:
        stmt = stmt.where(Student.class_id == class_id)
    stmt = stmt.order_by(Student.id.desc()).offset((page - 1) * size).limit(size)
    return db.scalars(stmt).all()


@app.get("/api/students/{student_id}", response_model=StudentOut, summary="查询学生详情", tags=["学生管理"])
def get_student(student_id: int, db: Session = Depends(get_db)):
    obj = db.get(Student, student_id)
    if not obj or obj.deleted:
        raise HTTPException(status_code=404, detail="学生不存在")
    return obj


@app.put(
    "/api/students/{student_id}",
    response_model=StudentOut,
    dependencies=[Depends(require_role("teacher"))],
    summary="更新学生信息",
    tags=["学生管理"],
)
def update_student(
    student_id: int,
    payload: StudentUpdate,
    db: Session = Depends(get_db),
    actor_id: int | None = Depends(get_actor_id),
):
    obj = db.get(Student, student_id)
    if not obj or obj.deleted:
        raise HTTPException(status_code=404, detail="学生不存在")
    if payload.version != obj.version:
        raise HTTPException(status_code=409, detail="数据版本冲突，请刷新后重试")
    update_data = payload.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        if k != "version":
            setattr(obj, k, v)
    obj.version += 1
    write_audit(
        db,
        user_id=actor_id,
        action="student.update",
        target_table="students",
        target_id=obj.id,
        detail_json=update_data,
    )
    db.commit()
    db.refresh(obj)
    return obj


@app.delete(
    "/api/students/{student_id}",
    dependencies=[Depends(require_role("admin"))],
    summary="删除学生（软删除）",
    tags=["学生管理"],
)
def soft_delete_student(
    student_id: int,
    db: Session = Depends(get_db),
    actor_id: int | None = Depends(get_actor_id),
):
    obj = db.get(Student, student_id)
    if not obj or obj.deleted:
        raise HTTPException(status_code=404, detail="学生不存在")
    obj.deleted = True
    obj.version += 1
    write_audit(
        db,
        user_id=actor_id,
        action="student.delete",
        target_table="students",
        target_id=obj.id,
        detail_json={"soft_deleted": True},
    )
    db.commit()
    return {"ok": True}


@app.post(
    "/api/courses",
    response_model=CourseOut,
    dependencies=[Depends(require_role("teacher"))],
    summary="新增课程",
    tags=["课程管理"],
)
def create_course(
    payload: CourseCreate,
    db: Session = Depends(get_db),
    actor_id: int | None = Depends(get_actor_id),
):
    obj = Course(**payload.model_dump())
    db.add(obj)
    db.flush()
    write_audit(
        db,
        user_id=actor_id,
        action="course.create",
        target_table="courses",
        target_id=obj.id,
        detail_json=payload.model_dump(mode="json"),
    )
    db.commit()
    db.refresh(obj)
    return obj


@app.get("/api/courses", response_model=list[CourseOut], summary="课程列表", tags=["课程管理"])
def list_courses(db: Session = Depends(get_db)):
    return db.scalars(select(Course).order_by(Course.id.desc())).all()


@app.post(
    "/api/enrollments",
    response_model=EnrollmentOut,
    dependencies=[Depends(require_role("teacher"))],
    summary="学生选课",
    tags=["选课管理"],
)
def create_enrollment(
    payload: EnrollmentCreate,
    db: Session = Depends(get_db),
    actor_id: int | None = Depends(get_actor_id),
):
    student = db.get(Student, payload.student_id)
    course = db.get(Course, payload.course_id)
    if not student or student.deleted:
        raise HTTPException(status_code=404, detail="学生不存在")
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")
    exists = db.scalar(
        select(Enrollment).where(
            and_(Enrollment.student_id == payload.student_id, Enrollment.course_id == payload.course_id)
        )
    )
    if exists:
        raise HTTPException(status_code=409, detail="该学生已选该课程")
    obj = Enrollment(**payload.model_dump())
    db.add(obj)
    db.flush()
    write_audit(
        db,
        user_id=actor_id,
        action="enrollment.create",
        target_table="enrollments",
        target_id=obj.id,
        detail_json=payload.model_dump(),
    )
    db.commit()
    db.refresh(obj)
    return obj


def calc_grade_point(total: float) -> float:
    if total >= 90:
        return 4.0
    if total >= 80:
        return 3.0
    if total >= 70:
        return 2.0
    if total >= 60:
        return 1.0
    return 0.0


@app.put(
    "/api/scores/{enrollment_id}",
    response_model=ScoreOut,
    dependencies=[Depends(require_role("teacher"))],
    summary="录入/更新成绩",
    tags=["成绩管理"],
)
def upsert_score(
    enrollment_id: int,
    payload: ScoreUpsert,
    db: Session = Depends(get_db),
    actor_id: int | None = Depends(get_actor_id),
):
    enrollment = db.get(Enrollment, enrollment_id)
    if not enrollment:
        raise HTTPException(status_code=404, detail="选课记录不存在")
    total = round(payload.usual_score * 0.4 + payload.final_score * 0.6, 2)
    gp = calc_grade_point(total)
    obj = db.scalar(select(Score).where(Score.enrollment_id == enrollment_id))
    if obj is None:
        obj = Score(
            enrollment_id=enrollment_id,
            usual_score=payload.usual_score,
            final_score=payload.final_score,
            total_score=total,
            grade_point=gp,
        )
        db.add(obj)
        action = "score.create"
    else:
        obj.usual_score = payload.usual_score
        obj.final_score = payload.final_score
        obj.total_score = total
        obj.grade_point = gp
        action = "score.update"
    db.flush()
    write_audit(
        db,
        user_id=actor_id,
        action=action,
        target_table="scores",
        target_id=obj.id,
        detail_json=payload.model_dump() | {"total_score": total, "grade_point": gp},
    )
    db.commit()
    db.refresh(obj)
    return obj


@app.get("/api/reports/class-rank", summary="班级成绩排名报表", tags=["报表"])
def report_class_rank(db: Session = Depends(get_db)):
    avg_score = func.avg(Score.total_score).label("avg_score")
    rank_col = func.dense_rank().over(
        partition_by=Student.class_id,
        order_by=desc(avg_score),
    ).label("class_rank")

    stmt = (
        select(
            Student.id.label("student_id"),
            Student.student_no,
            Student.name,
            ClassRoom.class_name,
            func.round(avg_score, 2).label("avg_score"),
            rank_col,
        )
        .join(ClassRoom, ClassRoom.id == Student.class_id)
        .join(Enrollment, Enrollment.student_id == Student.id)
        .join(Score, Score.enrollment_id == Enrollment.id)
        .where(Student.deleted.is_(False))
        .group_by(Student.id, ClassRoom.id)
        .order_by(ClassRoom.class_name, rank_col)
    )
    rows = db.execute(stmt).mappings().all()
    return rows


@app.get(
    "/api/audit-logs",
    response_model=list[AuditLogOut],
    dependencies=[Depends(require_role("admin"))],
    summary="审计日志查询",
    tags=["审计"],
)
def list_audit_logs(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = (
        select(AuditLog)
        .order_by(AuditLog.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return db.scalars(stmt).all()
