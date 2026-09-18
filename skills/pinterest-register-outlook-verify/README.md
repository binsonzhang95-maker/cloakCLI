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
```

## 选择器 / 提交
- `#email` `#password` `#birthdate` → 表单 Continue（避开 Google）
- 验证页 `#code`；React 友好逐字填入后点 **验证弹窗内** Continue（不要只按 Enter）
- Continue 选择器优先：`verification-code-form` / `form:has(#code)` / `[role=dialog]`
- 点 Continue 后等到：**码框消失** / **码框下可见错误** / **Email confirmed toast** / **onboarding**。不要只凭 onboarding 文案判成功（须先排除 verify 错误）。toast 即使 `#code` 还在也先当成功候选，再查 Settings 徽章。

## 验证错误分类（batch 04–10）
- `ok`：码 UI 消失且无 verify 错误（geo05 Augustine、geo09 Prudence path A）
- `verify_soft_oops`：仍在 `#code` UI，红框 + **Sorry! Something went wrong on our end.**（不是 invalid code）。geo07 Gabriel / geo08 Celestino。会点一次 **Send new code** → 重基 IMAP uid → 再填码 Continue（`verify_retry`，最多 1 次）
- `ok` + `path: toast_email_confirmed`：页面/toast 出现 **Email confirmed**（即使 `#code` 模态还在）。Escape 关掉模态 → 打开 `settings/account-settings/`，Email 徽章为 **Confirmed** 才算成功。徽章仍是 Unconfirmed 则保持 `verify_soft_oops` 并记 note
- `oops_blocked`：注册 Continue 后弹出 **Oops!** 模态（Okay）。geo06 Titus / geo10 Ethelyn。代理/指纹风控，选择器救不了；runner 可 Okay 关掉再点一次 Continue，仍 Oops 则原样失败
- `code_error`：incorrect / invalid / expired
- 日志只记 `error_snip` / `code_len`，**不回显** 6 位码或密码

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
登录成功后 cookie 落在 `data/profiles/<geo>-pinterest-run/`（CloakBrowser persistent）。默认**保留**；只有加 `--fresh-profile` 才会清空重开。一号一 geo，勿混用。
