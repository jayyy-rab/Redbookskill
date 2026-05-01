# 环境：Windows / PowerShell（避免命令对了跑不起来）

- **链式命令**：PowerShell 5.x 中请勿使用 `cmd` 的 `&&` 串联；请用分号 `;`，或拆成多条命令。
- **路径**：含空格的路径必须加双引号；产品图、标题文件等优先给**绝对路径**。
- **写 UTF-8 文件**（替代 bash 的 `printf`）示例：

```powershell
Set-Content -Path "C:\temp\title.txt" -Encoding utf8 -Value "标题"
Set-Content -Path "C:\temp\content.txt" -Encoding utf8 -Value "正文多行`n第二行"
```
