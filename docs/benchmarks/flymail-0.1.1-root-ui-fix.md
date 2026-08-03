# FlyMail 0.1.1 根页面修复验证

## 问题

生产 `0.1.0` 的 `/api/health` 正常，但访问 `http://<host>:36080/` 返回安全 JSON 404，而不是 Vue 前端页面。

## 根因

V2 正式入口 `backend/flymail/api/app.py` 只注册了 API 路由。Docker 镜像已经把前端产物复制到 `/app/frontend-dist`，并设置 `FLYMAIL_UI_DIR=/app/frontend-dist`，但应用没有在路由未命中时读取该目录，因此根路径和 Vue History 路由都进入统一 404 处理器。

## 修复

- API 应用启动时验证 `FLYMAIL_UI_DIR/index.html` 并把安全解析后的路径放入应用状态。
- 仅在路由系统已经判定 GET/HEAD 请求为 404 后执行前端兜底，避免遮挡现有或后续 API 路由。
- 根路径和无扩展名的 Vue History 路由返回 `index.html`。
- 已存在的静态文件按真实 MIME 类型返回。
- `/api` 路径、缺失的 `assets/*` 和缺失的带扩展名文件继续返回安全 JSON 404。
- 静态文件解析拒绝空字节、绝对路径、`..` 和 UI 根目录外的解析结果。

## 版本与镜像

- 版本：`0.1.1`
- 镜像：`benxianyu/flymail:0.1.1`
- 镜像 ID：`sha256:01911375be6f5e1852f2384b96693287cd3ca41f073cbe0ebd468c89ed4fb475`
- OpenAPI：90 个路径、114 个操作、138 个 schema
- OpenAPI SHA-256：`51700b765067e28fb6ceb6f3e80499653680baa1adb40fa7485129a03c4fa84a`
- Docker Hub：未上传

## 自动化验证

- 新增回归先在旧实现上得到根路径 `404 != 200`，随后转绿。
- API 应用与冻结合同：`15/15` 通过。
- 完整后端：`654/654` 通过，用时 `303.194` 秒。
- 兼容前端：`96/96` 通过。
- V2 前端：`64/64` 通过。
- 前端生产构建：通过；初始 JS `66.70 KiB gzip`，最大异步块 `65.64 KiB gzip`。
- Python 静态编译、三个 Shell 语法检查、Docker Compose 渲染和 `git diff --check`：通过。
- 隔离容器健康、MySQL 8.0、`/data/mysql/`、回环绑定、Worker 心跳、首次管理员、重启持久化、租约恢复和安全关闭：通过。
- 镜像、历史、日志、Compose 和 Git 差异秘密扫描：通过。

镜像级根页面检查：

```text
root=html
asset=passed
spa_fallback=html
unknown_api=json_404
version=0.1.1
```

## 生产状态

补丁提交前，生产容器仍运行 `benxianyu/flymail:0.1.0`，使用现有 `/Docker/flymail/data`。部署 `0.1.1` 时只替换容器镜像，不重建、移动或清空数据目录。部署完成后的实际状态在本文件后续更新。
