# 架构

React 单页前端通过同源 `/backend` 路径调用 FastAPI。服务器部署由 `deploy/web.py` 提供网页登录与静态文件，并将鉴权后的请求转发至应用内部 API。开发环境由 Vite 提供同源代理。

后端是模块化单体：`api/routes` 处理 HTTP，`services` 处理作品管理、阅读和版本维护，`generation` 处理有限阶段创作，`providers` 对接作者配置的模型，`db` 和本地文件目录保存状态与不可变工件。

Chief 负责剧情计划，Writer 按单元写作，Memory 接续有正文证据的事实；Checker 和 Reader 按作者配置使用。完整正文、候选事实与正式资料分开保存，采用由作者确认。旧生成流程已退役，历史数据结构保留以支持读取与迁移，不恢复旧执行入口。

Docker 包含前端与后端，数据库使用独立 PostgreSQL 容器。数据、日志和凭据保存在运行时挂载中，模型 Key 不进入镜像或浏览器代码。阶段预算与授权、未知结果暂停、已保存正文保留和恢复检查点边界仍由生成内核执行。

运行方法见 [README](../README.md)，开发与迁移链见 [开发说明](DEVELOPMENT.md)。
