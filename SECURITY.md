# 安全说明

本项目会处理 API Key、小说正文、Provider 原始响应、费用记录和本地数据库。公开仓库只应包含源码、
通用配置样例、测试和不含运行数据的文档。

## 密钥保存

- API Key 通过工作台配置；Windows 使用 Credential Manager，Linux / Docker 使用私有持久文件（0600）。
- 不要把真实密钥写入 `.env`、Provider Profile JSON、测试、日志、截图、Issue 或提交信息。
- `.env.example` 只能保存空值和本地开发占位值。真实 `.env` 已被 `.gitignore` 排除。
- `.runtime/local-token` 是启动时生成的本地令牌，也不得上传。

## 不应公开的内容

- `data/`、`logs/`、`backups/`、`exports/`、数据库 dump 和系统 archive；
- 小说正文、参考语料、未采用候选、Provider 原始响应和提示词运行载荷；
- 本机绝对路径、Windows 用户名、运行 ID、供应商请求 ID、账单明细和事故记录；
- `.env`、私钥、证书、Cookie、Authorization Header 和 Credential Manager 导出。

提交前运行：

```powershell
./scripts/check-public-snapshot.ps1
```

该脚本执行高置信度密钥格式、本机用户路径和禁止跟踪路径检查。它不能替代人工复核，也不能证明 Git
历史从未包含敏感内容。

## 密钥疑似泄露

1. 立即在供应商侧撤销或轮换密钥，不要等待仓库清理完成。
2. 检查 Provider 账单和访问日志，确认是否存在异常调用。
3. 从 Windows Credential Manager 删除旧凭据并保存新密钥。
4. 在推送前重写包含密钥的 Git 历史；只删除当前文件不足以清除旧提交。
5. 已经公开时，使用 GitHub Private Vulnerability Reporting（若仓库已启用）联系维护者，不要把密钥贴入公开 Issue。

## 产品安全边界

- 配置 Profile、保存密钥和本地预检本身不会调用 Provider。
- 一键运行只消费不可变授权信封中的有限、一次性槽位，并在作者审核门停止。
- 失败重试、结果不确定、可能重复计费、扩容、换模型和正式合并需要独立作者确认。
- Provider 响应和正式故事状态采用不可变记录；恢复失败不得静默外发或自动重试。
