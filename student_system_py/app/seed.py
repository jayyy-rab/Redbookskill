from __future__ import annotations

from .database import SessionLocal, Base, engine
from .models import ClassRoom, Course, Student, Gender, StudentStatus


def run_seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(ClassRoom).count() == 0:
            c1 = ClassRoom(class_code="CS2401", class_name="计科2401", grade_year=2024, major="计算机科学")
            c2 = ClassRoom(class_code="SE2401", class_name="软工2401", grade_year=2024, major="软件工程")
            db.add_all([c1, c2])
            db.flush()
            db.add_all(
                [
                    Student(
                        student_no="20240001",
                        name="张三",
                        gender=Gender.M,
                        class_id=c1.id,
                        status=StudentStatus.ACTIVE,
                    ),
                    Student(
                        student_no="20240002",
                        name="李四",
                        gender=Gender.F,
                        class_id=c1.id,
                        status=StudentStatus.ACTIVE,
                    ),
                ]
            )
            db.add_all(
                [
                    Course(course_code="DB001", course_name="数据库系统", credit=3.0, teacher_name="王老师", semester="2026-Spring"),
                    Course(course_code="PY101", course_name="Python程序设计", credit=2.0, teacher_name="刘老师", semester="2026-Spring"),
                ]
            )
            db.commit()
            print("seed done")
        else:
            print("seed skipped: data exists")
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()

