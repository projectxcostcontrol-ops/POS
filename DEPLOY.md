# Deploying pos-app

The app is **multi-tenant**: one deployment serves many separate restaurant
businesses. Each person who signs up either creates a new business (and owns
it) or joins an existing one through an invite link. All data lives under
`tenants/{tenant_id}/...` in Firestore, and the tenant is taken from the
signed-in user's own record - never from a request parameter - so one
business cannot address another's data even by guessing ids.

---

## 1. Real Firebase project (replaces the emulator)

1. Go to https://console.firebase.google.com -> Create project.
2. Enable **Firestore Database** (production mode is fine).
3. Enable **Authentication** -> Sign-in method -> **Email/Password** -> Enable.
   Without this, nobody can sign up at all.
4. Authentication -> Settings -> **Authorized domains** -> add the domain the
   frontend will be served from (e.g. `your-app.vercel.app`). Logins from a
   domain that isn't listed are rejected.
5. Project Settings -> Service Accounts -> **Generate new private key**.
   This downloads a JSON file - keep it secret, don't commit it.
6. Project Settings -> General -> "Your apps" -> add a **Web app** if you
   haven't. Copy the `apiKey`, `authDomain`, and `projectId` shown - the
   frontend needs them in step 4.

### Firestore security rules

Deploy `firestore.rules` (included in this repo), which denies **all** direct
client access to Firestore:

```bash
firebase deploy --only firestore:rules
```

This isn't over-caution. The web app never reads Firestore directly - it only
uses Firebase to sign in, and every piece of data goes through the backend,
which confines each request to the caller's own business. Leaving client
access open would let a signed-in user read other businesses' data straight
from Firestore and skip that check entirely. The backend uses the Admin SDK,
which these rules don't apply to, so locking clients out costs nothing.

---

## 2. Backend - deploy as a container

A `backend/Dockerfile` is included so the same container runs identically on
Koyeb, Northflank, Render, or a plain VPS.

Environment variables to set in the platform's dashboard:

| Variable | Value |
|---|---|
| `USE_FIREBASE_EMULATOR` | `false` |
| `FIREBASE_CREDENTIALS_JSON` | the *entire* service-account JSON from step 1.5, as one value |
| `SUPER_ADMIN_EMAILS` | your own email(s), comma-separated - see below |
| `GEMINI_API_KEY` | for AI invoice reading (optional) |
| `FIREBASE_STORAGE_BUCKET` | for receipt photos (optional, see last section) |
| `SYNC_INTERVAL_SECONDS` | optional fallback; the live value is set per business on its own Settings page |

Do **not** set `FIRESTORE_EMULATOR_HOST` or `FIREBASE_AUTH_EMULATOR_HOST` in
production - if either is present, the backend tries to verify logins against
a local emulator that isn't there.

`LOYVERSE_ACCESS_TOKEN` is not used in production. Each business enters its
own token on its own Settings page, and it's stored per tenant in Firestore.

### `SUPER_ADMIN_EMAILS`

Gates the read-only `/admin` overview (how many businesses use the system,
how many are active). It's checked against the email in the verified Firebase
token, not against anything stored in the database, so it can't be granted by
tampering with a business's records. Anyone not listed gets a 404 and never
sees the link. Leave it empty to disable the page entirely.

The admin view deliberately exposes only counts - there's no endpoint that
opens a customer's stock, recipes, or takings. The promise that each
restaurant's data is private has to hold against us too.

### Option A: Koyeb (no sleep on the free tier)

1. Push this repo to GitHub.
2. Koyeb dashboard -> Create Service -> GitHub -> select the repo.
3. Set the working directory / Dockerfile path to `backend`.
4. Koyeb auto-detects the `Dockerfile` and exposed port via `$PORT`.
5. Add the environment variables listed above.
6. Deploy. Koyeb gives you a URL like `https://pos-app-xxxx.koyeb.app`.

### Option B: Northflank (no forced sleep on the free tier either)

1. Push this repo to GitHub.
2. Northflank dashboard -> Create -> Service -> connect the repo.
3. Build type: Dockerfile, context/root: `backend`.
4. Add the same environment variables.
5. Northflank assigns a public URL automatically after deploy.

