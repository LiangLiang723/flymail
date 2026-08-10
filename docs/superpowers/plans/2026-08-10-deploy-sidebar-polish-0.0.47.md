# Sidebar Polish 0.0.47 Deployment Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package commit `d2801cd` and the current `main` checkout into a new `0.0.47` Docker image and replace the running `flymail` container without changing persistent data.

**Architecture:** No application behavior changes are added in this corrective release. `VERSION` remains the release source of truth and is synchronized to package metadata, Compose, and README; the image is verified in an isolated temporary `/data` before the production container is replaced while preserving `/Docker/flymail/data:/data` and the existing host port.

**Tech Stack:** Vue 3, FastAPI, MySQL 8.0, Docker, Node test runner, Python unittest.

**Execution status:** Completed on 2026-08-10; production `flymail` is running `benxianyu/flymail:0.0.47` with the existing `/Docker/flymail/data:/data` mount.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on `main`.
- Do not delete, migrate, reset, or write test data into `/Docker/flymail/data`.
- Do not upload Docker Hub.
- Do not expose passwords, session secrets, mailbox credentials, OAuth tokens, or database URLs in logs or Git.
- Production replacement must preserve the current `/Docker/flymail/data:/data` bind mount and host port.

---

### Task 1: Publish version metadata

**Files:**
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: current release `0.0.46`.
- Produces: consistent release version `0.0.47` in every version surface.

- [ ] **Step 1:** Change `VERSION` from `0.0.46` to `0.0.47` and synchronize the four derived version surfaces.
- [ ] **Step 2:** Verify `VERSION`, root package version, frontend package version, Compose image tag, and README image tags all equal `0.0.47`.
- [ ] **Step 3:** Run `git diff --check` and inspect the version-only diff.

### Task 2: Verify source before packaging

**Files:** None.

**Interfaces:**
- Consumes: current `main` source including `d2801cd`.
- Produces: test and build evidence for the exact release source.

- [ ] **Step 1:** Run `cd backend && python -m unittest discover -s tests -v`; expect all tests to pass.
- [ ] **Step 2:** Run `cd frontend && npm test`; expect all tests to pass.
- [ ] **Step 3:** Run `cd frontend && npm run build`; expect TypeScript and Vite production build success.
- [ ] **Step 4:** Run `bash -n scripts/docker-entrypoint.sh` and validate Compose using a temporary private env file derived from the running container if the repository `.env` is absent.

### Task 3: Build and validate image

**Files:** None.

**Interfaces:**
- Consumes: version `0.0.47` source tree.
- Produces: local image `benxianyu/flymail:0.0.47` proven safe to deploy.

- [ ] **Step 1:** Build `docker build -t benxianyu/flymail:0.0.47 .`.
- [ ] **Step 2:** Start a uniquely named temporary container with a unique temporary host data directory, a generated session secret, and a database password containing special characters.
- [ ] **Step 3:** Verify Docker health, `/api/health` version `0.0.47`, MySQL 8.0, datadir `/data/mysql/`, `/data/flymail`, database read/write, restart persistence, secret-free image metadata, redacted logs, and graceful shutdown.
- [ ] **Step 4:** Remove only the temporary container and temporary data.

### Task 4: Replace production container safely

**Files:** None.

**Interfaces:**
- Consumes: verified image `benxianyu/flymail:0.0.47` plus the existing production container configuration.
- Produces: running `flymail` on `0.0.47` with unchanged persistent data.

- [ ] **Step 1:** Audit current image, port binding, restart policy, network mode, environment, and `/Docker/flymail/data:/data` mount without printing secrets.
- [ ] **Step 2:** Gracefully stop and rename the old container as rollback protection, then start `flymail` from `0.0.47` with the same runtime configuration.
- [ ] **Step 3:** Verify `running/healthy`, health version `0.0.47`, MySQL 8.0 datadir, persistent mount, database table count, and restart persistence.
- [ ] **Step 4:** Verify the deployed frontend contains the sidebar account/folder scroll split and custom-folder initial icon markup, then delete the rollback container only after all checks pass.

### Task 5: Commit and push release metadata

**Files:**
- Commit only the plan and version synchronization files.

**Interfaces:**
- Consumes: verified production deployment.
- Produces: `origin/main` matching deployed version `0.0.47`.

- [ ] **Step 1:** Re-run `git diff --check`, inspect status/diff, and scan staged files for secrets.
- [ ] **Step 2:** Commit with a specific Chinese release/deployment title.
- [ ] **Step 3:** Push `origin main` and verify `main...origin/main`, production health, image tag, and version.
