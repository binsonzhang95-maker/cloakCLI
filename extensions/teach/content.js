/* CloakCLI Teach content script.
 * Injected only for allowlisted http(s) origins (never <all_urls>).
 * M3: richer DOM capture during human takeover (click/fill/press/select).
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
  let takeover = false;
  let lastObservationId = "";

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
      return;
    }
    if (msg && msg.type === "takeover") {
      takeover = !!msg.on;
      sendResponse({ ok: true, takeover });
      return;
    }
  });

  function install() {
    document.addEventListener("click", onClick, true);
    document.addEventListener("change", onChange, true);
    document.addEventListener("keydown", onKey, true);
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

  function inShadow(el) {
    try {
      const root = el.getRootNode && el.getRootNode();
      return !!(root && root !== document && typeof ShadowRoot !== "undefined" && root instanceof ShadowRoot);
    } catch {
      return false;
    }
  }

  function frameName() {
    try {
      if (window !== window.top) return "iframe";
    } catch {
      return "iframe";
    }
    return "main";
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
    lastObservationId = observation_id;
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
    const candidates = {};
    const uniqueMap = {};
    const add = (key, s) => {
      if (!s) return;
      const u = unique(s);
      uniqueMap[key] = u;
      if (u && !selectors.includes(s)) selectors.push(s);
      if (!candidates[key]) candidates[key] = s;
    };
    if (el.id) add("id", "#" + CSS.escape(el.id));
    const testid = el.getAttribute("data-testid") || el.getAttribute("data-test");
    if (testid) {
      const key = el.hasAttribute("data-testid") ? "data-testid" : "data-test";
      add("testid", `[${key}="${cssAttr(testid)}"]`);
    }
    if (el.getAttribute("name")) {
      add("name", `${el.tagName.toLowerCase()}[name="${cssAttr(el.getAttribute("name"))}"]`);
    }
    const ac = el.getAttribute("autocomplete");
    if (ac && ac !== "off") add("autocomplete", `[autocomplete="${cssAttr(ac)}"]`);
    const aria = el.getAttribute("aria-label");
    const role = el.getAttribute("role") || implicitRole(el);
    if (role && aria) {
      add("role_name", `[role="${cssAttr(role)}"][aria-label="${cssAttr(aria)}"]`);
    }
    if (aria) add("aria", `${el.tagName.toLowerCase()}[aria-label="${cssAttr(aria)}"]`);
    if (aria) add("label", `[aria-label="${cssAttr(aria)}"]`);
    const text = stableText(el);
    if (text) {
      add("text", `${el.tagName.toLowerCase()}:has-text("${cssAttr(text)}")`);
    }
    const path = cssPath(el);
    if (path) {
      candidates.css_path = path;
      uniqueMap.css = unique(path);
    }
    const primary = selectors[0] || path;
    if (primary && !selectors.includes(primary)) selectors.unshift(primary);
    return {
      selector: primary,
      selectors,
      unstable: isUnstable(primary),
      selector_candidates: candidates,
      candidate_unique: uniqueMap,
    };
  }

  function implicitRole(el) {
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "button") return "button";
    if (tag === "a") return "link";
    if (tag === "input") {
      const t = (el.type || "text").toLowerCase();
      if (t === "submit" || t === "button") return "button";
    }
    if (tag === "select") return "combobox";
    return el.getAttribute("role") || "";
  }

  function stableText(el) {
    if (isSecretField(el)) return "";
    let t = (el.getAttribute("aria-label") || el.innerText || el.textContent || "").trim();
    t = t.replace(/\s+/g, " ");
    if (!t || t.length > 48) return "";
    if (/^[0-9.$€£¥%]+$/.test(t)) return "";
    return t;
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

  function buildEvent(kind, el, extra) {
    const bundle = el instanceof Element ? selectorBundle(el) : { selector: "", selectors: [], unstable: true, selector_candidates: {}, candidate_unique: {} };
    const role = el instanceof Element ? implicitRole(el) : "";
    const label = el instanceof Element ? (el.getAttribute("aria-label") || "").trim() : "";
    let text = "";
    if (el instanceof Element && !isSecretField(el)) {
      text = stableText(el);
    }
    const ev = {
      kind,
      ts: new Date().toISOString(),
      url: location.href,
      origin: location.origin,
      tag: el instanceof Element ? (el.tagName || "").toLowerCase() : "",
      role,
      text,
      label: label || text,
      accessible_name: label || text,
      selector: bundle.selector,
      selectors: bundle.selectors,
      unstable: bundle.unstable,
      selector_candidates: bundle.selector_candidates,
      candidate_unique: bundle.candidate_unique,
      viewport: { width: window.innerWidth || 0, height: window.innerHeight || 0 },
      frame: frameName(),
      shadow: el instanceof Element ? inShadow(el) : false,
      observation_id: lastObservationId,
      field: el instanceof Element ? fieldHint(el) : undefined,
    };
    if (extra) Object.assign(ev, extra);
    return ev;
  }

  function emit(ev) {
    chrome.runtime.sendMessage({ type: "record", event: ev, takeover: takeover });
  }

  function onClick(ev) {
    const el =
      ev.target && ev.target.closest
        ? ev.target.closest("a,button,input,select,textarea,[role='button'],[role='link']")
        : ev.target;
    if (!(el instanceof Element)) return;
    if (isTypingField(el) && el.tagName.toLowerCase() !== "select") return;
    const extra = {};
    if (typeof ev.clientX === "number" && typeof ev.clientY === "number") {
      extra.x = Math.round(ev.clientX);
      extra.y = Math.round(ev.clientY);
      extra.coords = { x: extra.x, y: extra.y };
    }
    if (el.tagName.toLowerCase() === "select") {
      extra.kind = "click";
    }
    emit(buildEvent("click", el, extra));
  }

  function onChange(ev) {
    const el = ev.target;
    if (!(el instanceof Element)) return;
    const tag = el.tagName.toLowerCase();
    if (tag === "select") {
      emit(
        buildEvent("select", el, {
          value: String(el.value || ""),
        })
      );
      return;
    }
    if (!isTypingField(el)) return;
    const secret = isSecretField(el);
    let value = "";
    if ("value" in el) value = String(el.value);
    const extra = {
      field: fieldHint(el),
    };
    if (secret) {
      extra.value = "";
      extra.redacted = true;
      extra.value_len = value.length;
    } else {
      extra.value = value;
    }
    emit(buildEvent("input", el, extra));
  }

  function onKey(ev) {
    if (!takeover) return;
    const key = ev.key || "";
    const keep = ["Enter", "Tab", "Escape", "Esc"];
    if (!keep.includes(key)) return;
    const el = ev.target instanceof Element ? ev.target : document.activeElement;
    emit(
      buildEvent("keypress", el, {
        key: key === "Esc" ? "Escape" : key,
      })
    );
  }
})();
