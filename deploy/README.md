# Docker 部署说明

首次在线部署按 [仓库 README](../README.md) 操作，使用 `ghcr.io/ghost-soul/xs-agent:latest`。应用包含网页和后端，PostgreSQL 为独立容器。支持 Linux x86_64 / amd64。

## 初始化与运行资料

`python3 deploy/configure.py` 在服务器生成随机密码和 `deploy/local/deployment.env`。默认账号为 `author`，网页登录密码在 `deploy/local/secrets/web_password`。已有配置时拒绝覆盖，更新时沿用原文件。

- `application-data` 保存新服务器上的正文、故事资料、模型配置和私有凭据。
- `database-data` 保存新 PostgreSQL 数据库。
- `application-logs` 保存服务器日志。
- `deploy/local/secrets/` 保存数据库和网页登录密码，以只读文件挂载进入容器。

镜像和公开源码不含这些运行资料，也不含本机原小说、模型 Key、账单、历史调用、参考语料或备份。API Key 登录网页后重新配置。旧作品导入是独立操作。

应用以非 root UID 10001 运行，根文件系统只读；数据库端口不对外发布。默认应用仅监听本机，远程个人访问使用 SSH 隧道。域名访问配置 HTTPS 反向代理并保留 Host 和 Origin。内部 API 令牌仅保留在服务器，不写入浏览器脚本。

## 停止与迁移

确认创作暂停、没有在途调用后再停止应用。Compose 默认允许 360 秒退出，当前响应最多等待 300 秒；未知结果不会自动重发或增加模型费用。

若调整 `NOVEL_WRITER_GENERATION_SHUTDOWN_GRACE_SECONDS`，同步将 `NOVEL_WRITER_STOP_GRACE_PERIOD` 设为至少多出 60 秒，例如 `660s`。

迁移命令 `docker compose --env-file deploy/local/deployment.env run --rm migrate` 是显式维护操作；已有应用在线持有存储锁时会拒绝执行。停止或重建容器保留数据卷，正常维护不要使用 `down -v`。

## 初始离线镜像包

[下载初始 ZIP](https://github.com/Ghost-soul/xs-agent/releases/download/docker-2026.09.25/novel-writer-docker-20260925.zip)，SHA-256：

```text
8dc6a42f9211ff48133068c5d943ee70aa470d7ac00582688415ca9efab7790d
```

在已克隆的仓库目录中执行：

```sh
unzip novel-writer-docker-20260925.zip novel-writer-images.tar.gz
docker load -i novel-writer-images.tar.gz
python3 deploy/configure.py
export NOVEL_WRITER_IMAGE=novel-writer:2026.09.24
docker compose --env-file deploy/local/deployment.env up -d database
docker compose --env-file deploy/local/deployment.env run --rm migrate
docker compose --env-file deploy/local/deployment.env up -d app
```

离线包是固定的初始版本；如需后续更新，使用 GHCR 或重新构建。长期离线使用时，将生成的 `deployment.env` 中 `NOVEL_WRITER_IMAGE` 改为 `novel-writer:2026.09.24`，后续终端不需要再次 export。

## 源码与可选分词器

从源码构建优先使用 README 中的白名单打包方式。也可显式执行 `docker compose --env-file deploy/local/deployment.env -f deploy/compose.yaml build app`；该文件保留源码构建配置，根目录 Compose 用于拉取和部署。

活动题材与叙事卡编入镜像，修改后重新构建。没有打包本机分词资产；默认使用明确标注的 UTF-8 字节保守上界，精确分词需在数据卷的 `tokenizers/` 中另行安装可信文件及来源／SHA 清单，应用不会自动下载。
