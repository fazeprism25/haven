// Just the extension's own root path, factored out of extension.js so
// opera.js can use it too without a circular import (extension.js will, in
// turn, dispatch to opera.js for HAVEN_E2E_BROWSER=opera-gx*).

import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const EXTENSION_PATH = path.resolve(__dirname, "../../..");
