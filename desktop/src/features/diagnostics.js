import {
  listen,
  opsStatus,
  ptyResize,
  ptyStart,
  ptyStop,
  ptyWrite,
  shellStatus,
} from "../api.js";
import { setCatalog } from "../store.js";

let term = null;
let fitAddon = null;
let starting = false;
let stopping = false;
let running = false;
let lastExit = null;
let canLaunch = false;
let listenersBound = false;
let xtermCssLoaded = false;

function $(id) {
  return document.getElementById(id);
}

function updateButtons() {
  const busy = starting || stopping;
  const start = $("btn-start");
  const stop = $("btn-stop");
  const restart = $("btn-restart");
  if (!start) return;
  start.disabled = busy || running || !canLaunch;
  stop.disabled = busy || !running;
  restart.disabled = busy || !canLaunch;
}

function applyShell(status) {
  canLaunch = Boolean(status.binary && status.home);
  running = Boolean(status.running);
  const pty = $("diag-pty");
  if (pty) {
    if (running) pty.textContent = "pty: running";
    else pty.textContent = lastExit ? `pty: ${lastExit}` : "pty: stopped";
  }
  updateButtons();
}

function noteExit(payload) {
  const code = payload && payload.code;
  const reason = (payload && payload.reason) || "exited";
  const message = (payload && payload.message) || "process exited";
  if (reason === "stopped") lastExit = "stopped";
  else if (code === null || code === undefined) lastExit = reason === "error" ? "error" : "exited";
  else lastExit = `exited ${code}`;
  if (starting) return;
  running = false;
  const pty = $("diag-pty");
  if (pty) pty.textContent = `pty: ${lastExit}`;
  updateButtons();
  if (term) {
    term.writeln("");
    term.writeln(
      `\x1b[90m[${message}${code === null || code === undefined ? "" : ` (code ${code})`}]\x1b[0m`,
    );
    term.writeln("\x1b[90mStart or Restart to run cloakcli tui again.\x1b[0m");
  }
}

function fit() {
  if (!fitAddon) return;
  try {
    fitAddon.fit();
  } catch {
    // container not measurable yet
  }
}

export async function ensureDiagnostics() {
  if (term) {
    requestAnimationFrame(() => {
      fit();
      if (running) {
        ptyResize(term.cols, term.rows).catch(() => {});
      }
    });
    return;
  }

  if (!xtermCssLoaded) {
    await import("@xterm/xterm/css/xterm.css");
    xtermCssLoaded = true;
  }
  const [{ Terminal }, { FitAddon }] = await Promise.all([
    import("@xterm/xterm"),
    import("@xterm/addon-fit"),
  ]);

  term = new Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: 'ui-monospace, "Cascadia Mono", Menlo, Monaco, "Courier New", monospace',
    theme: {
      background: "#08090b",
      foreground: "#e8eaed",
      cursor: "#5ce1d6",
      selectionBackground: "#5ce1d655",
      black: "#0b0d10",
      red: "#e06c75",
      green: "#6ecf8b",
      yellow: "#e0b04a",
      blue: "#6ea0d3",
      magenta: "#c48ad3",
      cyan: "#5ce1d6",
      white: "#e8eaed",
    },
    allowProposedApi: false,
    convertEol: false,
  });
  fitAddon = new FitAddon();
  term.loadAddon(fitAddon);
  term.open($("terminal"));
  term.onData((data) => {
    if (!running) return;
    ptyWrite(data).catch(() => {});
  });

  new ResizeObserver(() => {
    fit();
    if (running) ptyResize(term.cols, term.rows).catch(() => {});
  }).observe($("terminal"));

  if (!listenersBound) {
    listenersBound = true;
    await listen("pty-data", (event) => {
      if (typeof event.payload === "string" && term) term.write(event.payload);
    });
    await listen("pty-exit", async (event) => {
      noteExit(event.payload || {});
      try {
        applyShell(await shellStatus());
        setCatalog({ status: await opsStatus() });
      } catch {
        running = false;
        updateButtons();
      }
    });
    await listen("pty-status", async (event) => {
      const payload = event.payload || {};
      if (!starting && payload.running === false) {
        running = false;
        if (payload.reason) {
          lastExit = payload.reason === "stopped" ? "stopped" : lastExit || payload.reason;
        }
        const pty = $("diag-pty");
        if (pty) pty.textContent = `pty: ${lastExit || "stopped"}`;
        updateButtons();
      }
      try {
        applyShell(await shellStatus());
      } catch {
        updateButtons();
      }
    });
  }

  $("btn-start").addEventListener("click", () => startPty());
  $("btn-stop").addEventListener("click", () => stopPty());
  $("btn-restart").addEventListener("click", () => restartPty());

  try {
    applyShell(await shellStatus());
  } catch {
    updateButtons();
  }
  fit();
}

async function startPty({ reset, continueFrom } = {}) {
  if (!continueFrom && (starting || stopping || running)) return;
  if (!continueFrom) starting = true;
  updateButtons();
  try {
    const status = await shellStatus();
    applyShell(status);
    if (!status.binary) {
      term.reset();
      term.writeln("\x1b[31mcloakcli binary not found.\x1b[0m");
      term.writeln(status.binary_error || "");
      return;
    }
    if (!status.home) {
      term.reset();
      term.writeln("\x1b[33mCLOAKCLI_HOME is not set or invalid.\x1b[0m");
      term.writeln(status.home_error || "");
      return;
    }
    if (reset) term.reset();
    fit();
    await ptyStart(term.cols, term.rows);
    lastExit = null;
    applyShell(await shellStatus());
    setCatalog({ status: await opsStatus() });
    term.focus();
  } catch (err) {
    term.writeln(`\r\n\x1b[31m${String(err)}\x1b[0m`);
    try {
      applyShell(await shellStatus());
    } catch {
      updateButtons();
    }
  } finally {
    starting = false;
    updateButtons();
  }
}

async function stopPty() {
  if (starting || stopping || !running) return;
  stopping = true;
  updateButtons();
  try {
    await ptyStop();
    try {
      applyShell(await shellStatus());
      setCatalog({ status: await opsStatus() });
    } catch {
      running = false;
      updateButtons();
    }
  } catch (err) {
    term.writeln(`\r\n\x1b[31m${String(err)}\x1b[0m`);
  } finally {
    stopping = false;
    updateButtons();
  }
}

async function restartPty() {
  if (starting || stopping || !canLaunch) return;
  starting = true;
  updateButtons();
  try {
    await ptyStop();
  } catch {
    // already stopped
  }
  running = false;
  await startPty({ reset: true, continueFrom: true });
}

export function focusDiagnostics() {
  if (term) term.focus();
}

window.addEventListener("resize", () => {
  if (!term) return;
  fit();
  if (running) ptyResize(term.cols, term.rows).catch(() => {});
});
