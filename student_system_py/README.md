# Student System (Python / FastAPI)

这是一个可直接演示的“学生教务管理系统”后端，包含：

- 学生/班级/课程 CRUD
- 选课、成绩录入
- 班级排名报表（窗口函数）
- 软删除 + 乐观锁
- 审计日志
- 简单角色控制（`X-Role` 请求头）

## 1) 安装

```bash
cd student_system_py
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 2) 启动

```bash
uvicorn app.main:app --reload --port 8000
```

打开文档：

- Swagger: <http://127.0.0.1:8000/docs>

## 3) 初始化演示数据

```bash
python -m app.seed
```

## 4) 权限头（演示用）

- 教师/管理员接口需要带请求头：
  - `X-Role: teacher` 或 `X-Role: admin`
- 审计接口需要：
  - `X-Role: admin`
- 审计写入人可传：
  - `X-User-Id: 1001`

## 5) MySQL 切换

默认 SQLite。切换 MySQL：

```bash
set DATABASE_URL=mysql+pymysql://root:123456@127.0.0.1:3306/student_system?charset=utf8mb4
```

然后安装驱动：

```bash
pip install pymysql
```

