# 工程基线规则

本项目所有后续开发必须遵守 `docs/ENGINEERING_FLOW_V1_1.md`。

任何修改 Step1-Step10、ctx、result.json、bugs.json、diagnostics.json、Dify、FastAPI 边界前，必须先读取该文件。

不允许绕过工程图直接改代码。

## 冻结范围

| 项 | 状态 |
|----|------|
| 四层架构（Dify/FastAPI/Worker/Python Steps） | 冻结 |
| Step1-Step10 顺序 | 冻结 |
| 函数签名 `run_stepN_xxx(ctx) -> StepResult` | 冻结 |
| input.json 基础字段 | 冻结 |
| result.json 结构 | 冻结 |
| bugs.json 结构 | 冻结 |
| diagnostics.json 结构 | 冻结 |
| 产物校验规则 | 冻结 |
| 截图规范 | 冻结 |
| retry_policy | 冻结 |
| 代码实现细节 | 不冻结 |

## 变更流程

任何架构变更必须先提交变更单，包含：
1. 为什么改
2. 涉及哪些 Step
3. 影响哪些文件
4. 是否影响 ctx
5. 是否影响 result/bugs/diagnostics
6. 怎么验证
7. 怎么回滚

---
创建时间：2026-05-10
对应文件：docs/ENGINEERING_FLOW_V1_1.md
