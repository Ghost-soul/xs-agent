# Docker 部署：全新空白实例

应用镜像包含前端、后端、当前活动卡库和数据库迁移代码。PostgreSQL 为独立容器，两者由同一
Docker Compose 项目管理。只需 Linux x86_64 服务器、Docker Engine 与 Compose v2；
从源码构建时需要下载依赖。镜像在本地生成，不会自动上传任何镜像仓库。

包内没有本机 `.env`、API Key、Provider 配置、登录令牌、小说、人物／世界资料、历史响应、费用、
参考语料、日志、备份或 Git 历史。启动后数据库为空，模型供应商及 Key 在网页中重新配置。
不要把运行后生成的 `deploy/local/` 或 Docker 数据卷加入可分享部署包。

## 首次启动

在解压后的根目录执行。Python 3 只用于一次性生成服务器配置，不需要安装应用依赖。

```sh
python3 deploy/configure.py
docker compose --env-file deploy/local/deployment.env -f deploy/compose.yaml build app
docker compose --env-file deploy/local/deployment.env -f deploy/compose.yaml up -d database
docker compose --env-file deploy/local/deployment.env -f deploy/compose.yaml run --rm migrate
docker compose --env-file deploy/local/deployment.env -f deploy/compose.yaml up -d app
docker compose --env-file deploy/local/deployment.env -f deploy/compose.yaml ps
```

若已取得配套镜像包，先 `docker load -i novel-writer-images.tar.gz`，并省略上面的 `build app`。
数据库迁移是显式维护步骤，不在每次应用启动时自动执行。应用使用非 root UID 10001，
只读容器根目录、独立可写数据／日志卷；没有对外发布 PostgreSQL 端口。

默认访问地址为 `http://localhost:8080`，账号 `author`，密码保存在
`deploy/local/secrets/web_password`。在服务器终端查看该文件即可登录；浏览器会弹出登录框。
该密码由首次配置时随机生成，与模型 API Key 不同。

从自己电脑访问远程服务器，保持默认本机监听并建立 SSH 隧道：

```sh
ssh -N -L 8080:127.0.0.1:8080 <用户>@<服务器>
```

随后在自己电脑打开 `http://localhost:8080`。如需域名访问，在运行 configure 时指定
`--origin https://你的域名`，由同机 HTTPS 反向代理转发到 `127.0.0.1:8080`，保留原 Host 和 Origin。
浏览器写操作只接受配置的网站 Origin，内部 API 令牌由服务器注入，不进入前端脚本。
默认没有公网监听。改变浏览器地址或端口时需同步修改 `deployment.env` 中的 Origin。

## 数据与更新

- `application-data`：新服务器创建的资料、正文缓冲、模型设置、私有凭据和可选分词资产。
- `database-data`：服务器的新 PostgreSQL 数据库。与开发环境现有 Compose 项目和卷隔离。
- `application-logs`：服务器运行日志。
- `deploy/local/secrets/`：新服务器数据库密码与网页登录密码，通过运行时 secret 文件挂载。

停止或重建容器不会删除这些数据。请勿使用 `docker compose down -v`，它会删除数据卷。
更新镜像时沿用原 `deploy/local/` 配置，不重新生成密码。先确认创作暂停、没有在途调用，
再 `stop app`；必要时显式运行迁移，最后 `up -d app`。Compose 默认允许应用用 360 秒退出，
当前响应最多等待 300 秒；未知结果仍不会自动重发或增加模型费用。
若调整 `NOVEL_WRITER_GENERATION_SHUTDOWN_GRACE_SECONDS`，同步将
`NOVEL_WRITER_STOP_GRACE_PERIOD` 设为至少多出 60 秒的时间，例如 `660s`。

卡库包含在镜像中，修改卡文后重新构建。没有复制本机 `data/tokenizers`；初始使用明确标注的
UTF-8 字节保守上界，若要精确分词，应在服务器数据卷的 `tokenizers/` 安装可信的模型分词文件
和对应来源／SHA 清单。应用不会自动下载分词器。

导入旧作品须另行明确操作，本部署流程不连接、不导出、不迁移原本机数据库。
旧语料绝对路径也不会随镜像带入服务器。

## 从开发仓库重新打包

```sh
python scripts/package_docker.py --output dist/docker-release-new
docker build -t novel-writer:2026.09.24 dist/docker-release-new/source
```

打包工具使用明确的程序文件白名单，输出 `SOURCE-MANIFEST.json` 与源码 ZIP；构建只读取这个
经过筛选的目录。Dockerfile 只复制指定程序资源，`.dockerignore` 另行排除运行数据与密钥。
不要对正在运行且已写入个人数据的容器使用 `docker commit` 来制作分享镜像。
