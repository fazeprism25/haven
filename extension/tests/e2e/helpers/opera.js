// Reuses your actual Opera GX profile (already logged into ChatGPT) instead
// of the fresh-profile interactive login in setup-login.js. Two modes,
// chosen by HAVEN_E2E_BROWSER (see fixtures.js's `context` fixture):
//
//   HAVEN_E2E_BROWSER=opera-gx        -- launches directly against your
//     real, live Opera GX user-data directory. Requires Opera GX to be
//     fully closed first -- see the empirical note on launchOperaGxContext
//     below.
//
//   HAVEN_E2E_BROWSER=opera-gx-copy   -- launches against a one-time,
//     read-only-on-the-source COPY of your profile (see copyOperaGxProfile /
//     ../copy-opera-profile.js), never touching the live one again.
//
// Neither mode performs, automates, or scripts a login -- both require a
// profile that is already authenticated, exactly as asked. Nothing here
// reads, decrypts, or transmits credentials or cookie contents; the
// filesystem copy is a byte-for-byte copy Windows Explorer could do too.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import { EXTENSION_PATH } from "./paths.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// No registry/App Paths lookup here (Opera GX doesn't reliably register
// one) -- instead, the same locations Opera's own installer actually uses,
// checked directly, plus an env var escape hatch for anything else. This is
// deliberately "search known locations", not "assume the one path I saw on
// one machine": override with HAVEN_E2E_OPERA_EXECUTABLE if yours differs.
function candidateInstallRoots() {
  const roots = [];
  if (process.env.LOCALAPPDATA) roots.push(path.join(process.env.LOCALAPPDATA, "Programs", "Opera GX"));
  if (process.env.ProgramFiles) roots.push(path.join(process.env.ProgramFiles, "Opera GX"));
  if (process.env["ProgramFiles(x86)"]) roots.push(path.join(process.env["ProgramFiles(x86)"], "Opera GX"));
  return roots;
}

export function findOperaGxExecutable() {
  if (process.env.HAVEN_E2E_OPERA_EXECUTABLE) {
    if (!fs.existsSync(process.env.HAVEN_E2E_OPERA_EXECUTABLE)) {
      throw new Error(`HAVEN_E2E_OPERA_EXECUTABLE points at a path that doesn't exist: ${process.env.HAVEN_E2E_OPERA_EXECUTABLE}`);
    }
    return process.env.HAVEN_E2E_OPERA_EXECUTABLE;
  }

  const checked = [];
  for (const root of candidateInstallRoots()) {
    // The install root's own opera.exe is a real, directly launchable
    // binary (not just a version-selector stub) -- confirmed against a real
    // install; Playwright can pass it CDP flags directly. Prefer it, and
    // only fall back to a versioned subfolder (e.g. "133.0.5932.56\opera.exe",
    // picking the newest by string sort, which matches these dotted version
    // strings closely enough) if that root binary is somehow absent.
    const rootExe = path.join(root, "opera.exe");
    checked.push(rootExe);
    if (fs.existsSync(rootExe)) return rootExe;

    if (fs.existsSync(root)) {
      const versionDirs = fs
        .readdirSync(root, { withFileTypes: true })
        .filter((e) => e.isDirectory() && /^\d/.test(e.name))
        .map((e) => e.name)
        .sort()
        .reverse();
      for (const dir of versionDirs) {
        const versionedExe = path.join(root, dir, "opera.exe");
        checked.push(versionedExe);
        if (fs.existsSync(versionedExe)) return versionedExe;
      }
    }
  }

  throw new Error(
    "Couldn't find an Opera GX install. Checked:\n" +
      checked.map((p) => `  - ${p}`).join("\n") +
      "\nIf it's installed somewhere else, set HAVEN_E2E_OPERA_EXECUTABLE to the full path to opera.exe."
  );
}

// Opera GX's user-data root on Windows -- %APPDATA%, not %LOCALAPPDATA%
// (the executable and the profile data live under different roots; this is
// Opera's own convention, same shape as regular Opera's "Opera Stable").
export function findOperaGxUserDataDir() {
  const dir =
    process.env.HAVEN_E2E_OPERA_USER_DATA_DIR ??
    path.join(process.env.APPDATA ?? "", "Opera Software", "Opera GX Stable");
  if (!fs.existsSync(dir)) {
    throw new Error(
      `Opera GX profile directory not found at ${dir}. If yours lives elsewhere, set HAVEN_E2E_OPERA_USER_DATA_DIR.`
    );
  }
  return dir;
}

// A profile is any subfolder of the user-data root containing a
// "Preferences" file -- the one reliable, version-independent signal
// Chromium-family browsers use, rather than parsing "Local State"'s JSON
// schema (profile.info_cache), which varies across versions and, on the
// install this was tested against, didn't hold a usable display name
// anyway. Most people only ever have "Default"; multiple profiles show up
// here as "Default", "Profile 1", etc. -- to tell which is which, open
// opera://version in each Opera GX profile and check "Profile Path", or
// match Opera's own profile-switcher name in its UI, and pass the matching
// folder name via HAVEN_E2E_OPERA_PROFILE.
export function listOperaGxProfiles(userDataDir = findOperaGxUserDataDir()) {
  return fs
    .readdirSync(userDataDir, { withFileTypes: true })
    .filter((e) => e.isDirectory() && fs.existsSync(path.join(userDataDir, e.name, "Preferences")))
    .map((e) => e.name);
}

// Chromium writes this file for as long as any process holds the profile
// open, on every OS -- the exact signal that made the empirical test this
// change is based on fail. Checked per-directory (not "is any opera.exe
// process running anywhere") so a copied profile (opera-gx-copy mode) is
// never blocked by your live daily browser running elsewhere.
export function isProfileLocked(userDataDir) {
  return fs.existsSync(path.join(userDataDir, "lockfile"));
}

