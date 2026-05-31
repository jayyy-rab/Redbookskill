"""
FastAPI 包装层 — 把小红书发布 Skill 变成远程 API。

启动:
  python scripts/api.py

客户调用:
  curl -X POST http://127.0.0.1:8000/run \\
    -H "X-API-Key: <你的 Key>" \\
    -H "Content-Type: application/json" \\
    -d '{"keyword": "茶叶", "product_name": "...", "mode": "preview"}'

依赖:
  pip install fastapi uvicorn pydantic
"""

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── 项目路径 ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# ── API 密钥（必须改成随机字符串） ─────────────────────────
# 生产环境建议从环境变量读取:
#   import os; API_KEY = os.environ.get("API_KEY", "fallback-dev-key")
API_KEY = os.environ.get("API_KEY", "2ce4e9d5364b003b2aa90eb85aa94694172f9595ce4bd2f43f4a89c83a36de3e")

# 多客户 Key 支持
CLIENT_KEYS: dict[str, str] = {
    # "client_name": "their-secret-key",
}

# ── 全局任务状态存储 ──────────────────────────────────────
# 键 = task_id, 值 = {"status": ..., "result": ..., "error": ...}
_tasks: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

app = FastAPI(
    title="XHS Publish API",
    description="小红书全自动发布系统 — HTTP 接口",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 请求 / 响应模型 ───────────────────────────────────────


class RunRequest(BaseModel):
    """创建任务请求"""
    keyword: str = ""
    product_name: str = ""
    product_id: str = ""
    image_paths: list[str] = []
    accounts: list[str] = ["runner"]
    mode: str = "preview"
    allow_live_publish: bool = False
    brief: str = ""


class RunResponse(BaseModel):
    """创建任务响应"""
    task_id: str
    status: str
    message: str
    endpoint: str = ""


class TaskStatusResponse(BaseModel):
    """任务状态查询响应"""
    task_id: str
    status: str
    result: Any = None
    error: str = ""


# ── Token 验证 ───────────────────────────────────────────


def _verify(x_api_key: str | None) -> str:
    if not x_api_key:
        raise HTTPException(401, "缺少 X-API-Key 请求头")
    if x_api_key == API_KEY:
        return "admin"
    for name, key in CLIENT_KEYS.items():
        if x_api_key == key:
            return name
    raise HTTPException(403, "API Key 无效")


def _gen_task_id() -> str:
    return f"task_{int(time.time() * 1000)}"


# ── 后台任务执行 ─────────────────────────────────────────


def _run_skill(task_id: str, req: RunRequest) -> None:
    """在后台线程中执行 pipeline。"""
    try:
        from workflow_orchestrator import run_task
        from workflow_core import TaskInput

        ti = TaskInput(
            task_id=task_id,
            product_images=req.image_paths,
            seed_keyword=req.keyword,
            product_name=req.product_name,
            product_id=req.product_id,
            brief=req.brief,
            accounts=req.accounts,
            publish_mode=req.mode,
            allow_live_publish=req.allow_live_publish,
        )
        result = run_task(ti, run_root=str(REPO_ROOT / "runs"))
        with _lock:
            _tasks[task_id] = {
                "status": "completed",
                "result": result.to_dict() if hasattr(result, "to_dict") else str(result),
                "error": "",
            }
    except Exception as e:
        with _lock:
            _tasks[task_id] = {
                "status": "failed",
                "result": None,
                "error": f"{type(e).__name__}: {e}",
            }


# ── API 端点 ─────────────────────────────────────────────


@app.get("/")
def root():
    return {
        "service": "XHS Publish API",
        "version": "1.0.0",
        "endpoints": {
            "POST /run": "创建发布任务",
            "GET /task/{task_id}": "查询任务状态",
            "GET /health": "健康检查",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/run", response_model=RunResponse)
def run(req: RunRequest, x_api_key: str = Header(None, alias="X-API-Key")):
    """创建发布任务（后台执行，返回 task_id 后即可轮询状态）。"""
    caller = _verify(x_api_key)
    task_id = _gen_task_id()

    with _lock:
        _tasks[task_id] = {"status": "running", "result": None, "error": ""}

    t = threading.Thread(target=_run_skill, args=(task_id, req), daemon=True)
    t.start()

    return RunResponse(
        task_id=task_id,
        status="running",
        message=f"任务已提交（调用者: {caller}）",
        endpoint=f"/task/{task_id}",
    )


@app.get("/task/{task_id}", response_model=TaskStatusResponse)
def get_task(task_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """查询任务状态和结果。"""
    _verify(x_api_key)
    with _lock:
        entry = _tasks.get(task_id)
    if entry is None:
        raise HTTPException(404, f"任务不存在: {task_id}")
    return TaskStatusResponse(task_id=task_id, **entry)


@app.get("/tasks")
def list_tasks(x_api_key: str = Header(None, alias="X-API-Key")):
    """列出所有任务摘要。"""
    _verify(x_api_key)
    with _lock:
        return {
            "count": len(_tasks),
            "tasks": [
                {"task_id": tid, "status": t["status"]}
                for tid, t in _tasks.items()
            ],
        }


# ── 启动 ─────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    key_display = API_KEY[:8] + "..." if len(API_KEY) > 8 else API_KEY
    print(f"API 启动 → http://127.0.0.1:{port}")
    print(f"API Key: {key_display}")
    print(f"帮助:  curl http://127.0.0.1:{port}/")
    print(f"运行:  curl -X POST http://127.0.0.1:{port}/run ...")
    uvicorn.run(app, host="0.0.0.0", port=port)