---

## 3. Frontend

Any static-site host works (Vercel, Netlify, Cloudflare Pages).

1. Build command: `npm install && npm run build`
2. Publish directory: `dist`
3. Environment variables:

| Variable | Value |
|---|---|
| `VITE_API_URL` | the backend URL from step 2 |
| `VITE_FIREBASE_API_KEY` | from Firebase Console (step 1.6) |
| `VITE_FIREBASE_AUTH_DOMAIN` | `your-project-id.firebaseapp.com` |
| `VITE_FIREBASE_PROJECT_ID` | `your-project-id` |

Leave `VITE_USE_AUTH_EMULATOR` **out entirely** (not set to `false`) in
production.

`frontend/vercel.json` is included so client-side routes don't 404 on
refresh. On other hosts, configure the equivalent SPA fallback (rewrite all
paths to `/index.html`) - without it, opening an invite link directly returns
a 404 instead of the signup screen.

---

## 4. First-time setup after deploying

1. Open the deployed frontend URL.
2. **Create the first business.** Sign up with your own email (the one in
   `SUPER_ADMIN_EMAILS` if you want the admin view), then give the business
   a name. You become its owner.
3. **Settings** -> paste your Loyverse access token -> **เชื่อมต่อ**. The
   token is saved per business in Firestore, not in any env var.
4. Pick the active branch once stores load.
5. Adjust the sync interval if you want (takes effect immediately, no
   redeploy).
6. **Users** -> invite staff. The app generates a link you copy and send
   however you like (LINE, chat, anything) - there's no email delivery to
   configure. The link works once and only for the email it was issued to.

Every further business that signs up repeats steps 2-6 for itself, with its
own Loyverse token and its own separate data.

---

## Local development

Three processes, three terminal windows:

```bash
# 1. Firebase emulator (Auth on 9099, Firestore on 8080)
firebase emulators:start

# 2. Backend
cd backend && source venv/bin/activate
uvicorn api.main:app --reload

# 3. Frontend
cd frontend && npm run dev
```

Requires Python 3.10+ (the code uses `str | None` type syntax) and
`backend/.env` + `frontend/.env` filled from the matching `.env.example`
files. The one setting that must agree across both: `FIREBASE_PROJECT_ID`
(backend) and `VITE_FIREBASE_PROJECT_ID` (frontend). A mismatch fails at
login with an "aud" claim error that doesn't obviously point at config.

Emulator data is wiped when you stop it, so you re-create a test business
each session. That's usually what you want when testing the signup flow.

---

## Why Koyeb/Northflank instead of Render

Render's free tier spins down after inactivity (cold starts of 30-60s, and
the background auto-sync loop stops while asleep). Koyeb and Northflank's
free tiers stay running continuously, which matters here since stock
deduction depends on that loop running on a schedule. Render still works
fine if you don't mind the manual "ซิงก์ตอนนี้" button covering the gaps.

---

## Receipt image storage - optional but recommended

Scanned invoice photos are kept for 7 days so you can visually double-check
the AI's reading if a number looks off, then auto-deleted. Setup:

1. Firebase Console -> **Storage** -> get started (creates the default
   bucket if you haven't used Storage yet).
2. Copy the bucket name (looks like `your-project-id.appspot.com`) and set it
   as `FIREBASE_STORAGE_BUCKET` in the backend's environment variables.
3. Set the 7-day auto-delete rule (one-time, in the console):
   Storage -> your bucket -> **Lifecycle** tab -> **Add a rule**
   - Condition: **Age** -> 7 days
   - Action: **Delete**

   Or via `gsutil`:
   ```bash
   cat > lifecycle.json << 'RULE'
   {"rule": [{"action": {"type": "Delete"}, "condition": {"age": 7}}]}
   RULE
   gsutil lifecycle set lifecycle.json gs://your-project-id.appspot.com
   ```

Images are stored under `tenant_id/store_id/...` so two businesses can never
collide on a path. Leave `FIREBASE_STORAGE_BUCKET` blank to skip image
storage entirely - the app still works, it just won't show the original photo
on the review screen.
