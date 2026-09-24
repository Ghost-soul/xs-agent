# Docker 部署说明

服务器部署按 [仓库 README](../README.md) 操作，从 GHCR 拉取 `ghcr.io/ghost-soul/xs-agent:latest`，使用根目录 `docker-compose.yml` 启动。应用镜像包含网页和后端，PostgreSQL 为独立容器。支持 Linux x86_64 / amd64。

## 初始化与运行资料

`python3 deploy/configure.py` 在服务器生成随机密码和 `deploy/local/deployment.env`。默认账号为 `author`，网页登录密码在 `deploy/local/secrets/web_password`。已有配置时拒绝覆盖，更新时沿用原文件。

根目录 [`.env.example`](../.env.example) 提供 Compose 参数参考。推荐在生成的 `deployment.env` 中按需追加参数；生成的 `NOVEL_WRITER_SECRET_DIR` 为服务器绝对路径，沿用该值即可。若使用根目录 `.env`，先运行初始化脚本生成密码，再复制模板并编辑；不要同时混用两份环境文件。密码必须保留在 secrets 文件中，API Key 在登录网页后配置。

| 参数 | 默认值 / 用途 |
| --- | --- |
| `NOVEL_WRITER_STACK` | `novel-writer-server`，决定数据卷和网络名称；更新时保持一致 |
| `NOVEL_WRITER_IMAGE` | `ghcr.io/ghost-soul/xs-agent:latest`，根目录 Compose 的发布镜像 |
| `NOVEL_WRITER_PUBLIC_ORIGIN` | `http://localhost:8080`，必须与浏览器实际来源一致，不含末尾 `/` |
| `NOVEL_WRITER_BIND` / `NOVEL_WRITER_HTTP_PORT` | `127.0.0.1` / `8080`，宿主机监听地址和端口 |
| `NOVEL_WRITER_WEB_USER` | `author`，网页登录用户名 |
| `NOVEL_WRITER_DATABASE_NAME` | `novel_writer`，首次初始化后的更新不要随意更改 |
| `NOVEL_WRITER_SECRET_DIR` | 初始化脚本生成的 secrets 绝对目录；无需把密码写进环境文件 |
| `NOVEL_WRITER_GENERATION_SHUTDOWN_GRACE_SECONDS` | `300`，在途响应退出等待秒数 |
| `NOVEL_WRITER_STOP_GRACE_PERIOD` | `360s`，容器退出宽限时间，至少比上述等待多 60 秒 |

修改访问端口时同时调整 `NOVEL_WRITER_PUBLIC_ORIGIN`。SSH 隧道两端端口不同时，来源以浏览器访问的本地地址为准。预编译 GHCR 镜像目前仅提供 `linux/amd64`，不能当作原生 ARM64 镜像使用。

- `application-data` 保存新服务器上的正文、故事资料、模型配置和私有凭据。
- `database-data` 保存新 PostgreSQL 数据库。
- `application-logs` 保存服务器日志。
- `deploy/local/secrets/` 保存数据库和网页登录密码，以只读文件挂载进入容器。

镜像和公开源码不含这些运行资料，也不含本机原小说、模型 Key、账单、历史调用、参考语料或备份。API Key 登录网页后重新配置。旧作品导入是独立操作。

应用以非 root UID 10001 运行，根文件系统只读；数据库端口不对外发布。默认应用仅监听本机，远程个人访问使用 SSH 隧道。域名访问配置 HTTPS 反向代理并保留 Host 和 Origin。内部 API 令牌仅保留在服务器，不写入浏览器脚本。

应用和数据库的 Docker 标准输出日志均配置为每个容器最多 3 个 10 MB 文件；应用数据卷中的 `system.log` 另有自身轮转。镜像自带就绪健康检查，Compose 同样配置了检查；一次性迁移容器关闭该检查。

## 停止与迁移

确认创作暂停、没有在途调用后再停止应用。Compose 默认允许 360 秒退出，当前响应最多等待 300 秒；未知结果不会自动重发或增加模型费用。

若调整 `NOVEL_WRITER_GENERATION_SHUTDOWN_GRACE_SECONDS`，同步将 `NOVEL_WRITER_STOP_GRACE_PERIOD` 设为至少多出 60 秒，例如 `660s`。

拉取镜像后，迁移命令 `docker compose --env-file deploy/local/deployment.env run --rm --pull never migrate` 是显式维护操作；已有应用在线持有存储锁时会拒绝执行。停止或重建容器保留数据卷，正常维护不要使用 `down -v`。

## 镜像版本与更新

`NOVEL_WRITER_IMAGE` 指向 GHCR 中的应用镜像。`latest` 跟随发布更新；需要固定版本时改为 `ghcr.io/ghost-soul/xs-agent:sha-<完整提交 SHA>` 或镜像摘要。

每次更新先同步仓库中的部署文件并执行 `docker compose --env-file deploy/local/deployment.env pull`，镜像下载完成后再停止应用、迁移并启动，可减少停机时间。README 的迁移及启动命令使用 `--pull never`，确保两步使用刚刚拉取的同一镜像；下一次更新仍先显式执行 `pull`。Compose 中 `pull_policy: missing` 对浮动 `latest` 标签的行为见 [Docker Compose 文档](https://docs.docker.com/reference/compose-file/services/#pull_policy)。

Dockerfile 和 CI 白名单脚本用于 GitHub Actions 发布镜像。构建缓存采用 [uv 官方建议](https://docs.astral.sh/uv/guides/integration/docker/#caching)，缓存不会进入最终运行镜像。

## 活动卡库与可选分词器

活动题材与叙事卡编入镜像，修改后通过 CI 发布新镜像，服务器拉取更新。没有打包本机分词资产；默认使用明确标注的 UTF-8 字节保守上界，精确分词需在数据卷的 `tokenizers/` 中另行安装可信文件及来源／SHA 清单，应用不会自动下载。
