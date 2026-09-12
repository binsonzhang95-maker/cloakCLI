import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

const appWindow = getCurrentWindow();

const term = new Terminal({
  cursorBlink: true,
  fontSize: 13,
  fontFamily: 'Menlo, Monaco, "Cascadia Mono", "Courier New", monospace',
  theme: {
    background: "#100e0c",
    foreground: "#f3e6d4",
    cursor: "#e08a3c",
    selectionBackground: "#e08a3c55",
    black: "#1a1613",
    red: "#d35c4a",
    green: "#7fbf6e",
    yellow: "#e0b04a",
    blue: "#6ea0d3",
    magenta: "#c48ad3",
    cyan: "#6ec4bf",
    white: "#f3e6d4",
  },
  allowProposedApi: false,
  convertEol: false,
});

const fitAddon = new FitAddon();
term.loadAddon(fitAddon);
term.open(document.getElementById("terminal"));

const sessionLabel = document.getElementById("session-label");
const statusBin = document.getElementById("status-bin");
const statusHome = document.getElementById("status-home");
const statusPty = document.getElementById("status-pty");
const setup = document.getElementById("setup");
const setupError = document.getElementById("setup-error");
const homeInput = document.getElementById("home-input");
const iconMaximize = document.getElementById("icon-maximize");
const iconRestore = document.getElementById("icon-restore");
const btnStart = document.getElementById("btn-start");
const btnStop = document.getElementById("btn-stop");
const btnRestart = document.getElementById("btn-restart");

let starting = false;
let stopping = false;
let running = false;
let lastExit = null;
let canLaunch = false;

function fit() {
  try {
    fitAddon.fit();
  } catch {
    // xterm can throw if the container is not yet measurable
  }
}

async function syncMaximizeIcon() {
  const maximized = await appWindow.isMaximized();
  iconMaximize.classList.toggle("hidden", maximized);
  iconRestore.classList.toggle("hidden", !maximized);
  document.getElementById("titlebar-maximize").title = maximized ? "Restore" : "Maximize";
}

document.getElementById("titlebar-minimize").addEventListener("click", () => {
  appWindow.minimize();
});
document.getElementById("titlebar-maximize").addEventListener("click", async () => {
  await appWindow.toggleMaximize();
  await syncMaximizeIcon();
});
document.getElementById("titlebar-close").addEventListener("click", () => {
  appWindow.close();
});
document.querySelector(".titlebar-drag").addEventListener("mousedown", (event) => {
  if (event.buttons !== 1) return;
  if (event.target.closest("button")) return;
  if (event.detail === 2) {
    appWindow.toggleMaximize().then(syncMaximizeIcon);
  }
});

window.addEventListener("resize", () => {
  fit();
  if (running) {
    invoke("pty_resize", { cols: term.cols, rows: term.rows }).catch(() => {});
  }
  syncMaximizeIcon();
});

new ResizeObserver(() => {
  fit();
  if (running) {
    invoke("pty_resize", { cols: term.cols, rows: term.rows }).catch(() => {});
  }
}).observe(document.getElementById("terminal"));

term.onData((data) => {
  if (!running) return;
  invoke("pty_write", { data }).catch(() => {});
});

function showSetup(message, currentHome) {
  setup.classList.remove("hidden");
  setupError.textContent = message || "";
  if (currentHome) homeInput.value = currentHome;
  homeInput.focus();
}

function hideSetup() {
  setup.classList.add("hidden");
  setupError.textContent = "";
}

document.getElementById("home-save").addEventListener("click", async () => {
  setupError.textContent = "";
  try {
    await invoke("set_home", { path: homeInput.value });
    hideSetup();
    await refreshAndStart();
  } catch (err) {
    setupError.textContent = String(err);
  }
});

homeInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    document.getElementById("home-save").click();
  }
});

function updateButtons() {
  const busy = starting || stopping;
  btnStart.disabled = busy || running || !canLaunch;
  btnStop.disabled = busy || !running;
  btnRestart.disabled = busy || !canLaunch;
}

