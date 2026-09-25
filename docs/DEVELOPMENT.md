# 开发与验证

运行环境为 Python 3.12、Node.js 22、pnpm 10.13.1、uv 0.8.x 和 PostgreSQL 17。依赖由 `uv.lock` 与 `frontend/pnpm-lock.yaml` 锁定。

## 本地开发

根目录服务器 Compose 和开发数据库配置分开。开发数据库命令为 `docker compose -f compose.dev.yaml up -d --wait database`，仅监听本机端口；其中的默认密码仅用于隔离的本地开发。

Windows 可执行 `scripts/setup.ps1` 后运行 `scripts/start.ps1`。脚本使用 `compose.dev.yaml`，与完整服务器部署分开。启动脚本和运行文件只用于本机，不应提交。

Linux 安装依赖与静态验证示例：

```sh
uv sync --frozen --extra rag
pnpm --dir frontend install --frozen-lockfile
uv run pytest tests/unit -m 'not integration'
pnpm --dir frontend test
pnpm --dir frontend typecheck
pnpm --dir frontend build
uv run python scripts/check-doc-consistency.py
uv run python scripts/check-unused-modules.py
```

测试文件只含合成样本。集成测试必须显式配置 `NOVEL_WRITER_TEST_DATABASE_URL` 且实际库名以 `_test` 结尾，不可指向已有小说数据库；`NOVEL_WRITER_INTEGRATION_ZERO_SKIP=1` 启用零跳过门禁。Windows 的完整检查入口为 `scripts/test.ps1`。部分原有代码仍有独立的类型或格式检查遗留问题，镜像构建通过不代表全部历史门禁已通过。

## 公共源码边界

公开快照包含当前源代码、测试、活动卡库、迁移及通用配置，不上传本机协作记忆、事故与运行报告、数据库内容或旧 Git 历史。公开版文档检查验证公开文件链接和迁移链，不依赖本机维护记录。

仓库 Alembic head：`20260925_0048`

提交前可运行 `scripts/check-public-snapshot.ps1` 检查禁止路径和常见密钥格式。不要向版本控制添加生成的服务器配置、API Key、小说或数据库备份。安全边界见 [安全说明](../SECURITY.md)。
