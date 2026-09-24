# Docker 部署参数

服务器部署只需根目录的 `docker-compose.yml` 和由 `.env.example` 填写得到的 `.env`。应用从 GHCR 拉取，数据库初始化和迁移由容器自动执行。完整操作见 [仓库 README](../README.md)。

## 环境配置

| 参数 | 默认值 / 用途 |
| --- | --- |
| `NOVEL_WRITER_IMAGE` | `ghcr.io/ghost-soul/xs-agent:latest`，应用镜像 |
| `NOVEL_WRITER_STACK` | `novel-writer-server`，决定数据卷和网络名称；更新时保持一致 |
| `NOVEL_WRITER_DATABASE_PASSWORD` | 必填，至少 24 字符；数据库初始化后保持不变 |
| `NOVEL_WRITER_WEB_PASSWORD` | 必填，至少 24 字符；网页登录密码，应与数据库密码不同 |
| `NOVEL_WRITER_WEB_USER` | `author`，网页登录用户名 |
| `NOVEL_WRITER_PUBLIC_ORIGIN` | `http://localhost:8080`，必须与浏览器实际来源一致，不含末尾 `/` |
| `NOVEL_WRITER_BIND` / `NOVEL_WRITER_HTTP_PORT` | `127.0.0.1` / `8080`，宿主机监听地址和端口 |
| `NOVEL_WRITER_DATABASE_NAME` | `novel_writer`，数据库初始化后保持不变 |
| `NOVEL_WRITER_GENERATION_SHUTDOWN_GRACE_SECONDS` | `300`，在途响应退出等待秒数 |
| `NOVEL_WRITER_STOP_GRACE_PERIOD` | `360s`，至少比上述等待多 60 秒 |

将真实 `.env` 的权限限制为 0600，不上传或分享。密码建议使用 32 字符以上的随机字母和数字；包含 `$`、`#` 等特殊字符时用单引号包住值，避免被当作变量或注释解释。模型 API Key 登录网页后配置。

Compose 使用 [environment secrets](https://docs.docker.com/reference/compose-file/secrets/) 将 `.env` 中的两个密码挂载为容器内的只读文件。服务器使用 Docker Compose 插件执行部署。

## 启动与更新

```sh
docker compose pull
docker compose up -d --pull never --wait
```

首次启动自动创建数据库。每次应用容器启动时，等待数据库健康，然后执行迁移，成功后启动网页和后端；迁移失败时应用不会带着错误的数据库版本继续运行。重复启动和容器重建沿用现有数据。

更新前先暂停创作并等待在途调用结束。`pull` 下载新镜像后，`up` 重建应用并自动迁移。`--pull never` 让该次启动使用刚拉取到本机的镜像；下次更新仍先执行 `pull`。需要固定版本时将镜像设为提交 SHA 标签或镜像摘要。

## 持久化与运行

- `application-data` 保存正文、故事资料、模型配置、私有凭据和可选分词器。
- `database-data` 保存 PostgreSQL 数据。
- `application-logs` 保存应用日志。

应用以 UID 10001 运行，应用代码归 root 所有；数据库端口不对外发布。Compose 需要可写的容器根文件系统来注入 environment secrets，因此未启用 `read_only`。默认应用仅监听本机。容器日志限制为每个容器最多 3 个 10 MB 文件，应用的 `system.log` 另有自身轮转。

Compose 默认允许 360 秒退出，在途响应最多等待 300 秒。调整响应等待时间时，应将容器宽限时间设为至少多出 60 秒。停止或重建保留数据卷，正常维护不要使用 `down -v`。

活动题材与叙事卡编入镜像，修改后通过 CI 发布新镜像，服务器拉取更新。默认分词使用明确标注的 UTF-8 字节保守上界；精确分词需在数据卷的 `tokenizers/` 中安装可信文件及来源／SHA 清单。
