"""Persistent per-profile nurture BehaviorProfile (0.2.5 P0 — de-homology).

Stores behavior_seed + bounded joint params in profiles/<name>/profile.json
under the ``behavior_profile`` field (separate from fingerprint_seed).

Engineering goal: reduce avoidable cross-profile homology from shared
hyperparameters / coupled RNG / fixed ritual steps / unbounded loops.
Does NOT claim anti-detect or platform unrecognizability.

Atomic JSON writes mirror fingerprint.py. Never log proxy/email/cookies.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GENERATOR_VERSION = "0.2.5"
SCHEMA_VERSION = 1
BOUNDS_VERSION = 1
_FIELD = "behavior_profile"

BEHAVIOR_SEED_MIN = 1
BEHAVIOR_SEED_MAX = 2_147_483_647

PAUSE_SCALE_RANGE = (0.75, 1.35)
PAUSE_DISPERSION_RANGE = (0.80, 1.25)
SCROLL_STEP_SCALE_RANGE = (0.80, 1.25)
SCROLL_DECAY_RANGE = (0.85, 1.20)

OPTIONAL_WEIGHT_KEYS = ("feed_hover", "between_pin_scroll", "peek_scroll")
OPTIONAL_WEIGHT_RANGE = (0.50, 1.50)

DEFAULT_MAX_SESSION_ELAPSED_SEC = 600.0
DEFAULT_MAX_ACTIONS = 80
DEFAULT_MAX_STATE_VISITS = 40
BUDGET_ELAPSED_CEILING_SEC = 900.0
BUDGET_ACTIONS_CEILING = 120
BUDGET_STATE_VISITS_CEILING = 60

ENV_IDLE_WANDER = "CLOAKCLI_IDLE_WANDER"
ENV_MAX_ELAPSED = "CLOAKCLI_NURTURE_MAX_ELAPSED_SEC"
ENV_MAX_ACTIONS = "CLOAKCLI_NURTURE_MAX_ACTIONS"
ENV_MAX_STATE_VISITS = "CLOAKCLI_NURTURE_MAX_STATE_VISITS"


class BehaviorProfileError(RuntimeError):
    """Existing profile.json is corrupt/unreadable/non-object. File left untouched."""


def _read_profile_json(meta_path: Path) -> dict[str, Any] | None:
    """Load profile.json object.

    Returns None if the file does not exist.
    Raises BehaviorProfileError if the file exists but is unreadable, invalid JSON,
    or not a JSON object — callers must not overwrite the file in that case.
    """
    meta_path = Path(meta_path)
    if not meta_path.is_file():
        return None
    try:
        loaded = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BehaviorProfileError(
            f"profile_json_unreadable:{meta_path.name}:{type(exc).__name__}"
        ) from exc
    if not isinstance(loaded, dict):
        raise BehaviorProfileError(f"profile_json_not_object:{meta_path.name}")
    return loaded


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(v)))


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def mint_behavior_seed(*, rng: random.Random | None = None) -> int:
    r = rng if rng is not None else random.Random()
    return int(r.randint(BEHAVIOR_SEED_MIN, BEHAVIOR_SEED_MAX))


def sample_params_joint(rng: random.Random) -> dict[str, Any]:
    """Sample pause/scroll/weight params from a bounded joint space.

    Joint constraint: high pause_scale prefers mid/low scroll_step_scale
    (and vice versa) so we do not mint both-ultraslow or both-ultrafast extremes.
    """
    pause_scale = float(rng.uniform(*PAUSE_SCALE_RANGE))
    pause_dispersion = float(rng.uniform(*PAUSE_DISPERSION_RANGE))
    if pause_scale > 1.15:
        scroll_lo, scroll_hi = SCROLL_STEP_SCALE_RANGE[0], 1.10
    elif pause_scale < 0.90:
        scroll_lo, scroll_hi = 0.95, SCROLL_STEP_SCALE_RANGE[1]
    else:
        scroll_lo, scroll_hi = SCROLL_STEP_SCALE_RANGE
    scroll_step_scale = float(rng.uniform(scroll_lo, scroll_hi))
    scroll_decay = float(rng.uniform(*SCROLL_DECAY_RANGE))
    weights = {
        k: float(_clamp(rng.uniform(*OPTIONAL_WEIGHT_RANGE), *OPTIONAL_WEIGHT_RANGE))
        for k in OPTIONAL_WEIGHT_KEYS
    }
    return {
        "pause_scale": round(pause_scale, 4),
        "pause_dispersion": round(pause_dispersion, 4),
        "scroll_step_scale": round(scroll_step_scale, 4),
        "scroll_decay": round(scroll_decay, 4),
        "optional_action_weights": weights,
    }


def _coerce_weights(raw: Any) -> dict[str, float]:
    out: dict[str, float] = {k: 1.0 for k in OPTIONAL_WEIGHT_KEYS}
    if not isinstance(raw, dict):
        return out
    for k in OPTIONAL_WEIGHT_KEYS:
        v = raw.get(k)
        try:
            out[k] = float(_clamp(float(v), *OPTIONAL_WEIGHT_RANGE))
        except (TypeError, ValueError):
            out[k] = 1.0
    return out


def _valid_params(params: Any) -> dict[str, Any] | None:
    if not isinstance(params, dict):
        return None
    try:
        pause_scale = float(params["pause_scale"])
        pause_dispersion = float(params["pause_dispersion"])
        scroll_step_scale = float(params["scroll_step_scale"])
        scroll_decay = float(params["scroll_decay"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (PAUSE_SCALE_RANGE[0] <= pause_scale <= PAUSE_SCALE_RANGE[1]):
        return None
    if not (PAUSE_DISPERSION_RANGE[0] <= pause_dispersion <= PAUSE_DISPERSION_RANGE[1]):
        return None
    if not (SCROLL_STEP_SCALE_RANGE[0] <= scroll_step_scale <= SCROLL_STEP_SCALE_RANGE[1]):
        return None
    if not (SCROLL_DECAY_RANGE[0] <= scroll_decay <= SCROLL_DECAY_RANGE[1]):
        return None
    return {
        "pause_scale": pause_scale,
        "pause_dispersion": pause_dispersion,
        "scroll_step_scale": scroll_step_scale,
        "scroll_decay": scroll_decay,
        "optional_action_weights": _coerce_weights(params.get("optional_action_weights")),
    }


def _coerce_behavior_seed(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, str) and value.strip().isdigit():
            n = int(value.strip())
        else:
            n = int(value)
    except (TypeError, ValueError):
        return None
    if BEHAVIOR_SEED_MIN <= n <= BEHAVIOR_SEED_MAX:
        return n
    return None


def _coerce_session_seq(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, n)


@dataclass(frozen=True)
class BehaviorProfile:
    schema_version: int
    generator_version: str
    behavior_seed: int
    params: dict[str, Any]
    bounds_version: int = BOUNDS_VERSION
    session_seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "generator_version": str(self.generator_version),
            "behavior_seed": int(self.behavior_seed),
            "params": dict(self.params),
            "bounds_version": int(self.bounds_version),
            "session_seq": int(self.session_seq),
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> BehaviorProfile | None:
        if not isinstance(raw, dict):
            return None
        seed = _coerce_behavior_seed(raw.get("behavior_seed"))
        params = _valid_params(raw.get("params"))
        if seed is None or params is None:
            return None
        try:
            schema = int(raw.get("schema_version") or SCHEMA_VERSION)
        except (TypeError, ValueError):
            schema = SCHEMA_VERSION
        gen = str(raw.get("generator_version") or GENERATOR_VERSION)
        try:
            bounds = int(raw.get("bounds_version") or BOUNDS_VERSION)
        except (TypeError, ValueError):
            bounds = BOUNDS_VERSION
        return BehaviorProfile(
            schema_version=schema,
            generator_version=gen,
            behavior_seed=seed,
            params=params,
            bounds_version=bounds,
            session_seq=_coerce_session_seq(raw.get("session_seq")),
        )


@dataclass(frozen=True)
class ResolvedBehaviorConfig:
    """Values the behavior layer actually reads (no stealth re-read of globals)."""

    pause_scale: float
    pause_dispersion: float
    scroll_step_scale: float
    scroll_decay: float
    optional_action_weights: dict[str, float]
    max_session_elapsed_sec: float
    max_actions: int
    max_state_visits: int
    idle_wander_enabled: bool
    behavior_seed: int
    session_seq: int
    generator_version: str = GENERATOR_VERSION

    def optional_weight(self, key: str, default: float = 1.0) -> float:
        return float(self.optional_action_weights.get(key, default))


@dataclass
class BehaviorStreams:
    """Isolated RNG streams for profile / session / action classes."""

    profile_rng: random.Random
    session_rng: random.Random
    pause_rng: random.Random
    scroll_rng: random.Random
    optional_rng: random.Random


def _derive_rng(behavior_seed: int, *parts: str | int) -> random.Random:
    material = "|".join(["bp", str(int(behavior_seed)), *[str(p) for p in parts]])
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def make_behavior_streams(
    behavior_seed: int,
    session_seq: int,
    *,
    session_nonce: str | int | None = None,
) -> BehaviorStreams:
    """Build profile/session/substream RNGs isolated from fingerprint RNG."""
    nonce = session_nonce if session_nonce is not None else ""
    return BehaviorStreams(
        profile_rng=_derive_rng(behavior_seed, "profile"),
        session_rng=_derive_rng(behavior_seed, "session", int(session_seq), nonce),
        pause_rng=_derive_rng(behavior_seed, "pause", int(session_seq), nonce),
        scroll_rng=_derive_rng(behavior_seed, "scroll", int(session_seq), nonce),
        optional_rng=_derive_rng(behavior_seed, "optional", int(session_seq), nonce),
    )


def effective_params_hash(profile: BehaviorProfile | dict[str, Any]) -> str:
    """Hash of effective params only (no profile_id / timestamps / session_seq)."""
    if isinstance(profile, BehaviorProfile):
        params = profile.params
        gen = profile.generator_version
        bounds = profile.bounds_version
        seed = profile.behavior_seed
    else:
        params = profile.get("params") or {}
        gen = profile.get("generator_version") or GENERATOR_VERSION
        bounds = profile.get("bounds_version") or BOUNDS_VERSION
        seed = profile.get("behavior_seed")
    payload = {
        "generator_version": gen,
        "bounds_version": bounds,
        "behavior_seed": seed,
        "params": params,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def idle_wander_enabled(
    *,
    flag: bool | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    """Idle ambient wander DEFAULT OFF. Enable via flag or CLOAKCLI_IDLE_WANDER=1."""
    if flag is True:
        return True
    if flag is False:
        return False
    envmap = env if env is not None else os.environ
    raw = str(envmap.get(ENV_IDLE_WANDER, "") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _budget_from_env() -> tuple[float, int, int]:
    elapsed = DEFAULT_MAX_SESSION_ELAPSED_SEC
    actions = DEFAULT_MAX_ACTIONS
    visits = DEFAULT_MAX_STATE_VISITS
    raw_e = os.environ.get(ENV_MAX_ELAPSED)
    raw_a = os.environ.get(ENV_MAX_ACTIONS)
    raw_v = os.environ.get(ENV_MAX_STATE_VISITS)
    if raw_e is not None and str(raw_e).strip() != "":
        try:
            elapsed = float(str(raw_e).strip())
        except ValueError:
            pass
    if raw_a is not None and str(raw_a).strip() != "":
        try:
            actions = int(str(raw_a).strip())
        except ValueError:
            pass
    if raw_v is not None and str(raw_v).strip() != "":
        try:
            visits = int(str(raw_v).strip())
        except ValueError:
            pass
    elapsed = _clamp(elapsed, 5.0, BUDGET_ELAPSED_CEILING_SEC)
    actions = int(_clamp(actions, 1, BUDGET_ACTIONS_CEILING))
    visits = int(_clamp(visits, 1, BUDGET_STATE_VISITS_CEILING))
    return float(elapsed), int(actions), int(visits)


def resolve_behavior_config(
    profile: BehaviorProfile,
    *,
    idle_wander: bool | None = None,
    max_session_elapsed_sec: float | None = None,
    max_actions: int | None = None,
    max_state_visits: int | None = None,
) -> ResolvedBehaviorConfig:
    env_elapsed, env_actions, env_visits = _budget_from_env()
    elapsed = env_elapsed if max_session_elapsed_sec is None else float(max_session_elapsed_sec)
    actions = env_actions if max_actions is None else int(max_actions)
    visits = env_visits if max_state_visits is None else int(max_state_visits)
    # Hard ceilings — profile must not raise budgets.
    elapsed = min(float(elapsed), BUDGET_ELAPSED_CEILING_SEC)
    actions = min(int(actions), BUDGET_ACTIONS_CEILING)
    visits = min(int(visits), BUDGET_STATE_VISITS_CEILING)
    p = profile.params
    return ResolvedBehaviorConfig(
        pause_scale=float(p["pause_scale"]),
        pause_dispersion=float(p["pause_dispersion"]),
        scroll_step_scale=float(p["scroll_step_scale"]),
        scroll_decay=float(p["scroll_decay"]),
        optional_action_weights=dict(p["optional_action_weights"]),
        max_session_elapsed_sec=float(elapsed),
        max_actions=int(actions),
        max_state_visits=int(visits),
        idle_wander_enabled=idle_wander_enabled(flag=idle_wander),
        behavior_seed=int(profile.behavior_seed),
        session_seq=int(profile.session_seq),
        generator_version=str(profile.generator_version),
    )


def ensure_behavior_profile(
    meta_path: Path,
    *,
    regenerate: bool = False,
    rng: random.Random | None = None,
) -> BehaviorProfile:
    """Load profile.json; mint/persist behavior_profile if missing/invalid.

    Restart-stable: existing valid block is returned unchanged (unless regenerate).
    Preserves fingerprint_seed and all other fields.

    Distinguishes missing file (mint OK) from corrupt/unreadable/non-object
    existing file (raises BehaviorProfileError; file left untouched).
    """
    meta_path = Path(meta_path)
    data = _read_profile_json(meta_path)
    if data is None:
        data = {}

    existing_raw = data.get(_FIELD)
    if isinstance(existing_raw, dict) and not regenerate:
        existing = BehaviorProfile.from_dict(existing_raw)
        if existing is not None:
            return existing

    mint_rng = rng if rng is not None else random.Random()
    seed = mint_behavior_seed(rng=mint_rng)
    param_rng = _derive_rng(seed, "mint_params")
    params = sample_params_joint(param_rng)
    profile = BehaviorProfile(
        schema_version=SCHEMA_VERSION,
        generator_version=GENERATOR_VERSION,
        behavior_seed=seed,
        params=params,
        bounds_version=BOUNDS_VERSION,
        session_seq=0,
    )
    data[_FIELD] = profile.to_dict()
    _atomic_write_json(meta_path, data)
    return profile


def bump_session_seq(meta_path: Path) -> BehaviorProfile:
    """Increment session_seq so restarts do not replay the same prefix.

    Caller MUST hold ProfileSessionLock for this profile (same-profile mutex).
    Corrupt/unreadable profile raises BehaviorProfileError; file left untouched.
    """
    meta_path = Path(meta_path)
    data = _read_profile_json(meta_path)
    if data is None:
        # Missing file: mint then bump under the same write.
        profile = ensure_behavior_profile(meta_path)
        data = _read_profile_json(meta_path)
        if data is None:
            raise BehaviorProfileError(f"profile_json_missing_after_mint:{meta_path.name}")
    else:
        existing_raw = data.get(_FIELD)
        profile = None
        if isinstance(existing_raw, dict):
            profile = BehaviorProfile.from_dict(existing_raw)
        if profile is None:
            profile = ensure_behavior_profile(meta_path)
            data = _read_profile_json(meta_path)
            if data is None:
                raise BehaviorProfileError(f"profile_json_missing_after_mint:{meta_path.name}")
    updated = BehaviorProfile(
        schema_version=profile.schema_version,
        generator_version=profile.generator_version,
        behavior_seed=profile.behavior_seed,
        params=dict(profile.params),
        bounds_version=profile.bounds_version,
        session_seq=int(profile.session_seq) + 1,
    )
    data[_FIELD] = updated.to_dict()
    _atomic_write_json(meta_path, data)
    return updated


@dataclass
class SessionBudget:
    """Enforce max elapsed / actions / state visits; expose end_reason."""

    max_session_elapsed_sec: float
    max_actions: int
    max_state_visits: int
    t0: float = 0.0
    actions: int = 0
    state_visits: dict[str, int] = field(default_factory=dict)
    end_reason: str | None = None
    _clock: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._clock is None:
            self._clock = time.monotonic
        if not self.t0:
            self.t0 = float(self._clock())

    def elapsed_sec(self) -> float:
        return float(self._clock()) - float(self.t0)

    def remaining_sec(self) -> float:
        """Seconds left before max_session_elapsed (0 if exhausted/elapsed)."""
        return max(0.0, float(self.max_session_elapsed_sec) - self.elapsed_sec())

    def remaining_ms(self) -> int:
        return int(self.remaining_sec() * 1000.0)

    def exhausted(self) -> bool:
        return self.end_reason is not None

    def check(self) -> str | None:
        if self.end_reason:
            return self.end_reason
        if self.elapsed_sec() >= float(self.max_session_elapsed_sec):
            self.end_reason = "budget_elapsed"
            return self.end_reason
        if self.actions >= int(self.max_actions):
            self.end_reason = "budget_actions"
            return self.end_reason
        total_visits = sum(self.state_visits.values())
        if total_visits >= int(self.max_state_visits):
            self.end_reason = "budget_state_visits"
            return self.end_reason
        return None

    def record_action(self) -> str | None:
        """Permit one execution if actions < max_actions.

        Allows the Nth action when max_actions=N; rejects N+1.
        Returns end_reason iff the action is NOT allowed (does not consume).
        """
        reason = self.check()
        if reason:
            return reason
        # actions is count of already-executed; permit while actions < max.
        if self.actions >= int(self.max_actions):
            self.end_reason = "budget_actions"
            return self.end_reason
        self.actions += 1
        return None

    def visit_state(self, name: str, *, cap: int | None = None) -> bool:
        """Return True if visit allowed and consume one; False if rejected (no consume).

        Allows the Nth visit when max_state_visits/cap = N; rejects N+1.
        Rejected attempts do not increment counters.
        """
        if self.check():
            return False
        key = str(name)
        n = int(self.state_visits.get(key, 0))
        if cap is not None and n >= int(cap):
            return False
        total = sum(self.state_visits.values())
        if total >= int(self.max_state_visits):
            self.end_reason = "budget_state_visits"
            return False
        self.state_visits[key] = n + 1
        return True


class ProfileSessionLock:
    """Same-profile mutex via exclusive file lock (fcntl). Non-blocking try."""

    def __init__(self, meta_path: Path) -> None:
        self.meta_path = Path(meta_path)
        self.lock_path = self.meta_path.parent / ".nurture_session.lock"
        self._fh: Any = None

    def acquire(self, *, blocking: bool = False) -> bool:
        import fcntl

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.lock_path, "a+", encoding="utf-8")
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(self._fh.fileno(), flags)
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(f"pid={os.getpid()}\n")
            self._fh.flush()
            return True
        except BlockingIOError:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
            return False

    def release(self) -> None:
        if self._fh is None:
            return
        import fcntl

        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None

    def __enter__(self) -> ProfileSessionLock:
        if not self.acquire(blocking=False):
            raise RuntimeError("profile_session_busy")
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()
