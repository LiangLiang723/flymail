# Nanoid Audit Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 FlyMail 前端依赖树中 nanoid 3.3.17 触发的 1 个 high severity npm audit 告警，同时避免无关的 Vite、PostCSS 或 Vue 升级。

**Architecture:** 当前告警来自 `vite 6.4.3 -> postcss 8.5.25 -> nanoid 3.3.17`。PostCSS 已允许 `nanoid ^3.3.16`，因此在 `frontend/package.json` 使用 npm `overrides` 将 nanoid 固定为安全的 3.3.18；不新增 FlyMail 运行时代码，也不改变后端或数据模型。

**Tech Stack:** npm、Vue 3、Vite、Docker。

**Spec:** 用户当前请求：修复前端 npm audit 的 1 个 high severity 告警。

## Global Constraints

- 只修复当前 nanoid 高危告警，不做无关依赖升级。
- 不新增生产功能或后端依赖。
- 不修改数据库 schema、认证、权限或 `/Docker/flymail/data` 数据结构。
- `VERSION` 从 `0.0.57` 升至 `0.0.58`，并同步根 package、frontend package、Compose 与 README 镜像版本。
- Docker Hub 不上传。

---

### Task 1: 固定安全的 nanoid 传递依赖

**Files:**
- Modify: `frontend/package.json`
- Verify: ignored local `frontend/package-lock.json` behavior

**Interfaces:**
- Consumes: `postcss@8.5.25` 对 `nanoid ^3.3.16` 的依赖约束。
- Produces: npm 安装解析 `nanoid@3.3.18`。

- [ ] **Step 1: Confirm RED**

Run: `cd frontend && npm audit --json`

Expected: `high: 1`，告警包为 `nanoid`，受影响范围 `<3.3.18`。

- [ ] **Step 2: Add the minimal override**

在 `frontend/package.json` 增加：

```json
"overrides": {
  "nanoid": "3.3.18"
}
```

不调整 Vite、PostCSS 或其他依赖版本。

- [ ] **Step 3: Verify GREEN in a clean dependency resolution**

把受版本控制的 `frontend/package.json` 与当前 lockfile 复制到独立 `/tmp` 目录后执行 `npm install --ignore-scripts` 和 `npm audit --audit-level=high`。

Expected: `nanoid@3.3.18 overridden`，`high: 0`，`critical: 0`，`total: 0`。

---

### Task 2: 发布 0.0.58 并完整验证

**Files:**
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Verify: `.env.example`, `Dockerfile`, `scripts/docker-entrypoint.sh`

**Interfaces:**
- Consumes: existing single-container Docker deployment.
- Produces: `benxianyu/flymail:0.0.58` and healthy local `flymail` container.

- [ ] **Step 1: Synchronize version 0.0.58**

Update `VERSION`, root/frontend package versions, Compose image tag and README image references to `0.0.58`.

- [ ] **Step 2: Run source and security verification**

Run backend unit tests, frontend tests, frontend production build, clean-install npm audit, shell syntax, Compose structure validation, `git diff --check`, and version consistency checks.

Expected: backend and frontend tests all pass; npm audit reports zero vulnerabilities at high/critical and zero total for the resolved frontend tree.

- [ ] **Step 3: Build and verify Docker image**

Build `benxianyu/flymail:0.0.58`; also verify the frontend builder stage resolves `nanoid@3.3.18` and npm audit has zero high/critical vulnerabilities. Run an isolated temporary container using a temporary `/tmp` data directory and a password containing special characters; verify health, MySQL 8.0, `/data/mysql/`, `/data/flymail`, DB read/write, restart persistence, log redaction, image metadata secret absence, and safe MySQL shutdown.

- [ ] **Step 4: Recreate current local container safely**

Preserve current environment, port mapping, restart policy and `/Docker/flymail/data:/data`; replace only the image with `benxianyu/flymail:0.0.58`. Verify health and persistence after restart before removing rollback container.

- [ ] **Step 5: Commit and push only this task**

Commit title: `🔒 修复前端 nanoid 高危依赖告警`

Push to `origin/main` only after fresh verification. Do not push Docker Hub.
