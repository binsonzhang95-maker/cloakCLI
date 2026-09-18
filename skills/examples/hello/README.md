# hello

示例 skill：打开 https://example.com，提取 h1，截图。

在 Mac 上可用 TUI / `browser open` 教学验证。

- **本地调试：** `cloakcli skill run hello --profile <test-profile>`（仅调试；测试账号 / 独立 profile）。
- **批量 / 从机：** `cloakcli master skill-pack --skill hello` → `master skill-sync --client <id> --skill hello` → `master submit --client <id> --skill hello --profile <geo-profile>`。`job_submit` 绑定已发布包的 SHA-256 digest，不会回退到从机本地同名 skill。
