# 多账号与 CDP（切换 · 多端 · 并排登录）

```bash
python scripts/cdp_publish.py list-accounts
python scripts/cdp_publish.py add-account work --alias "工作号"
python scripts/cdp_publish.py --port 9223 --account work login
python scripts/publish_pipeline.py --port 9223 --account work --headless --title-file /abs/path/title.txt --content-file /abs/path/content.txt --image-urls "https://example.com/1.jpg"
```

**为何会「只看到一只 Chrome」：** 流水线（含 `bulk_publish_accounts` → `publish_pipeline --restart-browser-for-account`）通常**同一时间只起一个 CDP**，换号时会杀进程再起，所以任务栏不会像日常那样长期叠着两只独立窗口。**双号各自登录预览**时请先用：

```powershell
# 已为 acc_a / acc_b 等在 config\accounts.json 写入互不相同的 `"port"` 后：
python scripts/start_multi_chrome_accounts.py --accounts acc_a acc_b
```

会为每个端口各起一个 **`--remote-debugging-port` + 独立 `--user-data-dir`** 的 Chrome；随后在**对应窗口**里分别打开小红书 / Picset 完成登录。**任务栏可能被折叠**：点 Chrome 图标可展开多窗口；或 **Alt+Tab**。**未配置 `port`** 的账号不会被该脚本拉起，请 `python scripts/account_manager.py update 账号 --port 9xxx`。发布阶段仍由各脚本按 `--account` + `--port` 指向正确实例。
