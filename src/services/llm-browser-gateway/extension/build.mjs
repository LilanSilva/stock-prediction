import { copyFile, mkdir } from "node:fs/promises";
await mkdir("dist", { recursive: true });
for (const name of ["manifest.json", "popup.html"]) await copyFile(name, `dist/${name}`);
console.log("Load the extension/dist folder through chrome://extensions > Load unpacked.");
