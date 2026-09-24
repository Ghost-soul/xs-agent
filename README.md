# Novel Writer Docker 部署

适用于 Linux x86_64 / amd64 的全新实例部署。应用镜像包含前后端，PostgreSQL 使用独立容器；由根目录的 [docker-compose.yml](docker-compose.yml) 统一管理。

- [下载离线部署包（约 236 MB）](https://github.com/Ghost-soul/xs-agent/releases/download/docker-2026.09.25/novel-writer-docker-20260925.zip)
- [完整部署说明](deploy/README.md)（其中的构建命令适用于完整解压后的部署包）
- [发布页与附件](https://github.com/Ghost-soul/xs-agent/releases/tag/docker-2026.09.25)

部署包不包含开发机器的 API Key、个人配置、小说数据库、历史响应、费用记录、参考语料、日志、备份或 Git 历史。首次启动是空白实例，模型供应商与 API Key 需重新配置。

## 首次部署

服务器需安装 Docker Engine、Docker Compose v2、Python 3、Git、curl 和 unzip。以下命令在 Linux 服务器执行。

```sh
git clone https://github.com/Ghost-soul/xs-agent.git
cd xs-agent

curl -fL --retry 3 -o novel-writer-docker-20260925.zip https://github.com/Ghost-soul/xs-agent/releases/download/docker-2026.09.25/novel-writer-docker-20260925.zip
echo '8dc6a42f9211ff48133068c5d943ee70aa470d7ac00582688415ca9efab7790d  novel-writer-docker-20260925.zip' | sha256sum --check
unzip novel-writer-docker-20260925.zip novel-writer-images.tar.gz
docker load -i novel-writer-images.tar.gz

python3 deploy/configure.py
docker compose --env-file deploy/local/deployment.env up -d database
docker compose --env-file deploy/local/deployment.env run --rm migrate
docker compose --env-file deploy/local/deployment.env up -d app
docker compose --env-file deploy/local/deployment.env ps
```

校验失败时停止操作，重新下载部署包。应用镜像从 Release 导入，Compose 不会尝试从镜像仓库拉取应用；未导入镜像时会明确报错。数据库迁移是显式维护步骤，不会在每次启动时自动执行。

`configure.py` 仅首次运行，用于随机生成服务器自己的密码；已有配置时会拒绝覆盖。配置目录 `deploy/local/` 已被 Git 忽略，不要上传或分享。

## 登录与远程访问

默认账号为 `author`，在服务器查看新生成的网页登录密码：

```sh
cat deploy/local/secrets/web_password
```

服务默认仅监听服务器的 `127.0.0.1:8080`。在自己的电脑建立 SSH 隧道：

```sh
ssh -N -L 8080:127.0.0.1:8080 <用户>@<服务器>
```

随后在电脑浏览器打开 `http://localhost:8080`，使用上述账号和密码登录。模型 API Key 登录后在网页配置。

需要域名访问时，首次初始化使用 `python3 deploy/configure.py --origin https://你的域名`，由同机 HTTPS 反向代理转发至 `127.0.0.1:8080`，保留 Host 和 Origin。已经初始化时修改 `deploy/local/deployment.env` 中的网站来源，不重新生成密码。

## 停止、再次启动与数据保留

```sh
# 查看应用日志
docker compose --env-file deploy/local/deployment.env logs --tail=100 app

# 确认创作暂停、没有在途调用后停止
docker compose --env-file deploy/local/deployment.env stop

# 再次启动；已有实例不需要重新生成配置或重复初始化
docker compose --env-file deploy/local/deployment.env up -d
```

应用数据、数据库和日志分别使用持久数据卷，普通停止或重建容器会保留。不要使用 `docker compose down -v`，该命令会删除数据卷。保留同一份 `deploy/local/` 配置与 Compose 项目名，以继续使用原数据。

升级时先暂停创作并停止应用，再载入新镜像，必要时显式执行迁移，最后启动应用。根目录 Compose 不包含源码构建配置；需要修改程序或卡文时，解压完整部署包后参考其中的部署说明重新构建。
