# 茶叶 + 产品图：搜参考 → Picset → PS 调色 → acc_a / acc_b 预览填表（不点「发布」）
# 在项目根目录: powershell -ExecutionPolicy Bypass -File scripts/examples/run_bulk_tea_two_accounts.ps1
param(
    [Parameter(Mandatory = $false)]
    [string] $ProductImage = "C:\Users\lyh17\.cursor\projects\c-Users-lyh17-agents-skills-redbookskills\assets\c__Users_lyh17_AppData_Roaming_Cursor_User_workspaceStorage_d82650457fec83d5627abad5326d4d31_images______2026-04-28_091505-a4fab025-ecf9-463c-8392-619577686004.png"
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\")
Set-Location $root.Path
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"
$log = Join-Path $root.Path "tmp\tea_bulk_last_run.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

Write-Host "[run] log -> $log" -ForegroundColor Cyan

python -u scripts/bulk_publish_accounts.py `
  --product-images $ProductImage `
  --seed-keyword "茶叶" `
  --accounts acc_a acc_b `
  --photoshop-after-generate `
  --no-reuse-last-generated-for-prepare `
  --restart-browser-for-account `
  --reference-count 4 `
  --max-download 1 `
  --picset-batch-size 1 `
  --step-timeout-seconds 5400 `
  --preview `
  --retries 1 `
  2>&1 | Tee-Object -FilePath $log

exit $LASTEXITCODE
