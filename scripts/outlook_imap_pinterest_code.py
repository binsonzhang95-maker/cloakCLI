#!/usr/bin/env python3
"""Wait for Pinterest verification email via Outlook IMAP XOAUTH2; print JSON {code,subject,uid}."""
from __future__ import annotations
import argparse, email, imaplib, json, re, ssl, sys, time, urllib.parse, urllib.request
from email.header import decode_header
from pathlib import Path

CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
# Prefer 6-digit AFTER "code" / "Pinterest" (avoid earlier tracking numbers).
NEAR_CODE_RE = re.compile(
    r"(?:verification\s*code|your\s*code|security\s*code|pinterest[^\d]{0,40}code|\bcode)\D{0,40}(?<!\d)(\d{6})(?!\d)",
    re.I | re.S,
)
# Digits then keyword only within a short subject-style window
NEAR_CODE_REV = re.compile(
    r"(?<!\d)(\d{6})(?!\d)\s*(?:is\s+your\s+)?(?:verification\s*)?code",
    re.I,
)

def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v
    return env

def dec(v: str | None) -> str:
    if not v:
        return ""
    out = []
    for b, enc in decode_header(v):
        out.append(b.decode(enc or "utf-8", "replace") if isinstance(b, bytes) else b)
    return "".join(out)

def body_text(msg: email.message.Message) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() in ("text/plain", "text/html"):
                try:
                    parts.append((p.get_payload(decode=True) or b"").decode(p.get_content_charset() or "utf-8", "replace"))
                except Exception:
                    pass
    else:
        try:
            parts.append((msg.get_payload(decode=True) or b"").decode(msg.get_content_charset() or "utf-8", "replace"))
        except Exception:
            pass
    return "\n".join(parts)

def pick_code(text: str) -> str | None:
    """Prefer a 6-digit near 'code'/'Pinterest'; fall back carefully."""
    m = NEAR_CODE_RE.search(text)
    if m:
        return m.group(1)
    mrev = NEAR_CODE_REV.search(text)
    if mrev:
        return mrev.group(1)
    subj = text.splitlines()[0] if text else ""
    if CODE_RE.fullmatch(subj.strip()):
        return subj.strip()
    m3 = CODE_RE.search(text)
    return m3.group(1) if m3 else None

def access_token(client_id: str, refresh: str) -> str:
    form = urllib.parse.urlencode({
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    }).encode()
    req = urllib.request.Request(
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        data=form, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access_token"]

def open_imap(user: str, token: str):
    def auth(_):
        return f"user={user}\x01auth=Bearer {token}\x01\x01".encode()
    M = imaplib.IMAP4_SSL("outlook.office365.com", 993, ssl_context=ssl.create_default_context())
    M.authenticate("XOAUTH2", auth)
    M.select("INBOX")
    return M

def max_uid(env: dict[str, str]) -> int:
    token = access_token(env["OUTLOOK_CLIENT_ID"], env["OUTLOOK_REFRESH_TOKEN"])
    M = open_imap(env["OUTLOOK_EMAIL"], token)
    typ, data = M.uid("search", None, "ALL")
    uids = [int(x) for x in (data[0].split() if data and data[0] else [])]
    M.logout()
    return max(uids or [0])

def wait_code(env: dict[str, str], after_uid: int, timeout: int, interval: int):
    user = env["OUTLOOK_EMAIL"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        token = access_token(env["OUTLOOK_CLIENT_ID"], env["OUTLOOK_REFRESH_TOKEN"])
        M = open_imap(user, token)
        typ, data = M.uid("search", None, "ALL")
        uids = [int(x) for x in (data[0].split() if data and data[0] else [])]
        for uid in reversed(uids):
            if uid <= after_uid:
                break
            typ, msg_data = M.uid("fetch", str(uid), "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subj = dec(msg.get("Subject"))
            text = subj + "\n" + body_text(msg)
            low = text.lower()
            if "pinterest" not in low:
                continue
            if "verification code" not in low and "code" not in subj.lower():
                continue
            code = pick_code(text)
            if code:
                M.logout()
                return {"uid": uid, "subject": subj, "code": code}
        M.logout()
        time.sleep(interval)
    return None

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secrets", required=True)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--interval", type=int, default=6)
    ap.add_argument("--after-uid", type=int, default=None)
    ap.add_argument("--print-max-uid", action="store_true")
    args = ap.parse_args()
    env = load_env(Path(args.secrets))
    if args.print_max_uid:
        print(max_uid(env))
        return 0
    after = args.after_uid if args.after_uid is not None else max_uid(env)
    hit = wait_code(env, after, args.timeout, args.interval)
    if not hit:
        print(json.dumps({"error": "timeout"}), file=sys.stderr)
        return 1
    print(json.dumps(hit, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
