# Novel Writer / xs-agent

面向个人使用的小说创作与管理工作台。React 前端、FastAPI 后端、PostgreSQL 数据库；支持作品管理、题材与叙事卡、有限阶段创作、失败检查点恢复、阅读、导出和备份。

## Docker 部署

服务器只需要 **`docker-compose.yml` 和 `.env` 两个文件**，以及 Docker Engine 和当前版本的 Docker Compose 插件。支持 Linux x86_64 / amd64。应用镜像从 GHCR 拉取，数据库镜像从 Docker Hub 拉取。

1. 将本仓库的 [docker-compose.yml](docker-compose.yml) 放入服务器上的部署目录。
2. 将 [.env.example](.env.example) 的内容保存为同目录下的 `.env`。
3. 填写 `NOVEL_WRITER_DATABASE_PASSWORD` 和 `NOVEL_WRITER_WEB_PASSWORD`，使用两个不同的随机密码，每个至少 24 字符，推荐 32 字符以上。根据实际访问地址修改 `NOVEL_WRITER_PUBLIC_ORIGIN`。
4. 在该目录运行：

```sh
chmod 600 .env
docker compose pull
docker compose up -d --wait
docker compose ps
```

Compose 自动创建持久数据卷、初始化 PostgreSQL，并在数据库健康后运行数据库迁移、启动应用。密码从 `.env` 注入容器 secrets。全部启动步骤在容器中完成。

默认网页登录用户为 `author`，密码就是 `.env` 中的 `NOVEL_WRITER_WEB_PASSWORD`。首次部署为空白实例，登录后在网页中配置模型供应商和 API Key。

## Prompt 模板与知识检索

在「高级设置 → Prompt 模板」编辑各角色系统指导和任务结构，点击「保存为默认」。保存区会显示成功、失败或占位符问题；修改只影响后续新预览和新独立修订，已保存批次继续使用原合同。Chief 结合叙事卡设计完整事件，Writer 按当前有效单元执行；Memory 负责事实接力，Checker 可选，新阶段不提供 Reader。

模板属于私有运行设置，存于 `/app/data/prompt-templates/`，与小说和密钥一样使用持久卷，仓库与镜像不携带本机的自定义内容。迁移已有模板时，备份并单独迁移该目录；仅更新镜像不会把另一台机器的模板带过来。

知识检索按作品和版本隔离，使用 PostgreSQL / pgvector；镜像包含 CPU 推理依赖，但不包含本地模型权重和知识索引。未安装模型时保留精确／词法检索。模型配置和升级说明见 [知识检索](docs/KNOWLEDGE.md)。

## 访问地址

默认只监听服务器 `127.0.0.1:8080`。在自己的电脑建立 SSH 隧道：

```sh
ssh -N -L 8080:127.0.0.1:8080 <用户>@<服务器>
```

浏览器打开 `http://localhost:8080`。其他访问方式在 `.env` 中调整：

- 直接使用服务器 IP：设置 `NOVEL_WRITER_BIND=0.0.0.0`、`NOVEL_WRITER_PUBLIC_ORIGIN=http://服务器IP:8080`，并在防火墙放行对应端口。
- 使用 HTTPS 域名：设置 `NOVEL_WRITER_PUBLIC_ORIGIN=https://你的域名`，由反向代理转发至应用端口并保留 Host 和 Origin。公网访问建议采用这种方式。

`NOVEL_WRITER_PUBLIC_ORIGIN` 必须与浏览器地址的协议、主机和端口一致，不含末尾 `/`。修改宿主机端口时同步调整访问地址。

## 更新与数据

确认创作已暂停、没有在途调用后执行：

```sh
docker compose pull
docker compose up -d --pull never --wait
```

应用容器重建时自动迁移，随后启动网页和后端。若上游修改了 Compose 参数，先同步最新的 `docker-compose.yml`，保留自己的 `.env`。

```sh
# 查看日志
docker compose logs --tail=100 app

# 停止应用和数据库，保留数据
docker compose stop
```

作品、模型配置、私有凭据、数据库和日志使用持久数据卷。保留原来的 `NOVEL_WRITER_STACK`、数据库名称和数据库密码；更改项目名会使用另一组数据卷。`docker compose down -v` 会删除数据卷，不用于正常停止或更新。

## 镜像发布

应用镜像为 `ghcr.io/ghost-soul/xs-agent:latest`。固定版本可在 `.env` 中将 `NOVEL_WRITER_IMAGE` 改为 `ghcr.io/ghost-soul/xs-agent:sha-<完整提交 SHA>` 或镜像摘要。

GitHub Actions 在 `main` 分支相关源码变更后构建并发布镜像，也可手动触发 `Publish Docker image`。CI 使用白名单构建目录和 `.dockerignore` 过滤构建输入，多阶段构建仅将生产依赖、前端静态产物、应用源码、迁移和活动卡库放入运行镜像。

- [部署参数与运行说明](deploy/README.md)
- [开发环境与测试](docs/DEVELOPMENT.md)
- [架构](docs/ARCHITECTURE.md)
- [安全说明](SECURITY.md)

`compose.dev.yaml` 用于源码开发时的独立数据库，开发环境见上述开发文档。
