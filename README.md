# Redbookskill

Redbookskill 是一个用于小红书（Xiaohongshu / RED）内容发布与运营自动化的开源工具集。

## 功能概览
- 自动发布图文内容（标题、正文、图片）
- 多账号与登录状态管理
- 基于 CDP 的浏览器自动化执行
- 内容检索、评论互动与基础数据抓取

## 环境要求
- Python 3.10+
- Google Chrome
- Windows（当前主要在 Windows 环境验证）

## 安装
```bash
pip install -r requirements.txt
```

## 快速开始
```bash
# 首次登录
python scripts/cdp_publish.py login

# 检查登录
python scripts/cdp_publish.py check-login

# 发布示例
python scripts/cdp_publish.py publish --title "标题" --content-file content.txt --images image1.jpg,image2.jpg
```

## 目录结构
- `scripts/`：核心脚本与命令入口
- `config/`：运行配置（注意不要提交敏感信息）
- `tests/`：测试用例
- `docs/`：补充文档

## 注意事项
- 请勿提交账号 Cookie、密钥、个人隐私数据。
- 建议先在测试账号或沙盒场景验证发布流程。

## 许可证
本项目采用 `LICENSE` 文件中定义的开源许可证。
