# 阅读版 Docker

独立镜像 `ghcr.io/ghost-soul/xs-agent:reader`，直接查询现有创作数据库中的当前正式正文。
提供书架、章节目录、按章加载、正文搜索、上一章/下一章、夜读、字号与行距设置。
数据库更新后点击“刷新内容”；版本在阅读期间改变时明确提示刷新，避免混用不同版本的章节。

仅展示未归档作品的当前正式版本。未采用的阶段草稿、旧版本、世界观、Prompt、调用和费用不在阅读接口中。
没有生成任务、供应商配置、模型 SDK、迁移命令、导入或编辑接口。阅读后端只允许查询；
不保存本地小说副本、书签或阅读历史，不使用数据卷和浏览器本地存储，设置只在当前页面生效。
重新打开时可通过页面 URL 定位作品和章节。

## 与现有创作版部署在同一云服务器

准备 [docker-compose.reader.yml](../docker-compose.reader.yml) 和本目录的
[reader.env.example](reader.env.example)，将后者另存为 `.reader.env`。
运行配置不放进镜像，也不要提交真实密码。

1. 数据库网络默认 `novel-writer-server_default`，即创作版 `.env` 中 `NOVEL_WRITER_STACK` 后加 `_default`。
   若曾自定义项目名，请通过 `docker network ls` 核对并修改 `READER_DATABASE_NETWORK`。
2. `READER_DATABASE_HOST=database` 使用现有数据库容器，数据库名称与创作版一致。
   建议由管理员执行 [grant-reader.sql](grant-reader.sql)，再在 psql 中用 `\password novel_reader`
   交互设置密码；脚本只授予四张表的必要列读取权限，不授予写入或私有状态字段权限。
   本项目不会自动执行该管理操作。也可填写现有数据库账号，程序仍强制只读事务；
   专用只读账号可进一步限制数据库权限。
3. 填写数据库密码及独立的 `READER_WEB_PASSWORD`（至少 24 字符），
   `READER_PUBLIC_ORIGIN` 填浏览器实际访问地址，如 `https://reader.example.com`。
   HTTPS 反向代理转发到 `127.0.0.1:8081` 并保留 Host。
4. 在阅读版部署目录运行：

```sh
chmod 600 .reader.env
docker compose --env-file .reader.env -f docker-compose.reader.yml pull
docker compose --env-file .reader.env -f docker-compose.reader.yml up -d --wait
```

打开配置的网址，用户名默认为 `reader`。只启动阅读容器，不重启创作服务、数据库或执行迁移。
不需要复制小说文件或挂载创作版数据目录，也不要把现有数据库卷挂到第二个 PostgreSQL 容器。

若数据库位于另一台服务器，先在阅读服务器创建专用网络：
`docker network create xs-reader-network`，设置 `READER_DATABASE_NETWORK=xs-reader-network`，
`READER_DATABASE_HOST` 改为可达的数据库地址，并按数据库要求配置 `READER_DATABASE_SSLMODE=require`。
该配置需要数据库已允许阅读服务器连接；阅读容器不会开放或更改数据库网络设置。

## 更新与本地构建

```sh
docker compose --env-file .reader.env -f docker-compose.reader.yml pull
docker compose --env-file .reader.env -f docker-compose.reader.yml up -d --pull never --wait
```

固定版本可将 `READER_IMAGE` 改成 `ghcr.io/ghost-soul/xs-agent:reader-sha-<完整提交 SHA>`。
阅读版标签与创作版完全独立。

源码构建使用白名单目录，仅包含阅读服务、阅读前端、依赖锁定信息和登录网关：

```sh
python scripts/prepare_reader_context.py --output /tmp/xs-reader-build
docker build -f /tmp/xs-reader-build/reader/Dockerfile -t xs-agent-reader:local /tmp/xs-reader-build
```

镜像以 UID/GID `10001:10001` 运行，容器文件系统只读，仅临时目录使用内存。
访问日志关闭，健康检查不返回书名或正文；数据库故障只返回通用说明，不回显连接密码。
