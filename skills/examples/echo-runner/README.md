# echo-runner

Tiny **python_runner** fixture for skill package → master `skill_sync` → digest-bound `job_submit`.

## Package layout

```
skills/examples/echo-runner/
  skill.json          # name / description (no browser steps)
  manifest.json       # version + entry.kind=python_runner + secret *names* only
  scripts/echo.py     # package-relative runner (fixed interpreter, argv array, no shell)
```

`manifest.json` `entry.path` is relative to the package root. Master packs the directory into tar + SHA-256. Clients only run this path from an installed digest cache — never a job-supplied command.

```bash
# Master: pack + publish, then push to a connected client
cloakcli master skill-pack --skill echo-runner
cloakcli master skill-sync --client box1 --skill echo-runner
cloakcli master submit --client box1 --skill echo-runner --profile noproxy --headless

# Local debug only (test account / isolated profile — not production batch)
cloakcli skill run echo-runner --profile noproxy --headless
```

Pinterest IMAP/OAuth runners still live under repo `scripts/` this milestone; moving them into a package `scripts/` is a follow-up. Same entry contract: relative path, no `shell=True`, secrets as names/refs only.
