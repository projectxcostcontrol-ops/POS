# pos-app backend

## Quick start (Mac) - one-click scripts

Two scripts at the project root do the manual steps for you:

1. **`setup.command`** - run once. Creates the backend venv, installs
   Python deps, creates `backend/.env` from the template, and runs
   `npm install` for the frontend.
2. **`start.command`** - run every time you want to work on this.
   Opens 3 Terminal windows automatically: Firebase emulator, backend
   (`uvicorn`), frontend (`npm run dev`).
3. Open the frontend, go to **Settings**, paste your Loyverse token,
   click "เชื่อมต่อ". The token now lives in Firestore, not `.env` -
   see below.

Double-click either file in Finder. macOS will likely block it the
first time - right-click the file, choose **Open**, then confirm in
the dialog that appears (only needed once per script). The first time
`start.command` runs, macOS may also ask permission for Terminal to
control Terminal via Automation - allow it.

## Testing

Two scripts, for two different jobs:

**`backend/tests/test_stock.py`** - checks the V2 stock logic is correct.
Runs entirely in memory, so no Firebase, emulator, or Loyverse token
needed:

```bash
cd backend
python tests/test_stock.py
```

Covers: movements summing to the right stock, physical counts storing a
delta instead of overwriting, negative-stock detection, weighted average
cost, month-by-month cost isolation, receiving updating stock and cost
together, migration not losing or doubling old stock, recipe deduction
on sale, and re-syncs not double-deducting. Exits non-zero on failure,
so it works as a CI step later.

**`backend/tests/seed_sample_data.py`** - fills a store with realistic
data so there's something to look at while clicking through the UI. This
one hits your actual running backend:

```bash
cd backend
python tests/seed_sample_data.py <store_id>              # local emulator
python tests/seed_sample_data.py <store_id> https://your-backend-url
```

Creates 5 materials, 2 deliveries a month apart at different prices (so
monthly average cost has something real to separate), and 4 recipes.
Recipe names need to match your real Loyverse menu items for sales
deduction to kick in - edit the script if yours differ.

**`backend/tests/test_vision.py`** - checks the AI invoice reading logic
(step 4.1). Also offline, no API key needed - it tests the JSON parsing
and provider-fallback behaviour with fakes:

```bash
cd backend
python tests/test_vision.py
```

Covers: JSON arriving wrapped in a code fence or with extra chatter,
messy numbers ("1,250.50", "12 kg", "฿250"), dropping non-product lines
(discounts, totals), falling through to the next provider on a rate
limit, and skipping providers that have no API key configured.

**`backend/tests/test_scan_invoice.py`** - points the scanner at a real
photo, so you can see whether it reads YOUR suppliers' invoices well.
The offline tests prove the parsing works; only this tells you the
reading is accurate:

```bash
cd backend
python tests/test_scan_invoice.py path/to/invoice.jpg           # direct to the AI
python tests/test_scan_invoice.py invoice.jpg <store_id> <url>  # through the backend
python tests/test_scan_invoice.py invoice.jpg --raw             # also show raw output
```

Needs `GEMINI_API_KEY` in `backend/.env` - get a free one (no credit
card) at https://aistudio.google.com/apikey. Nothing is written to
stock; this only reads. Lines the model wasn't confident about are
flagged, which is the same signal the app will highlight for review.

**`backend/tests/test_matching.py`** - checks the matching engine (step
4.2): supplier aliases, general aliases, fuzzy suggestions, and
learning. Also offline:

```bash
cd backend
python tests/test_matching.py
```

Covers: exact name/alias matches, supplier-scoped aliases taking
priority over general ones (and not leaking to other suppliers),
unmatched names getting ranked suggestions instead of a silent guess,
completely unrelated names getting no suggestions at all, and learning
from a confirmed match making the same wording auto-match next time.

**`backend/tests/test_draft_receiving.py`** - checks the draft flow
(step 4.3): scan result -> draft -> review/edit -> confirm or discard.
Also offline:

```bash
cd backend
python tests/test_draft_receiving.py
```

Covers: a draft holding the scan output plus per-item matches, a fully-
matched draft confirming and updating stock while deleting the draft, a
manually-picked match confirming correctly AND getting learned as an
alias, unmatched lines being skipped (not silently dropped or blocking
the rest), a draft with nothing matched producing no receiving,
discarding leaving stock untouched, and two drafts staying independent.

