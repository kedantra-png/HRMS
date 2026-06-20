# Internal Android APK (College distribution)

Share the HRMS app as an **APK file** with faculty/staff only — no Google Play Store required.

Your Flask HRMS stays on the server; the APK is a small **WebView shell** that opens your site (same UI as mobile browser).

---

## Can faculty use their own network (mobile data / home Wi‑Fi)?

**Yes.** The APK does not “store” the college Wi‑Fi. It only opens a **website URL**. The phone uses whatever internet is active:

- College Wi‑Fi  
- Home broadband  
- Mobile data (Jio, Airtel, etc.)

**Requirement:** that URL must work **from the internet**, not only inside the college building.

| Server URL in APK | Works on faculty’s own network? |
|-------------------|----------------------------------|
| `http://192.168.1.50:8000` (LAN only) | **No** — only on college network |
| `https://hrms.bbhegdecollege.com` (public) | **Yes** — anywhere with internet |
| `https://apps.bbhegdecollege.com:8000` (if exposed) | **Yes** |

So for “use anywhere”, host HRMS on a **public address** (domain + HTTPS), then put that address in `mobile-app/capacitor.config.json`.

### How to make HRMS reachable from anywhere (pick one)

1. **College server + domain (best for you)**  
   - Run HRMS on the college server (`gunicorn` + `nginx`).  
   - IT points a subdomain to the server (e.g. `hrms.bbhegdecollege.com`).  
   - Enable **HTTPS** (Let’s Encrypt / college SSL).  
   - Firewall: allow port 443 from internet (or only from India if IT prefers).

2. **Cloud VPS** (DigitalOcean, AWS, Azure, etc.)  
   - Deploy Flask + MongoDB there.  
   - Faculty use APK on any network; data lives in cloud.

3. **VPN (middle option)**  
   - Server stays private; faculty install college **VPN** on phone, then open app.  
   - Technically “their network” + VPN tunnel to college.

4. **Temporary testing only** — ngrok / Cloudflare Tunnel  
   - Quick demo URL; not recommended for real faculty data long term.

After public URL works in **Chrome on mobile data** (not college Wi‑Fi), the same URL in the APK will work.

---

## What you need

| Item | Notes |
|------|--------|
| **PC** | Windows with Android Studio installed |
| **Server** | HRMS on a URL reachable from the **public internet** (for use on any network) |
| **URL** | **`https://your-college-domain/...`** (recommended), not `192.168.x.x` |
| **Node.js** | LTS from [nodejs.org](https://nodejs.org) (for Capacitor build) |

**Phones:** Android 8+. Users must allow **Install from unknown sources** for the app/browser you use to open the APK.

---

## Recommended: Capacitor shell (in this repo)

Project folder: [`mobile-app/`](../mobile-app/)

### 1. Deploy HRMS so phones can reach it (any network)

**Production (faculty on mobile data):**

```bash
# On server — behind nginx with HTTPS, example:
gunicorn -w 2 -b 127.0.0.1:8000 app:app
```

Public URL example: `https://hrms.bbhegdecollege.com`

**Test before building APK:** on a phone, turn **off college Wi‑Fi**, use **mobile data only**, open Chrome:

`https://YOUR_PUBLIC_URL/login`

If login works there, faculty can use the APK from home or anywhere.

**College LAN only (not for “own network” use):**

```bash
python app.py   # bind 0.0.0.0 for LAN testing
```

`http://192.168.x.x:8000` — APK works **only** on campus Wi‑Fi.

### 2. Set the app URL

Edit [`mobile-app/capacitor.config.json`](../mobile-app/capacitor.config.json):

```json
"server": {
  "url": "https://hrms.bbhegdecollege.com",
  "cleartext": false,
  "androidScheme": "https"
}
```

- **`https://`** + **`cleartext: false`** — use for public internet (faculty on any network).  
- **`http://192.168...`** + **`cleartext: true`** — campus LAN only (see `capacitor.config.lan.example.json` if you add one — or doc only).

Then run `npx cap sync android` and rebuild the APK.

### 3. Build the APK (one-time setup)

```bash
cd mobile-app
npm install
npx cap add android
npx cap sync android
npx cap open android
```

In **Android Studio**:

1. Wait for Gradle sync.
2. **Build → Build Bundle(s) / APK(s) → Build APK(s)**.
3. Debug APK path (typical):  
   `mobile-app/android/app/build/outputs/apk/debug/app-debug.apk`

Rename to something clear, e.g. `BBHegde-HRMS-v1.0.apk`.

### 4. Distribute internally

| Method | How |
|--------|-----|
| WhatsApp / Telegram | Send APK to staff groups (file size ~5–15 MB) |
| Google Drive / OneDrive | Upload → share link → “Anyone with link” |
| USB | Copy APK to phones from office PC |
| College website | Single download page with version + install steps |

**Install steps for staff (share this text):**

1. Download `BBHegde-HRMS-v1.0.apk`.
2. Open the file → if blocked, go to **Settings → Security → Install unknown apps** and allow your browser/files app.
3. Install → open **BB Hegde HRMS**.
4. Open the app — use **any internet** (mobile data or home Wi‑Fi) if HRMS uses a **public HTTPS URL**.  
   (College Wi‑Fi only needed if the APK was built with a private `192.168...` address.)

### 5. Updates

When you change the **website**, staff get updates automatically (app loads the server URL).

When you change the **APK** (app name, icon, package): build a new APK, bump version in `mobile-app/android/app/build.gradle`, redistribute.

---

## Release APK (optional, still internal)

Debug APK is fine for college-only use. For a slightly more trusted install:

```bash
# In Android Studio: Build → Generate Signed Bundle / APK → APK
# Create a keystore once, keep the password safe
```

Store the keystore file securely (IT office). You need it for every future update.

---

## Checklist before sharing APK

- [ ] Login works on phone browser using the same URL as `capacitor.config.json`
- [ ] Lecturer dashboard, leave, attendance pages load
- [ ] Profile photo upload works (camera/gallery)
- [ ] Staff chat / Socket.IO works on **mobile data** (not only college Wi‑Fi)
- [ ] Tested login with college Wi‑Fi **turned off**
- [ ] Server IP or hostname documented for IT (if IP changes, update config and rebuild APK)

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| White / blank screen | Wrong `server.url`; server not public; or still using `192.168...` while on mobile data |
| Works on Wi‑Fi but not mobile data | HRMS not on internet — use public HTTPS URL in config and rebuild APK |
| “Cleartext HTTP not permitted” | Set `"cleartext": true` in `capacitor.config.json` and sync; see `mobile-app` README |
| Login session lost | Ensure cookies allowed; avoid opening multiple WebView apps for same site |
| Chat not realtime | Server must support WebSocket; phones need stable Wi‑Fi |
| APK won’t install | Enable unknown sources; uninstall old test APK with same package name |

---

## Alternative: Android Studio WebView only

If you don’t want Node/Capacitor, create one Activity in Android Studio that loads:

`https://your-server/login`

Same internal distribution steps. Capacitor is easier to maintain in this repo.

---

## Package identity (do not change lightly)

| Field | Value in template |
|-------|-------------------|
| App ID | `com.bbhegdecollege.hrms` |
| App name | `BB Hegde HRMS` |

Changing `appId` after install = different app (users must uninstall old APK first).
