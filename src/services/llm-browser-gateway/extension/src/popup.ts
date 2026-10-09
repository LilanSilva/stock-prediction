export {};
const field = (id: string): HTMLInputElement => document.getElementById(id) as HTMLInputElement;
const display = document.getElementById("status") as HTMLElement;
const errorDisplay = document.getElementById("error") as HTMLElement;
(document.getElementById("extension-id") as HTMLElement).textContent = chrome.runtime.id;
async function action(message: unknown): Promise<void> {
  try {
    const response = await chrome.runtime.sendMessage(message);
    if (!response.ok) { errorDisplay.textContent = response.error; return; }
    const state = response.connected ? "Connected" : !response.paired ? "Pair this profile once" :
      response.connectionError || "Connecting automatically…";
    display.textContent = `${state}; ${response.jobs} pending jobs (v${response.extensionVersion})`;
  } catch {
    display.textContent = "Extension is restarting. Reopen this popup shortly.";
  }
}
field("pair").onclick = () => {
  errorDisplay.textContent = "";
  const key = field("key").value.trim();
  field("key").value = "";
  void action({type: "pair", key, profileId: field("profile").value.trim()});
};
for (const provider of ["chatgpt", "claude", "deepseek", "meta", "kimi", "gemini"]) {
  field(provider).onclick = () => void action({type: "designate", provider});
}
field("reset").onclick = () => void action({type: "reset"});
field("forget").onclick = () => void action({type: "forget"});
void action({type: "status"});
const refresh = setInterval(() => void action({type: "status"}), 1000);
window.addEventListener("unload", () => clearInterval(refresh));
