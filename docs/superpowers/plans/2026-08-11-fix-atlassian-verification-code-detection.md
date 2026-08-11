# Atlassian Verification Code Detection Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Atlassian-style template `24681357 是您的验证代码` so FlyMail recognizes the code from both subject and cached body without weakening existing false-positive filters.

**Architecture:** Keep the existing dependency-free `services.verification_code` scoring model. Extend only the strong Chinese verification context vocabulary with the two contexts proven by the production message: `验证代码` and identity-verification wording such as `验证自己的身份`; do not add a generic “代码” keyword that would increase order/promo false positives.

**Tech Stack:** Python 3, unittest, FastAPI response path, Vue 3 build regression, Docker/MySQL 8.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on `main`.
- Do not add production dependencies or database columns.
- Do not weaken order/date/phone/invoice/tracking false-positive suppression.
- Do not write, delete, migrate, or reset `/Docker/flymail/data`.
- Do not log verification-code values, mailbox credentials, database passwords, tokens, or session secrets.
- Keep existing mail-list UI and copy-button layout unchanged unless a regression proves UI code is involved.
- Build only the local `benxianyu/flymail` image; do not upload Docker Hub.

---

### Task 1: Reproduce the Atlassian wording with a synthetic automated test

**Files:**
- Modify: `backend/tests/test_verification_code.py`

**Interfaces:**
- Consumes: `extract_verification_code(subject='', body_text='', body_html='')`.
- Produces: regression coverage for the production wording using synthetic message content.

- [ ] **Step 1: Add the synthetic subject regression test**

```python
def test_extracts_atlassian_chinese_verification_code_subject(self):
    self.assertEqual(
        self.extract(subject="24681357 是您的验证代码【请注意，请勿泄露】"),
        "24681357",
    )
```

- [ ] **Step 2: Add the synthetic body-only regression test**

```python
def test_extracts_atlassian_identity_verification_body(self):
    self.assertEqual(
        self.extract(
            body_text=(
                "测试用户，您好。\n"
                "作为额外的安全防护层，您需要验证自己的身份。\n"
                "请输入以下代码：\n\n24681357"
            )
        ),
        "24681357",
    )
```

- [ ] **Step 3: Verify RED**

Run:

```bash
cd backend
python -m unittest \
  tests.test_verification_code.VerificationCodeTests.test_extracts_atlassian_chinese_verification_code_subject \
  tests.test_verification_code.VerificationCodeTests.test_extracts_atlassian_identity_verification_body -v
```

Expected before implementation: both assertions fail with `'' != '24681357'`.

### Task 2: Extend only the proven Chinese strong contexts

**Files:**
- Modify: `backend/services/verification_code.py`
- Test: `backend/tests/test_verification_code.py`

**Interfaces:**
- Consumes: `_STRONG_POSITIVE_RE` and current 80-character positive-context scoring.
- Produces: recognition of `验证代码` and `验证自己的身份`/`验证您的身份`/`验证你的身份`/`验证身份` without making generic `代码` a standalone positive signal.

- [ ] **Step 1: Implement the minimal regex expansion**

Add these alternatives to `_STRONG_POSITIVE_RE` near the existing Chinese verification terms:

```python
r"验证码|验证代码|校验码|...|"
r"验证(?:自己|您|你)?的?身份|"
```

Do not add standalone `代码` or `验证`, because those are too broad for email bodies containing unrelated numeric IDs.

- [ ] **Step 2: Verify GREEN on the two production regressions**

Run the same focused unittest command from Task 1.

Expected: both tests pass.

- [ ] **Step 3: Verify existing parser false-positive coverage**

Run:

```bash
cd backend
python -m unittest tests.test_verification_code -v
```

Expected: all verification-code parser tests pass, including order/date/phone exclusions.

### Task 3: Document the supported wording and bump the release

**Files:**
- Modify: `docs/superpowers/specs/2026-08-11-mail-verification-code-copy-design.md`
- Modify: `README.md`
- Modify: `VERSION`
- Modify via version sync: `package.json`
- Modify via version sync: `frontend/package.json`
- Modify via version sync: `docker-compose.yml`

**Interfaces:**
- Consumes: current version `0.0.49`.
- Produces: release `0.0.50` with documentation stating Chinese `验证代码` and identity-verification context are recognized.

- [ ] **Step 1: Update the design wording**

Extend the positive-context paragraph to include `验证代码` and identity-verification wording. Add an acceptance example for the exact Atlassian subject.

- [ ] **Step 2: Set VERSION to `0.0.50` and synchronize version surfaces**

Run:

```bash
npm run sync-version
```

Expected: `VERSION`, root `package.json`, `frontend/package.json`, `docker-compose.yml`, and README image references all report `0.0.50`.

- [ ] **Step 3: Re-check README behavior text**

Keep the public description concise; only add wording if needed to avoid documenting behavior more narrowly than the implementation.

### Task 4: Full regression, image, container, and Git delivery

**Files:** None beyond Task 1–3.

**Interfaces:**
- Consumes: release `0.0.50` source tree.
- Produces: verified local image and healthy production container `flymail` using the existing `/Docker/flymail/data:/data` mount.

- [ ] **Step 1: Run complete source verification**

```bash
cd backend && python -m unittest discover -s tests -v
cd frontend && npm install && npm test && npm run build
bash -n scripts/docker-entrypoint.sh
docker compose config --quiet
git diff --check
git status --short
git diff
```

If the workspace still intentionally lacks `.env`, record that standard Compose validation limitation and validate the Compose structure with the same non-persistent equivalent used for 0.0.49 rather than creating a credential file.

- [ ] **Step 2: Build the local image**

```bash
docker build -t benxianyu/flymail:0.0.50 .
```

Expected: build succeeds; Docker Hub is not used.

- [ ] **Step 3: Run isolated temporary-container validation**

Use an independent temporary host data directory and a MySQL password containing quote/backslash/`@`/`:`/`/`/`%`. Verify `healthy`, `/api/health` version `0.0.50`, MySQL 8.0 `/data/mysql/`, DB write/read, restart persistence, log redaction, image metadata secrecy, and graceful shutdown. Clean only the temporary container/data.

- [ ] **Step 4: Safely replace production**

Preserve current environment, host port, restart policy, network mode, and `/Docker/flymail/data:/data`. Keep rollback protection until `0.0.50` is healthy, `/api/health` reports `0.0.50`, MySQL is 8.0 with `/data/mysql/`, and existing database records remain readable after restart.

- [ ] **Step 5: Final verification, commit, and push**

Run `verification-before-completion`, inspect staged diff and secret scan, then commit only task files with:

```text
🐛 修复 Atlassian 验证代码邮件漏识别
```

Push `origin/main` and verify local `HEAD` equals `origin/main`.
