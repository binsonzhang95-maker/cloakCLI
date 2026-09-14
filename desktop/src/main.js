import { bootShell } from "./shell.js";

bootShell().catch((err) => {
  const label = document.getElementById("session-label");
  if (label) label.textContent = String(err);
  console.error(err);
});
