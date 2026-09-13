/* CloakCLI Teach content script.
 * Injected only for allowlisted http(s) origins (never <all_urls>).
 */
(function () {
  if (window.__cloakcliTeachInjected) return;
  window.__cloakcliTeachInjected = true;
  try {
    document.documentElement.setAttribute("data-cloakcli-teach-injected", "1");
  } catch {
    /* document may not be ready */
  }

  const origin = location.origin;
  if (!origin.startsWith("http://") && !origin.startsWith("https://")) {
    return;
  }

  let allowed = false;

  chrome.runtime.sendMessage({ type: "allowOrigin?", origin }, (ok) => {
    if (chrome.runtime.lastError || !ok) return;
    allowed = true;
    install();
    sendPageState();
  });

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!allowed) {
      sendResponse(null);
      return;
    }
    if (msg && msg.type === "collectPageState") {
      sendResponse(collectPageState());
    }
  });

  function install() {
    document.addEventListener("click", onClick, true);
    document.addEventListener("change", onChange, true);
    window.addEventListener("popstate", sendPageState);
    window.addEventListener("hashchange", sendPageState);
  }

  function isSecretField(el) {
    if (!(el instanceof Element)) return false;
    const t = (el.getAttribute("type") || el.type || "").toLowerCase();
    if (t === "password") return true;
    const hay = [
      el.getAttribute("name") || "",
      el.id || "",
      el.getAttribute("autocomplete") || "",
      t,
    ]
      .join(" ")
      .toLowerCase();
    return /password|passwd|secret|token|authorization|cookie|credential/.test(hay);
  }

  function collectPageState() {
    const clickable = [];
    const els = document.querySelectorAll(
      "a,button,input,select,[role='button'],[role='link']"
    );
    for (const el of els) {
      if (clickable.length >= 40) break;
      if (!(el instanceof Element)) continue;
      const bundle = selectorBundle(el);
      let text = (el.innerText || el.getAttribute("aria-label") || "").trim();
      if (isSecretField(el)) text = "[REDACTED]";
      if (text.length > 80) text = text.slice(0, 80);
      clickable.push({
        tag: (el.tagName || "").toLowerCase(),
        role: el.getAttribute("role") || "",
        text,
        selector: bundle.selector,
      });
    }
    const observation_id =
      "obs-" +
      (crypto.randomUUID
        ? crypto.randomUUID()
        : String(Date.now()) + "-" + String(Math.random()).slice(2, 10));
    return {
      url: location.href,
      origin: location.origin,
      title: document.title || "",
      viewport: { width: window.innerWidth || 0, height: window.innerHeight || 0 },
      observation_id,
      clickable,
    };
  }

  function sendPageState() {
    if (!allowed) return;
    chrome.runtime.sendMessage({ type: "page_state", state: collectPageState() });
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

  function isUnstable(sel) {
    if (!sel) return true;
    if (/:nth-(?:child|of-type)/i.test(sel)) return true;
    if ((sel.match(/>/g) || []).length >= 3) return true;
    if (sel.length > 80) return true;
    return false;
  }

  function selectorBundle(el) {
    const selectors = [];
    const add = (s) => {
      if (s && unique(s) && !selectors.includes(s)) selectors.push(s);
    };
    if (el.id) add("#" + CSS.escape(el.id));
    const testid = el.getAttribute("data-testid") || el.getAttribute("data-test");
    if (testid) {
      const key = el.hasAttribute("data-testid") ? "data-testid" : "data-test";
      add(`[${key}="${cssAttr(testid)}"]`);
    }
    if (el.getAttribute("name")) {
      add(`${el.tagName.toLowerCase()}[name="${cssAttr(el.getAttribute("name"))}"]`);
    }
    const ac = el.getAttribute("autocomplete");
    if (ac && ac !== "off") add(`[autocomplete="${cssAttr(ac)}"]`);
    const aria = el.getAttribute("aria-label");
    if (aria) add(`${el.tagName.toLowerCase()}[aria-label="${cssAttr(aria)}"]`);
    const primary = selectors[0] || cssPath(el);
    if (primary && !selectors.includes(primary)) selectors.unshift(primary);
    return {
      selector: primary,
      selectors,
      unstable: isUnstable(primary),
    };
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
      placeholder: el.getAttribute("placeholder") || "",
      testid: el.getAttribute("data-testid") || el.getAttribute("data-test") || "",
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
      const bundle = selectorBundle(el);
      chrome.runtime.sendMessage({
        type: "record",
        event: {
          kind: "click",
          selector: bundle.selector,
          selectors: bundle.selectors,
          unstable: bundle.unstable,
          role: el.getAttribute("role") || "",
          label: el.getAttribute("aria-label") || (el.innerText || "").trim().slice(0, 80),
        },
      });
    }
  }

  function onChange(ev) {
    const el = ev.target;
    if (!(el instanceof Element) || !isTypingField(el)) return;
    let value = "";
    if ("value" in el) value = String(el.value);
    const bundle = selectorBundle(el);
    chrome.runtime.sendMessage({
      type: "record",
      event: {
        kind: "input",
        selector: bundle.selector,
        selectors: bundle.selectors,
        unstable: bundle.unstable,
        value,
        field: fieldHint(el),
        label: el.getAttribute("aria-label") || "",
      },
    });
  }
})();