**`backend/tests/test_unit_conversion.py`** - checks unit handling
(step 4.4): recognizing Thai/English/shorthand spellings, safe weight
and volume conversions, and refusing to guess for count units. Also
offline:

```bash
cd backend
python tests/test_unit_conversion.py
```

Covers: normalizing spellings like "กก.", "KG", "EA" to the same
canonical unit, kg<->g and l<->ml converting with the price adjusted so
the total value is preserved, weight never mixing with volume, count
units (bottle/box/piece) never auto-converting into each other since
there's no universal size, and unconvertible/unrecognized units being
flagged on the item without dropping or corrupting it.

**`backend/tests/test_image_store.py`** - checks image storage (step
4.5): emulator mode skipping gracefully, missing/broken config failing
soft instead of crashing, and drafts round-tripping their image
reference correctly. Offline:

```bash
cd backend
python tests/test_image_store.py
```

## Deploying to production (real Firebase + Render)

See **`DEPLOY.md`** for the full walkthrough. Short version: the
Loyverse token and sync interval are no longer environment variables -
they're entered from the app's Settings page and stored in Firestore,
so changing them doesn't need a redeploy.

---

Implements the architecture we approved: UI -> business logic -> `PosProvider`
interface -> `LoyverseAdapter` (today) / a future own-POS adapter, without
anything above the interface knowing which one is plugged in.

```
backend/
  core/
    pos_provider.py   the port - abstract interface any POS backend implements
    stock_engine.py    business logic: deducts stock from sales via recipes
  adapters/
    loyverse_adapter.py   implements PosProvider using the Loyverse API
    _loyverse_client.py    the tested low-level Loyverse API wrapper (from pos-sync)
  storage/
    firestore_store.py   OUR OWN data: materials, recipes, expenses (not Loyverse's)
  api/
    main.py           FastAPI routes the frontend will call
```

Why `_loyverse_client.py` is prefixed with `_`: it's an implementation
detail. Only `loyverse_adapter.py` should ever import it - everything
else talks to `PosProvider`, so swapping in a different POS later means
writing one new adapter file, not touching business logic or the API.

## Setup

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env   # fill in your Loyverse token
```

Start the Firebase emulator (same as the earlier pos-sync prototype):
```bash
firebase emulators:start
```

Run the API:
```bash
uvicorn api.main:app --reload
```

Then visit http://127.0.0.1:8000/docs for the interactive API explorer -
useful for testing each endpoint before the frontend exists.

## What's implemented vs what's next

Implemented:
- Loyverse adapter (items, categories, receipts, stores) - **read-only**.
  The `PosProvider` interface has no write/create methods at all, by
  design: we never write back to Loyverse, so testing can't affect a
  real production account, and it keeps the door open to swapping in
  an own-built POS adapter later without anything above this layer
  changing.
- Our own category system lives entirely in Firestore
  (`create/rename/delete_category`, `set_item_category`) - Loyverse's
  own categories are only exposed read-only via
  `/api/{store_id}/loyverse-categories` for reference, never written to.
- Our own Firestore store for materials, recipes, and expenses
- Automatic stock deduction: `/api/{store_id}/sync` pulls new receipts
  and deducts recipe ingredients, skipping receipts already processed

Not yet built (next steps):
- Category edit/delete endpoints (only create exists so far)
- Scheduling `/sync` automatically (cron, or a webhook receiver) - right
  now it's a manual button on the Settings page
- Gross-profit and low-stock cards on the Dashboard (needs cross-referencing
  materials + recipes + receipts - straightforward to add, just not wired yet)

## Frontend

```
frontend/
  src/
    App.jsx              sidebar nav + routes for all 7 approved pages
    store/StoreContext.jsx   active-store selection (persisted in localStorage)
    api/client.js         fetch wrapper for every backend endpoint
    pages/                Dashboard, Items, Materials, Recipes, Receipts,
                           IncomeExpense, Settings - one file each
```

### Setup

This sandbox has no network access, so `npm install` couldn't be run here -
do it on your machine:

```bash
cd frontend
npm install
npm run dev
```

Make sure the backend (`uvicorn api.main:app --reload`) is running first,
since every page fetches from it on load. Visit http://localhost:5173.

If your backend runs somewhere other than `http://127.0.0.1:8000`, create
`frontend/.env` with:
```
VITE_API_URL=http://your-backend-host:port
```

