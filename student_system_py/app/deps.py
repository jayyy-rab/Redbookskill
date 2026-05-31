from __future__ import annotations

from typing import Generator
from fastapi import Header, HTTPException
from sqlalchemy.orm import Session
from .database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_actor_id(x_user_id: int | None = Header(default=None)) -> int | None:
    return x_user_id


def require_role(required: str):
    def checker(x_role: str | None = Header(default=None)) -> str:
        if x_role is None:
            raise HTTPException(status_code=401, detail="缺少请求头：X-Role")
        if x_role.lower() not in {required.lower(), "admin"}:
            raise HTTPException(status_code=403, detail=f"权限不足，需要角色：{required}")
        return x_role

    return checker
