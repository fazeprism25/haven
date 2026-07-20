// One-time interactive setup: opens a real, headed Chromium window (with
// the Haven extension loaded, same as the test suite itself) against
// chatgpt.com and waits for you to log in by hand. The resulting cookies
// persist in the profile directory (see helpers/auth.js), so every
// subsequent `npm run test:e2e` run reuses this session instead of prompting
// again -- until it actually expires, at which point re-run this script.
//
// This is the ONLY place in the whole suite that ever touches a real
// ChatGPT login, and it never sees or stores a password itself -- the
// credentials go directly from you to chatgpt.com's own login page inside
// the browser window, the same as logging in normally. Run with:
//   npm run test:e2e:login

import readline from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { launchExtensionContext } from "./helpers/extension.js";
import { markLoggedIn } from "./helpers/auth.js";

async function main() {
  console.log("Opening a headed Chromium window with the Haven extension loaded...");
  const context = await launchExtensionContext({ headless: false });
  const page = await context.newPage();
  await page.goto("https://chatgpt.com/");

  console.log("\nLog into ChatGPT in the opened window.");
  console.log("Once you can see your conversation list / compose box, come back here and press Enter.\n");

  const rl = readline.createInterface({ input: stdin, output: stdout });
  await rl.question("Press Enter once you're logged in... ");
  rl.close();

  // chatgpt.com renders a real compose box even for a signed-out visitor
  // (anonymous chat), so this is only a best-effort sanity check, not the
  // thing that decides whether the suite trusts this profile -- that's the
  // Enter keypress above, a real human confirming they actually logged in.
  const stillLoggedOut = await page
    .getByRole("button", { name: /log in/i })
    .first()
    .isVisible()
    .catch(() => false);
  if (stillLoggedOut) {
    console.warn(
      "\nHeads up: this page still looks signed out (a \"Log in\" button is visible). " +
        "If you did log in, ChatGPT's UI may have changed and this check is stale -- proceeding anyway."
    );
  }

  markLoggedIn();
  await context.close();
  console.log("Saved. The E2E suite will now reuse this session -- run `npm run test:e2e`.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
