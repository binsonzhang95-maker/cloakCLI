# pinterest-settings-email-verify

注册走 `signup_ok_no_code_challenge`（未弹 6 位码、邮箱 Unconfirmed）时用。

## 前置
- 已登录 / onboarding 可过（`DISPLAY_NAME` = 合理英文名，勿用邮箱前缀）
- `CODE` 由 runner IMAP 拉取（`Your Pinterest verification code`）

## 关键步骤
1. Escape 关掉账号菜单遮罩
2. `settings/account-settings/` → **Confirm Email**
3. `#code` → Continue → Email 徽章 **Confirmed**
4. Continue 后与 path A 相同：等到码 UI 消失 / 可见错误 / 再查 Confirmed。`verify_soft_oops` 会 **Send new code** 重试一次（`verify_retry`）。日志不回显验证码。

## Runner
```bash
python3 scripts/run_pinterest_settings_email_verify.py \
  --profile geo03 \
  --secrets data/secrets/pinterest-outlook-XX.env \
  --name Otis
```

与 `pinterest-register-outlook-verify` README「分支 B」一致；本 skill 是浏览器段可回放步骤。
