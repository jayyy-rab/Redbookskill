# 02 — 统一函数命名字典

所有核心步骤函数必须固定命名，AI 不准乱改。

## 编排函数

```python
def run_task(task_input: TaskInput, *, run_root: str | Path = "") -> TaskResult
def run_account(ctx: PipelineContext) -> AccountResult
```

## 10 步核心步骤

```python
def run_step1_validate_input(ctx: PipelineContext) -> StepResult
def run_step2_search_xhs_keyword(ctx: PipelineContext) -> StepResult
def run_step3_select_reference_post(ctx: PipelineContext) -> StepResult
def run_step4_generate_image(ctx: PipelineContext) -> StepResult
def run_step5_adjust_image_color(ctx: PipelineContext) -> StepResult
def run_step6_generate_copywriting(ctx: PipelineContext) -> StepResult
def run_step7_open_creator_page(ctx: PipelineContext) -> StepResult
def run_step8_fill_publish_form(ctx: PipelineContext) -> StepResult
def run_step9_attach_product(ctx: PipelineContext) -> StepResult
def run_step10_preview_or_publish(ctx: PipelineContext) -> StepResult
```

## 工具函数

```python
def load_input(input_path: str) -> TaskInput
def build_context(task_input: TaskInput, account_id: str) -> PipelineContext
def ensure_task_dirs(ctx: PipelineContext) -> None
def save_step_result(ctx: PipelineContext, result: StepResult) -> None
def save_bug(ctx: PipelineContext, bug: BugRecord) -> None
def save_screenshot(ctx: PipelineContext, step_id: str, name: str, png_bytes: bytes | None = None) -> str
def save_task_result(ctx: PipelineContext, result: TaskResult) -> None
def read_task_result(task_dir: str | Path) -> dict[str, Any] | None
```

## 规则

1. 所有函数必须接收 `ctx`，禁止散传 `product_img`、`keyword`、`account`、`output_dir`
2. 禁止每个步骤自己重新定义路径
3. 禁止每个步骤自己重新定义变量名
4. 工具函数放在 `workflow_io.py`，步骤函数放在 `workflow_steps.py`，编排器放在 `workflow_orchestrator.py`
