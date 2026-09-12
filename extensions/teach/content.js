/* CloakCLI Teach content script.
 * Injected only for allowlisted http(s) origins (never <all_urls>).
 */
(function () {
  if (window.__cloakcliTeachInjected) return;
  window.__cloakcliTeachInjected = true;

  const origin = location.origin;
  if (!origin.startsWith("http://") && !origin.startsWith("https://")) {
    return;
  }

  chrome.runtime.sendMessage({ type: "allowOrigin?", origin }, (ok) => {
    if (chrome.runtime.lastError || !ok) return;
    install();
  });

  function install() {
    document.addEventListener("click", onClick, true);
    document.addEventListener("change", onChange, true);
  }

  function cssAttr(v) {
    return String(v).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  function unique(sel) {
    try {
      return document.querySelectorAll(sel).length === 1;
    } catch {
      return false;
    }
  }

  function cssPath(el) {
    if (!(el instanceof Element)) return "";
    if (el.id && unique("#" + CSS.escape(el.id))) {
      return "#" + CSS.escape(el.id);
    }
    const testid = el.getAttribute("data-testid") || el.getAttribute("data-test");
    if (testid) {
      const key = el.hasAttribute("data-testid") ? "data-testid" : "data-test";
      const sel = `[${key}="${cssAttr(testid)}"]`;
      if (unique(sel)) return sel;
    }
    if (el.getAttribute("name")) {
      const sel = `${el.tagName.toLowerCase()}[name="${cssAttr(el.getAttribute("name"))}"]`;
      if (unique(sel)) return sel;
    }
    const aria = el.getAttribute("aria-label");
    if (aria) {
      const sel = `${el.tagName.toLowerCase()}[aria-label="${cssAttr(aria)}"]`;
      if (unique(sel)) return sel;
    }
    const cls = [...el.classList].filter((c) => c && !/^[0-9]/.test(c)).slice(0, 2);
    if (cls.length) {
      const sel = el.tagName.toLowerCase() + "." + cls.map((c) => CSS.escape(c)).join(".");
      if (unique(sel)) return sel;
    }
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      let part = cur.tagName.toLowerCase();
      if (cur.id && unique("#" + CSS.escape(cur.id))) {
        parts.unshift("#" + CSS.escape(cur.id));
        break;
      }
      const parent = cur.parentElement;
      if (parent) {
        const same = [...parent.children].filter((c) => c.tagName === cur.tagName);
        if (same.length > 1) {
          part += `:nth-of-type(${same.indexOf(cur) + 1})`;
        }
      }
      parts.unshift(part);
      cur = parent;
    }
    return parts.join(" > ");
  }

  function fieldHint(el) {
    return {
      tag: (el.tagName || "").toLowerCase(),
      type: (el.getAttribute("type") || el.type || "").toLowerCase(),
      name: el.getAttribute("name") || "",
      id: el.id || "",
      autocomplete: el.getAttribute("autocomplete") || "",
    };
  }

  function isTypingField(el) {
    if (!(el instanceof Element)) return false;
    const tag = el.tagName.toLowerCase();
    if (tag === "textarea" || tag === "select") return true;
    if (tag !== "input") return false;
    const t = (el.type || "text").toLowerCase();
    return !["button", "submit", "reset", "checkbox", "radio", "file", "image", "hidden"].includes(
      t
    );
  }

  function onClick(ev) {
    const el = ev.target && ev.target.closest ? ev.target.closest("a,button,input,select,textarea,[role='button']") : ev.target;
    if (!(el instanceof Element)) return;
    if (!isTypingField(el)) {
      chrome.runtime.sendMessage({
        type: "record",
        event: { kind: "click", selector: cssPath(el) },
      });
    }
  }

  function onChange(ev) {
    const el = ev.target;
    if (!(el instanceof Element) || !isTypingField(el)) return;
    let value = "";
    if ("value" in el) value = String(el.value);
    chrome.runtime.sendMessage({
      type: "record",
      event: {
        kind: "input",
        selector: cssPath(el),
        value,
        field: fieldHint(el),
      },
    });
  }
})();