// Launches directly against a real Opera GX profile. Confirmed empirically
// against a real, currently-running Opera GX install: Chromium detects the
// profile is already open and refuses to hand a second process real control
// of it -- the new process prints "Opening in existing browser session",
// forwards its command line to the already-running window, and exits
// immediately, so Playwright never gets a CDP connection and this throws a
// clear, fast error rather than hanging or corrupting anything. That's
// Chromium's own safety mechanism, not something this file adds -- it's
// exactly why isProfileLocked() is checked here first, with a clearer
// message pointing at the actual fix (close Opera GX, or use
// opera-gx-copy mode instead, which never touches the live directory).
export async function launchOperaGxContext({ headless = false, profileDirectory, userDataDir } = {}) {
  const executablePath = findOperaGxExecutable();
  const resolvedUserDataDir = userDataDir ?? findOperaGxUserDataDir();
  const profile = profileDirectory ?? process.env.HAVEN_E2E_OPERA_PROFILE ?? "Default";
  const profiles = listOperaGxProfiles(resolvedUserDataDir);

  if (!profiles.includes(profile)) {
    throw new Error(
      `Opera GX profile "${profile}" not found under ${resolvedUserDataDir}. ` +
        `Profiles found: ${profiles.join(", ") || "(none)"}. ` +
        "Set HAVEN_E2E_OPERA_PROFILE to the correct folder name."
    );
  }

  if (isProfileLocked(resolvedUserDataDir)) {
    throw new Error(
      `Opera GX's profile at ${resolvedUserDataDir} is currently open (its lockfile is present). ` +
        "Chromium refuses to let a second process automate an already-open profile -- fully quit Opera GX " +
        "(check the system tray/Task Manager, GX has a habit of staying resident) and try again, or run " +
        "`npm run test:e2e:opera-copy-profile` once and use HAVEN_E2E_BROWSER=opera-gx-copy instead, which " +
        "never touches your live browsing session again."
    );
  }

  return chromium.launchPersistentContext(resolvedUserDataDir, {
    executablePath,
    headless,
    args: [
      `--profile-directory=${profile}`,
      `--disable-extensions-except=${EXTENSION_PATH}`,
      `--load-extension=${EXTENSION_PATH}`,
    ],
  });
}

// Gitignored, alongside setup-login.js's own profile dir (see auth.js) --
// same "holds real session cookies, never commit it" reasoning.
export function defaultOperaCopyDestDir() {
  return path.resolve(__dirname, "../.auth/opera-gx-copy");
}

// One-time-per-refresh, read-only-on-the-source copy of a real Opera GX
// profile into this repo's gitignored tests/e2e/.auth/ area, so tests can
// automate that copy freely and indefinitely without ever locking (or
// risking) your real, live Opera GX session again. Safe to re-run any time
// your Opera GX session changes -- it fully replaces whatever was copied
// before, it just never touches the real profile it copied *from*.
//
// Two independent locks are checked, for two independent reasons:
// - the SOURCE (userDataDir) must be unlocked (Opera GX closed) for the
//   same reason a database backup wants the database quiesced first --
//   copying dozens of open SQLite files (Cookies, Login Data, Web Data,
//   ...) mid-write risks copying a torn, inconsistent snapshot, which could
//   silently drop the very session cookie you're trying to preserve.
// - the DESTINATION (resolvedDestDir) must also be unlocked -- if a
//   Playwright run is currently using this copy (HAVEN_E2E_BROWSER=
//   opera-gx-copy), it holds the exact same kind of Chromium lockfile
//   isProfileLocked() already knows how to detect; deleting/overwriting
//   those files out from under a running browser process would corrupt
//   its session or crash it mid-test.
export function copyOperaGxProfile({ userDataDir = findOperaGxUserDataDir(), profileDirectory, destDir } = {}) {
  const profile = profileDirectory ?? process.env.HAVEN_E2E_OPERA_PROFILE ?? "Default";
  if (isProfileLocked(userDataDir)) {
    throw new Error(
      `Opera GX's profile at ${userDataDir} is currently open. Fully quit Opera GX first -- ` +
        "copying its files while it's running risks a corrupted, inconsistent copy."
    );
  }
  if (!listOperaGxProfiles(userDataDir).includes(profile)) {
    throw new Error(`Opera GX profile "${profile}" not found under ${userDataDir}.`);
  }

  const resolvedDestDir = destDir ?? defaultOperaCopyDestDir();
  if (isProfileLocked(resolvedDestDir)) {
    throw new Error(
      `The existing copy at ${resolvedDestDir} is currently in use (its lockfile is present) -- ` +
        "a Playwright run is probably using it right now (HAVEN_E2E_BROWSER=opera-gx-copy). " +
        "Stop that run first, then re-run this to refresh the copy."
    );
  }

  console.log(`Copying from: ${userDataDir} (profile "${profile}")`);
  console.log(`Copying to:   ${resolvedDestDir}`);

  fs.rmSync(resolvedDestDir, { recursive: true, force: true });
  fs.mkdirSync(resolvedDestDir, { recursive: true });

  // "Local State" (profile list/metadata) lives at the user-data root,
  // alongside the profile subfolder itself -- Chromium expects both to be
  // present, at the same relative layout, for --profile-directory to work.
  fs.cpSync(path.join(userDataDir, "Local State"), path.join(resolvedDestDir, "Local State"));
  fs.cpSync(path.join(userDataDir, profile), path.join(resolvedDestDir, profile), { recursive: true });

  return resolvedDestDir;
}
