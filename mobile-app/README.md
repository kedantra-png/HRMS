# BB Hegde HRMS — Android shell (internal APK)

WebView wrapper for **internal** college distribution. Full guide: [docs/INTERNAL_ANDROID_APK.md](../docs/INTERNAL_ANDROID_APK.md)

## Faculty on mobile data / home Wi‑Fi?

**Yes**, if `server.url` is a **public HTTPS** address (e.g. `https://hrms.bbhegdecollege.com`), not `192.168.x.x`.

Test: open that URL in Chrome on a phone with **mobile data only** (college Wi‑Fi off). If it works, build the APK.

LAN-only example: see `capacitor.config.lan.example.json`.

## Quick start

1. **Edit server URL** in `capacitor.config.json` (`server.url` → your **public** HRMS HTTPS address).
2. Install tools: [Node.js LTS](https://nodejs.org), [Android Studio](https://developer.android.com/studio).
3. Run:

```bash
cd mobile-app
npm install
npx cap add android    # first time only
npx cap sync android
npx cap open android
```

4. Android Studio → **Build → Build APK(s)** → share `android/app/build/outputs/apk/debug/app-debug.apk`.

## HTTP on college LAN

`capacitor.config.json` includes `"cleartext": true` for `http://` URLs. After changing config:

```bash
npx cap sync android
```

If cleartext still fails, `android/app/src/main/AndroidManifest.xml` should reference `@xml/network_security_config` (added on first `cap add android` — see Capacitor docs or IT).

## Scripts

| Command | Purpose |
|---------|---------|
| `npm run sync` | Copy config into Android project |
| `npm run android` | Open Android Studio |
| `npm run build:apk` | Debug APK via Gradle (after `cap add android`) |

## Icons

Replace launcher icons in `android/app/src/main/res/` or use Android Studio **Image Asset** with `static/img/logo-removebg-preview.png` from the main HRMS project.
