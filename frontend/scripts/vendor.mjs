import { mkdir, copyFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
await mkdir(new URL("vendor/", root), { recursive: true });
await copyFile(new URL("node_modules/chart.js/dist/chart.umd.js", root), new URL("vendor/chart.umd.js", root));
await copyFile(new URL("node_modules/chart.js/LICENSE.md", root), new URL("vendor/CHART-LICENSE.md", root));
console.log(`Bundled Chart.js in ${fileURLToPath(new URL("vendor/", root))}`);
