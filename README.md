# Novel Writer / xs-agent

面向个人使用的小说创作与管理工作台。React 前端、FastAPI 后端、PostgreSQL 数据库；支持作品管理、题材与叙事卡、有限阶段创作、事实接力、失败检查点恢复、阅读、导出和备份。

本仓库包含当前应用源码、前后端测试、活动卡库、数据库迁移、Docker 构建与部署配置。公开源码快照不包含开发机器的 API Key、小说、个人设置、运行报告、协作记忆或原仓库 Git 历史。首次部署为空白实例，供应商和 API Key 在网页中重新配置。

## Docker 快速部署

适用 Linux x86_64 / amd64。服务器需要 Docker Engine、Compose v2、Python 3 和 Git。

```sh
git clone https://github.com/Ghost-soul/xs-agent.git
cd xs-agent
python3 deploy/configure.py
docker compose --env-file deploy/local/deployment.env pull
docker compose --env-file deploy/local/deployment.env up -d database
docker compose --env-file deploy/local/deployment.env run --rm migrate
docker compose --env-file deploy/local/deployment.env up -d app
docker compose --env-file deploy/local/deployment.env ps
```

应用镜像：`ghcr.io/ghost-soul/xs-agent:latest`。每次源码构建另发布 `sha-<完整提交 SHA>` 标签；需要固定版本时，将 `deploy/local/deployment.env` 中的 `NOVEL_WRITER_IMAGE` 改为该标签或 `ghcr.io/ghost-soul/xs-agent@sha256:<镜像摘要>`。

`configure.py` 只用于首次初始化，在本机随机生成数据库和网页登录密码，已有配置时拒绝覆盖。`deploy/local/` 已被 Git 忽略，不能上传或分享。数据库迁移需显式执行，普通启动不会自动迁移。

默认网页登录账号 `author`，服务器上查看密码：

```sh
cat deploy/local/secrets/web_password
```

应用默认仅监听服务器 `127.0.0.1:8080`。在自己的电脑建立 SSH 隧道：

```sh
ssh -N -L 8080:127.0.0.1:8080 <用户>@<服务器>
```

在电脑浏览器打开 `http://localhost:8080` 并登录。需要域名访问时，首次配置使用 `python3 deploy/configure.py --origin https://你的域名`，由同机 HTTPS 反向代理转发到 `127.0.0.1:8080`，保留 Host 和 Origin。已有实例修改 `deployment.env` 的网站来源，不重新生成密码。

## 更新与数据

```sh
# 查看应用日志
docker compose --env-file deploy/local/deployment.env logs --tail=100 app

# 确认创作暂停且没有在途调用后更新
docker compose --env-file deploy/local/deployment.env stop app
git pull --ff-only
docker compose --env-file deploy/local/deployment.env pull
docker compose --env-file deploy/local/deployment.env run --rm migrate
docker compose --env-file deploy/local/deployment.env up -d app
```

应用数据、数据库和日志各自使用持久数据卷。保留同一份配置和 Compose 项目名；停止或重建容器不会清空数据。`docker compose down -v` 会删除数据卷，不用于正常停止或更新。正式数据迁移和备份还原需另行进行，不会由镜像部署自动执行。

## 从源码构建

```sh
python3 scripts/package_docker.py --output dist/docker-source
docker build -t novel-writer:local dist/docker-source/source
NOVEL_WRITER_IMAGE=novel-writer:local docker compose --env-file deploy/local/deployment.env run --rm migrate
NOVEL_WRITER_IMAGE=novel-writer:local docker compose --env-file deploy/local/deployment.env up -d app
```

构建输入使用程序白名单和 `.dockerignore` 双重过滤。镜像中包含前后端和活动卡库；不包含数据库、API Key 或服务器运行资料。CI 使用 GitHub Actions 临时令牌构建并推送至 GHCR，不需要在源码或仓库 Secret 中保存个人访问令牌。

## 离线部署与开发

- [Docker 部署细节与离线镜像](deploy/README.md)
- [开发环境与测试](docs/DEVELOPMENT.md)
- [架构](docs/ARCHITECTURE.md)
- [安全说明](SECURITY.md)
- [初始离线部署包](https://github.com/Ghost-soul/xs-agent/releases/tag/docker-2026.09.25)

根目录 `docker-compose.yml` 用于完整服务器部署；`compose.dev.yaml` 仅用于开发数据库，需要显式指定。公开仓库未携带本机维护记录，不能用本次源码发布代替文学效果或正式服务器容量验收。
