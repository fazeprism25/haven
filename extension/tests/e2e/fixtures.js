// Shared Playwright fixtures: every spec imports `test`/`expect` from here
// instead of "@playwright/test" directly, so extension-loading, the mock
// Haven server, and the login-profile check are set up/torn down exactly
// once per test, consistently, rather than each spec file reinventing it.

import { test as base, expect } from "@playwright/test";
import { launchExtensionContext, getExtensionId } from "./helpers/extension.js";
import { startMockServer } from "./helpers/mock-server.js";
import { hasAuthProfile } from "./helpers/auth.js";

export const test = base.extend({
  // Auto-fixture (no spec ever requests it directly): skips the test up
  // front, with an actionable message, rather than letting it fail 20+
  // seconds later on a compose-box timeout inside openChatGPT(). See
  // README's "Authentication" section.
  //
  // HAVEN_E2E_BROWSER=opera-gx(-copy) has no marker-file equivalent of its
  // own -- setting it at all IS the trust signal (see helpers/opera.js):
  // you're pointing this suite at a profile you're asserting is already
  // logged in, the same way you'd assert that by choosing to open Opera GX
  // yourself. hasAuthProfile()'s marker file only ever applies to the
  // default fresh-Chromium-profile path (setup-login.js).
  requireAuth: [
    async ({}, use) => {
      const usingOperaProfile = Boolean(process.env.HAVEN_E2E_BROWSER?.startsWith("opera-gx"));
      test.skip(
        !usingOperaProfile && !hasAuthProfile(),
        "No authenticated ChatGPT profile found. Run `npm run test:e2e:login` once, then re-run this suite " +
          "(or set HAVEN_E2E_BROWSER=opera-gx to reuse your real Opera GX login -- see README.md)."
      );
      await use(undefined);
    },
    { auto: true },
  ],

  context: async ({}, use) => {
    const context = await launchExtensionContext();
    await use(context);
    await context.close();
  },

  extensionId: async ({ context }, use) => {
    await use(await getExtensionId(context));
  },

  // Fresh mock server per test (own port-bound listener, own request log,
  // own review-id counter) so tests never see another test's leftover
  // state. A spec that needs non-default fixture data (e.g. a specific
  // /memory/preview response) overrides this fixture locally via
  // `test.extend({ mockServer: async ({}, use) => { ...startMockServer({...}); } })`
  // -- see remember.spec.js -- rather than mutating this shared default.
  mockServer: async ({}, use) => {
    const server = await startMockServer();
    await use(server);
    await server.close();
  },

  // The single ChatGPT tab most specs drive. Depends on mockServer so the
  // extension always has a live backend to talk to before any Haven button
  // is clicked.
  page: async ({ context, mockServer }, use) => {
    void mockServer;
    const page = await context.newPage();
    await use(page);
    await page.close();
  },
});

export { expect };
