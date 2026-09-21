# pinterest-register-outlook-verify

Pinterest 注册 + Outlook 邮箱 **6 位验证码**确认（不是链接）。

## Vars / secrets
Skill 浏览器段用：
- `EMAIL` / `PASSWORD`
- `BIRTHDAY`：`YYYY-MM-DD`（`input[type=date]#birthdate`）
- 生日由 runner 在 **25–35 岁**间随机生成

Outlook IMAP（仅 secrets 文件，勿进 skill/git）：
- `OUTLOOK_EMAIL` / `OUTLOOK_CLIENT_ID` / `OUTLOOK_REFRESH_TOKEN`
- Token 仅 IMAP/EWS，不能 Graph

## 跑法
```bash
python3 scripts/run_pinterest_register_outlook_verify.py \
  --profile geo02 \
  --secrets data/secrets/pinterest-outlook-XX.env
# 无头易 Oops，默认 headed
# Default chains same-session nurture before ctx.close (omit --skip-nurture)
```

## Behavior pacing (runner 0.1.3)

Register now shares nurture 0.2.1 human behavior (trail click + key stream). Signup/verify Continue, email/password focus, and Send-new-code use `human_click_locator` (never `locator.click` / force teleport; one trail retry then fail). Email/password/code/name use `human_type_text`. Birthdate `#birthdate` still `fill` after trail focus. Quiet window after signup land; log-normal pauses.

- Between form fields: **800–2500ms** log-normal
- Before Continue (signup + verify): **2–5s**
- After Continue before judging Oops / code UI / success: **3–8s** settle + network idle / URL / toast poll (do not rush)
- After code fill: **1.2–2.8s** before verify Continue. If `#code` value does not match the target, fail immediately (`verify_soft_fail`) — do **not** click Continue
- Soft verify fail: one **Send new code** retry OK; hard **Oops**: park — no immediate re-Continue spam
- Email confirmed toast → treat as verify success candidate

## Concurrency

- Keep fleet concurrency **≤2–3** register jobs
- Shared **udeal** exit → **serial** or **1 per exit**; cooldown **5–10 min** after `oops_blocked`
- One account ↔ one geo/profile; do not share `user_data_dir`

## 选择器 / 提交
- `#email` `#password` `#birthdate` → 表单 Continue（避开 Google）
- 验证页 `#code`；React 友好逐字填入后点 **验证弹窗内** Continue（不要只按 Enter）
- Continue 选择器优先：`verification-code-form` / `form:has(#code)` / `[role=dialog]`

## 实测备注（2026-09-18）
- IMAP 取码稳定（优先匹配 code/Pinterest 附近的 6 位，避开追踪号）
- geo 代理 + headed 可到「Enter the code」；headless / 部分 geo 会 Oops
- Nova（outlook-02）+ geo02：账号已登录，进入 onboarding「Nice to meet you! What's your name?」
- 同邮箱再跑会走 `skipped_code_already_logged_in`（不再出验证码页）
- 批量前请用**未注册过**的新邮箱再验一遍完整「填码 → Continue → 登录态」路径
- Otis（outlook-03）+ geo03：点 Continue 后**未出验证码页**，直接进 onboarding（收件箱也无 Pinterest 验证信）；路径记为 `signup_ok_no_code_challenge`

## 分支 B：注册未弹验证码（IP/geo 相关）
部分 geo 点 Continue 后直接进 onboarding，邮箱状态为 **Unconfirmed**。需补：

1. 先走完 onboarding（名字用合理英文名，勿用邮箱前缀）
2. Settings → Account management：`https://www.pinterest.com/settings/account-settings/`
3. 点 **Confirm Email** → 弹窗 `#code` → IMAP 收 `Your Pinterest verification code` → Continue
4. 成功后 Email 旁徽章变为 **Confirmed**

```bash
python3 scripts/run_pinterest_settings_email_verify.py \
  --profile geo03 \
  --secrets data/secrets/pinterest-outlook-03.env \
  --name Otis
```

注意：点 Confirm Email 前先 `Escape` 关掉账号菜单遮罩，否则点不到。

配套 skill：`skills/pinterest-settings-email-verify/`（Confirm Email 浏览器段）。

## Cookie / 会话
登录成功后 cookie 落在 `data/profiles/<geo>-pinterest-run/`（CloakBrowser persistent）。默认**保留**。
- `--fresh-profile`: wipe **only at start** of a register attempt. **Never** after success. Runner **refuses** wipe if `data/profiles/<p>-pinterest-run/.cloak_session_ok` exists.
- After register success the runner touches `.cloak_session_ok` for ops.
- 一号一 geo，勿混用。

## Pipeline: register → nurture (same job) — CRITICAL

On **register/login success** (`status=ok`, including path `signup_ok_no_code_challenge`), the runner **MUST** chain nurture in the **same browser session** (keep-open) **before `ctx.close()`**. Cookies / `user_data_dir` are kept.

1. Touch `.cloak_session_ok`
2. Flush session (2–4s wait + optional `storage_state` + navigate home once)
3. `session_keepalive_probe` — if login wall → `nurture_status=session_lost_before_nurture` (not `browsed_ok`)
4. Nurture browse (0.2.1+); register `status=ok` preserved even if nurture fails

**Never** run independent nurture minutes later as the **primary** path (server session often revoked; cookies on disk ≠ logged-in; one password probe can deactivate). Independent nurture only for already-warm alive accounts.

| field | meaning |
|-------|---------|
| `nurture_status` | `browsed_ok` / `like_failed` / `skipped` / `session_lost_before_nurture` / `error:…` |
| `nurture_elapsed_s` | nurture wall time (seconds) |
| `nurture_liked` | bool |

```bash
# default: register success → same-session nurture → then close
python3 scripts/run_pinterest_register_outlook_verify.py \
  --profile geo02 \
  --secrets data/secrets/pinterest-outlook-XX.env

# only if batch already nurtures in-process (logs warning — not primary path)
python3 scripts/run_pinterest_register_outlook_verify.py \
  --profile geo02 \
  --secrets data/secrets/pinterest-outlook-XX.env \
  --skip-nurture
```

Optional: `--nurture-pins N` `--nurture-min-sec 120` `--nurture-max-sec 180`.

Flags for Bot:
- Prefer **headed** (default); avoid `--headless`
- Omit `--skip-nurture` unless you truly nurture in the same job elsewhere
- `--fresh-profile` only on brand-new attempt (no `.cloak_session_ok`)
- Concurrency ≤2–3; shared udeal → serial / 1 per exit; 5–10min cooldown after Oops

Standalone nurture (already-alive session): see `skills/pinterest-nurture-browse/` (0.2.1+).

