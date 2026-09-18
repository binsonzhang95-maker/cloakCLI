# pin-statuses

Fixture **python_runner** with a Pinterest-like declared status set:

| id | success | notes |
|----|---------|--------|
| `logged_in` | yes | login threshold |
| `email_confirmed` | yes (optional) | extra; skill must also have logged in |
| `oops_park` | no | retryable display flag; does not auto-resubmit |

Pass `vars.status` to choose the final report. Last stdout line is the JSON report (`skill_id` / `version` / `digest` / `status`).
