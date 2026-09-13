"""In-memory Playwright-like page for recover unit tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# 1x1 PNG
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


class FakeTimeoutError(Exception):
    pass


class FakeKeyboard:
    def __init__(self, page: "FakePage"):
        self.page = page

    def type(self, text: str, delay: int = 0) -> None:
        self.page.typed.append(text)
        if self.page.focused is not None:
            self.page.fields[self.page.focused] = (
                self.page.fields.get(self.page.focused, "") + text
            )

    def press(self, key: str) -> None:
        self.page.pressed.append(key)


class FakeMouse:
    def __init__(self, page: "FakePage"):
        self.page = page

    def click(self, x: int, y: int) -> None:
        self.page.coord_clicks.append((x, y))
        self.page._maybe_nav_on_click(None)

    def wheel(self, dx: int, dy: int) -> None:
        self.page.scrolls.append((dx, dy))


class FakeLocator:
    def __init__(self, page: "FakePage", sel: str):
        self.page = page
        self.sel = sel

    @property
    def first(self) -> "FakeLocator":
        return self

    def wait_for(self, state: str = "visible", timeout: int = 0) -> None:
        if self.sel not in self.page.elements:
            raise FakeTimeoutError(f"TimeoutError: waiting for {self.sel}")

    def inner_text(self, timeout: int = 0) -> str:
        if self.sel not in self.page.elements:
            raise FakeTimeoutError(f"TimeoutError: waiting for {self.sel}")
        return str(self.page.elements[self.sel].get("text", ""))

    def get_attribute(self, attr: str) -> str | None:
        el = self.page.elements.get(self.sel) or {}
        return el.get(attr)

    def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        if self.sel not in self.page.elements:
            raise FakeTimeoutError(f"TimeoutError: waiting for {self.sel}")
        self.page.scrolls.append(("into_view", self.sel))

    def bounding_box(self) -> dict[str, float] | None:
        if self.sel not in self.page.elements:
            return None
        return {"x": 10.0, "y": 10.0, "width": 80.0, "height": 20.0}


class FakePage:
    def __init__(self, url: str = "https://example.com/") -> None:
        self.url = url
        self.viewport_size = {"width": 1280, "height": 720}
        self.elements: dict[str, dict[str, Any]] = {
            "a": {"text": "More information", "href": "https://www.iana.org/domains/example"},
            "h1": {"text": "Example Domain"},
            "#more": {"text": "More information"},
        }
        self.fields: dict[str, str] = {}
        self.clicked: list[str] = []
        self.coord_clicks: list[tuple[int, int]] = []
        self.typed: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.gotos: list[str] = []
        self.scrolls: list[Any] = []
        self.pressed: list[str] = []
        self.selected: list[tuple[str, str]] = []
        self.screenshot_calls: list[dict[str, Any]] = []
        self.focused: str | None = None
        self._closed = False
        self.nav_on_click: dict[str, str] = {}
        self.keyboard = FakeKeyboard(self)
        self.mouse = FakeMouse(self)

    def is_closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True

    def screenshot(self, path: str | None = None, full_page: bool = False, **kwargs: Any) -> bytes:
        self.screenshot_calls.append(
            {"path": path, "full_page": bool(full_page), **kwargs}
        )
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(TINY_PNG)
        return TINY_PNG

    def select_option(self, sel: str, value: str, timeout: int = 0) -> None:
        if sel not in self.elements:
            raise FakeTimeoutError(f"TimeoutError: waiting for {sel}")
        self.selected.append((sel, value))
        self.focused = sel

    def click(self, sel: str, timeout: int = 0) -> None:
        if sel not in self.elements:
            raise FakeTimeoutError(f"TimeoutError: waiting for {sel}")
        self.clicked.append(sel)
        self.focused = sel
        self._maybe_nav_on_click(sel)

    def fill(self, sel: str, text: str, timeout: int = 0) -> None:
        if sel not in self.elements:
            raise FakeTimeoutError(f"TimeoutError: waiting for {sel}")
        self.filled.append((sel, text))
        self.fields[sel] = text
        self.focused = sel

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 0) -> None:
        self.gotos.append(url)
        self.url = url

    def wait_for_timeout(self, ms: int) -> None:
        return

    def locator(self, sel: str) -> FakeLocator:
        return FakeLocator(self, sel)

    def evaluate(self, script: str, arg: Any = None) -> Any:
        items = []
        for css, el in self.elements.items():
            items.append(
                {
                    "tag": "a" if css.startswith("a") else "div",
                    "text": el.get("text", ""),
                    "href": el.get("href"),
                    "css": css,
                    "bbox": {"x": 10, "y": 10, "w": 80, "h": 20},
                }
            )
        return {"title": "Example Domain", "url": self.url, "items": items}

    def _maybe_nav_on_click(self, sel: str | None) -> None:
        if sel and sel in self.nav_on_click:
            self.url = self.nav_on_click[sel]


class ScriptedProvider:
    def __init__(self, replies: list[str], before_complete=None):
        self.replies = list(replies)
        self.calls = 0
        self.before_complete = before_complete
        self.images: list[bool] = []
        self.image_bytes: list[int] = []

    def complete(self, cfg, messages, *, image_b64=None, timeout_sec=60):
        self.calls += 1
        attached = bool(image_b64)
        self.images.append(attached)
        self.image_bytes.append(len(image_b64) if image_b64 else 0)
        if self.before_complete:
            self.before_complete(self.calls)
        if not self.replies:
            return '{"schema_version":1,"action":"fail","reason":"no more scripted replies"}', 10
        return self.replies.pop(0), 10