function applyStatus(status) {
  canLaunch = Boolean(status.binary && status.home);
  statusBin.textContent = status.binary
    ? `bin: ${status.binary}`
    : `bin: missing (${status.binary_error || "not found"})`;
  statusHome.textContent = status.home
    ? `home: ${status.home}`
    : `home: unset (${status.home_error || "not set"})`;
  running = Boolean(status.running);
  if (running) {
    statusPty.textContent = "pty: running";
    sessionLabel.textContent = "cloakcli tui";
    lastExit = null;
  } else {
    statusPty.textContent = lastExit ? `pty: ${lastExit}` : "pty: stopped";
    sessionLabel.textContent =
      lastExit || status.binary_error || status.home_error || "idle";
  }
  updateButtons();
}

function noteExit(payload) {
  const code = payload && payload.code;
  const reason = (payload && payload.reason) || "exited";
  const message = (payload && payload.message) || "process exited";
  if (reason === "stopped") {
    lastExit = "stopped";
  } else if (code === null || code === undefined) {
    lastExit = reason === "error" ? "error" : "exited";
  } else {
    lastExit = `exited ${code}`;
  }
  if (starting) {
    return;
  }
  running = false;
  statusPty.textContent = `pty: ${lastExit}`;
  sessionLabel.textContent = lastExit;
  updateButtons();
  term.writeln("");
  term.writeln(
    `\x1b[90m[${message}${code === null || code === undefined ? "" : ` (code ${code})`}]\x1b[0m`,
  );
  term.writeln("\x1b[90mStart or Restart in the title bar to run again.\x1b[0m");
}

async function startPty({ reset, continueFrom } = {}) {
  if (!continueFrom && (starting || stopping || running)) return;
  if (!continueFrom) starting = true;
  updateButtons();
  try {
    const status = await invoke("shell_status");
    applyStatus(status);

    if (!status.binary) {
      term.reset();
      term.writeln("\x1b[31mcloakcli binary not found.\x1b[0m");
      term.writeln(status.binary_error || "");
      term.writeln("Set CLOAKCLI_BIN to an absolute path for development.");
      return;
    }
    if (!status.home) {
      term.reset();
      term.writeln("\x1b[33mCLOAKCLI_HOME is not set or invalid.\x1b[0m");
      term.writeln(status.home_error || "");
      showSetup(status.home_error, "");
      return;
    }

    hideSetup();
    if (reset) term.reset();
    fit();
    await invoke("pty_start", { cols: term.cols, rows: term.rows });
    lastExit = null;
    const next = await invoke("shell_status");
    applyStatus(next);
    term.focus();
  } catch (err) {
    term.writeln(`\r\n\x1b[31m${String(err)}\x1b[0m`);
    const message = String(err);
    if (message.toLowerCase().includes("cloakcli_home") || message.toLowerCase().includes("project root")) {
      showSetup(message, homeInput.value);
    }
    try {
      applyStatus(await invoke("shell_status"));
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
    await invoke("pty_stop");
    try {
      applyStatus(await invoke("shell_status"));
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
  // Keep `starting` set across stop so a late pty-exit cannot write into
  // the new session or clear running after the next spawn.
  starting = true;
  updateButtons();
  try {
    await invoke("pty_stop");
  } catch {
    // already stopped
  }
  running = false;
  await startPty({ reset: true, continueFrom: true });
}

async function refreshAndStart() {
  await startPty();
}

btnStart.addEventListener("click", () => startPty());
btnStop.addEventListener("click", () => stopPty());
btnRestart.addEventListener("click", () => restartPty());

async function boot() {
  await listen("pty-data", (event) => {
    if (typeof event.payload === "string") {
      term.write(event.payload);
    }
  });

  await listen("pty-exit", async (event) => {
    noteExit(event.payload || {});
    try {
      applyStatus(await invoke("shell_status"));
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
      statusPty.textContent = `pty: ${lastExit || "stopped"}`;
      sessionLabel.textContent = lastExit || "idle";
      updateButtons();
    }
    try {
      applyStatus(await invoke("shell_status"));
    } catch {
      updateButtons();
    }
  });

  fit();
  await syncMaximizeIcon();
  updateButtons();
  await refreshAndStart();
  term.focus();
}

boot();
