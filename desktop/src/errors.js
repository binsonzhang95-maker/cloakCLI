import { redactText } from "./redact.js";

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function formatError(err) {
  if (err == null) return "unknown error";
  if (typeof err === "string") return redactText(err);
  if (err && typeof err.message === "string") return redactText(err.message);
  return redactText(String(err));
}

/** Render a recoverable page failure. `onRetry` is optional. */
export function renderErrorBoundary(root, label, err, onRetry) {
  if (!root) return;
  const msg = formatError(err);
  root.innerHTML = `
    <div class="error-boundary" role="alert">
      <div class="page-kicker">ERROR BOUNDARY</div>
      <div class="page-title">${esc(label || "view")}</div>
      <p class="error-boundary-msg">${esc(msg)}</p>
      ${onRetry ? `<button type="button" class="ghost" data-retry>Retry</button>` : ""}
    </div>
  `;
  if (onRetry) {
    root.querySelector("[data-retry]")?.addEventListener("click", () => {
      try {
        onRetry();
      } catch (e2) {
        renderErrorBoundary(root, label, e2, onRetry);
      }
    });
  }
}

/** Call `renderFn(root)` and swap in an error boundary if it throws. */
export function safeRender(root, label, renderFn) {
  if (!root || typeof renderFn !== "function") return false;
  try {
    renderFn(root);
    return true;
  } catch (err) {
    renderErrorBoundary(root, label, err, () => safeRender(root, label, renderFn));
    return false;
  }
}

export function showFatal(err) {
  if (typeof document === "undefined") return;
  const el = document.getElementById("fatal");
  const msg = document.getElementById("fatal-msg");
  if (!el || !msg) return;
  msg.textContent = formatError(err);
  el.classList.remove("hidden");
}
