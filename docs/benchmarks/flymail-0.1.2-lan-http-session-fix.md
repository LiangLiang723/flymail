# FlyMail 0.1.2 局域网 HTTP 会话修复验证

## 问题

`0.1.1` 已恢复根页面和前端静态资源，但在真实局域网地址 `http://192.168.3.53:36080` 登录时出现：

```text
login_status=200
me_status=401
cookie_stored=no
```

登录业务本身成功，浏览器却没有保存会话 Cookie，因此页面无法维持登录状态。

## 根因

`backend/flymail/api/routes/auth.py` 在登录和注销时无条件使用 `Secure` Cookie。浏览器不会在普通 HTTP 地址上保存或发送 `Secure` Cookie。FlyMail 同时支持直连局域网 HTTP 和 HTTPS 反向代理，因此 Cookie 的 `Secure` 属性必须与请求方案一致。

## 修复

- HTTP 请求设置不带 `Secure` 的签名、HttpOnly、SameSite=Lax 会话 Cookie。
- HTTPS 请求继续设置 `Secure`，保持传输安全边界。
- 注销删除 Cookie 时使用与请求方案一致的 `Secure` 属性，保证 HTTP 和 HTTPS 都能正确清除。
- 会话签名、服务端哈希存储、CSRF、同源校验、密码版本和失效机制不变。

## TDD 证据

新增 `test_http_login_sets_non_secure_cookie_and_session_is_reusable`：

1. 旧实现先失败，错误明确为 HTTP `Set-Cookie` 仍包含 `Secure`。
2. 最小实现后，HTTP 登录、`/auth/me`、注销、Cookie 清除和注销后 401 全部通过。
3. 既有 HTTPS 测试继续确认 `Secure` Cookie，避免为兼容 HTTP 放宽 HTTPS。

HTTP 与 HTTPS 目标回归：`2/2` 通过；完整认证与 OpenAPI 合同回归：`18/18` 通过。

## 版本与镜像

- 版本：`0.1.2`
- 镜像：`benxianyu/flymail:0.1.2`
- 镜像 ID：`sha256:ab4f2b1b9b7bee19a98c15a5f64bbc600c7950a5b5fb6aaa5a8fb27d82d66918`
- OpenAPI：90 个路径、114 个操作、138 个 schema
- OpenAPI SHA-256：`64564c212c0683db08c655c0a06f78d602c12bd0bbb28afa2ba8ea7876897acc`
- Docker Hub：未上传

## 完整验证

- 后端：`655/655` 通过，用时 `331.454` 秒。
- 兼容前端：`96/96` 通过。
- V2 前端：`64/64` 通过。
- 前端生产构建：通过；初始 JS `66.70 KiB gzip`，最大异步块 `65.64 KiB gzip`。
- Python 静态编译、Shell 语法、Docker Compose 渲染和 `git diff --check`：通过。
- 隔离容器健康、MySQL 8.0、`/data/mysql/`、回环绑定、Worker 心跳、首次管理员、重启持久化、租约恢复和安全关闭：通过。
- 镜像、历史、日志、Compose 和 Git 差异秘密扫描：通过。

镜像级 HTTP 会话检查：

```text
login=200
me=200
logout=200
post_logout=401
cookie_stored=yes
root_content_type=text/html; charset=utf-8
version=0.1.2
```

## 生产状态

生产容器已使用现有 `/Docker/flymail/data` 从 `0.1.1` 替换为 `benxianyu/flymail:0.1.2`，数据目录没有重建、移动或清空。生产验证结果：

```text
version=0.1.2
health=healthy
root=html
asset=passed
unknown_api=json_404
login=200
me=200
logout=200
post_logout=401
login_after_restart=200
me_after_restart=200
schema=17
mysql=8.0.46 /data/mysql/ 127.0.0.1
admins=1
accounts=0
worker_heartbeat_age_seconds=4
```

MySQL 3306 未发布到宿主机，旧 V2 容器只在新容器全部检查通过后删除；旧 `0.0.25` 数据保留目录不变。
