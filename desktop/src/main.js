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

let starting = false;
let running = false;

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

function applyStatus(status) {
  statusBin.textContent = status.binary
    ? `bin: ${status.binary}`
    : `bin: missing (${status.binary_error || "not found"})`;
  statusHome.textContent = status.home
    ? `home: ${status.home}`
    : `home: unset (${status.home_error || "not set"})`;
  statusPty.textContent = `pty: ${status.running ? "running" : "stopped"}`;
  sessionLabel.textContent = status.running
    ? "cloakcli tui"
    : status.binary_error || status.home_error || "idle";
  running = Boolean(status.running);
}

async function refreshAndStart() {
  if (starting) return;
  starting = true;
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
    fit();
    await invoke("pty_start", { cols: term.cols, rows: term.rows });
    const next = await invoke("shell_status");
    applyStatus(next);
    term.focus();
  } catch (err) {
    term.writeln(`\r\n\x1b[31m${String(err)}\x1b[0m`);
    const message = String(err);
    if (message.toLowerCase().includes("cloakcli_home") || message.toLowerCase().includes("project root")) {
      showSetup(message, homeInput.value);
    }
  } finally {
    starting = false;
  }
}

async function boot() {
  await listen("pty-data", (event) => {
    if (typeof event.payload === "string") {
      term.write(event.payload);
    }
  });

  await listen("pty-exit", async (event) => {
    running = false;
    const payload = event.payload || {};
    const code = payload.code;
    const message = payload.message || "process exited";
    term.writeln("");
    term.writeln(
      `\x1b[90m[${message}${code === null || code === undefined ? "" : ` (code ${code})`}]\x1b[0m`,
    );
    try {
      applyStatus(await invoke("shell_status"));
    } catch {
      statusPty.textContent = "pty: stopped";
    }
  });

  fit();
  await syncMaximizeIcon();
  await refreshAndStart();
  term.focus();
}

boot();
