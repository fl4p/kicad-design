# Setup: make sure you can actually read datasheets before you design

Companion to `SKILL.md`. **Run this preflight at the START of ANY task that will read a datasheet — schematic-only work included — not when
you hit a wall.** `SKILL.md` forbids quoting a spec from memory — this file is how you
make that possible, and how to validate a PDF once you have one.

`SKILL.md` § *Getting the PDF: vendor WAFs* explains **why** vendor sites block you and
what the Akamai signature looks like. This file is the **operational** half: what to
check up front, how to get and verify API keys, the rate-limit traps that masquerade as
auth failures, a programmatic browser fetch, and what to do when you still cannot read
the PDF.

Commands in §0, §3 and §3a are written for **macOS**. The reasoning is portable; the paths
and `nc` flags are not.

---

## 0. Preflight — five checks

Run them and report the results before starting design work. Do not skip one because
you "probably won't need it".

```sh
# 1. Local datasheet cache — cheapest, and often already has the part.
#    Use the project's datasheets/ dir if it has one; otherwise ask the user.
ls <datasheet-dir>/ | grep -i <part-family>

# 2. Which vendor sites are reachable from THIS machine, right now.
#    This PRINTS, it does not verdict -- and it probes the wrong host on purpose,
#    so read it as orientation only.  `http=403`, a 200 behind a consent wall and a
#    redirect all look like success here, and 3 explains that the host that matters
#    is the ASSET host (mds.analog.com), not www.  Proving you can fetch a datasheet
#    means fetching a known PDF URL and running it through 4's validation.
# Identify the WAF first -- the fix differs. `server: cloudflare` + `cf-ray` => Cloudflare,
# which usually yields to two curl headers (SKILL.md); `AkamaiGHost` => Akamai, which needs a
# browser with a non-HeadlessChrome UA.  curl -I https://<host>/ | grep -iE 'server|cf-ray'
for h in www.analog.com www.ti.com www.st.com www.onsemi.com www.vishay.com; do
  printf "%-22s " "$h"
  curl -sS -o /dev/null -w "http=%{http_code} t=%{time_total}s\n" --max-time 12 "https://$h/"
done

# 3. Distributor API keys present?  Names only — anchor the match so a value can
#    never print, even from a multi-line variable.
env | grep -iE '^[A-Za-z0-9_]*(digikey|mouser|element14|farnell)[A-Za-z0-9_]*=' | sed -E 's/=.*/=<set>/'
#    ...then check the PROJECT'S own gitignored config. Agents routinely check only
#    ~/.claude, ~/.config and the environment and miss keys sitting in the repo they
#    are working in — see SKILL.md § Getting the PDF.

# 4. Can Playwright actually drive real Chrome?  (importing the module proves nothing)
python3 -c "from playwright.sync_api import sync_playwright
p=sync_playwright().start(); b=p.chromium.launch(channel='chrome'); print('chrome channel ok'); b.close(); p.stop()"

# 5. PDF tooling (poppler; not present on stock macOS)
#    NOT `command -v a b c` -- that exits 0 if ANY ONE of them resolves.  Measured:
#    bash `command -v ls missing1 missing2` -> rc=0, zsh -> rc=1.  So the one-liner
#    passes under bash on a machine that has pdfinfo and neither pdftotext nor
#    pdftocairo -- exactly the tools 4 and *Reading the PDF* depend on -- and it
#    fails open only in scripts and CI, never when you test it interactively in zsh.
miss=
for c in pdfinfo pdftotext pdftocairo; do
  command -v "$c" >/dev/null 2>&1 || { echo "MISSING $c"; miss=1; }
done
[ -z "$miss" ] || { echo "brew install poppler"; exit 1; }
```

Check 4 matters more than it looks. `python3 -c "import playwright"` succeeds when the
browser drivers are missing and when `channel="chrome"` is unavailable — it prints OK
in exactly the situation §3a cannot run. That is the anti-monotone false PASS: a guard
that reports fine when it cannot evaluate its input.

### Reading check 2

`http=000` in **under ~100 ms** does not mean "no network". Before concluding anything:

```sh
nc -z -G 5 www.analog.com 443          # TCP connect  (-G is macOS; not GNU netcat)
curl -v --max-time 12 -o /dev/null https://www.analog.com/ 2>&1 | grep -i "TLS handshake\|ALPN"
```

If TCP connects, the TLS handshake completes, ALPN negotiates h2, **and the request
still yields nothing**, that is consistent with a **WAF fingerprint rejection** rather
than a network block — the server is refusing the client's fingerprint after the
handshake. A transparent proxy or captive portal can produce a similar signature, so
treat it as a hypothesis: try §3, then §3a, before reporting "unreachable".

Do not pass a bare `-A "Mozilla/5.0"`. A version-less UA is itself a cheap bot tell and
can *manufacture* the block you are testing for. Send a full realistic UA or none.

Vendor reachability is per-IP, per-date and per-WAF-ruleset. Treat any list of "blocked
vendors" as an example of the *pattern*, not a status table — always run check 2
yourself. Observed once, from one residential IP in 2026-08: Analog Devices and ST
showed the fingerprint-rejection signature; onsemi returned a plain 403 to curl but
loaded fine in a browser; TI, Farnell and Vishay were fine on plain curl.

That volatility is not hypothetical: **Mouser was recorded here as "fine on plain curl"
and is now the single hardest host measured** (2026-08-11) — bare `curl` is dropped, and a
UA-corrected `curl` gets a `200` carrying an "Access to this page has been denied" page.
It stayed 403 at every *cold* browser rung including a headed persistent profile, and its
`/datasheet/*.pdf` path serves the same deny page. Prefer the **Mouser Search API** below,
for which a key is already provisioned.

If pages are genuinely needed, Mouser has one working rung — a **one-time human warmup**,
described in `SKILL.md`. A dedicated profile lives at
`~/.cache/kicad-dl-profile-mouser`; a human solved its CAPTCHA on 2026-08-11 and it has
served real content headless ever since. Use it read-mostly:

```python
# copy first -- a bad run must not burn the human solve
PROFILE = os.path.expanduser("~/.cache/kicad-dl-profile-mouser")
ctx = p.chromium.launch_persistent_context(
    PROFILE, channel="chrome", headless=True,      # headless is FINE once warmed
    user_agent=UA,                                  # ...but the UA override is mandatory
    args=["--disable-blink-features=AutomationControlled"])
```

Dropping `user_agent=` re-triggers wall 1 and the connection is dropped
(`ERR_HTTP2_PROTOCOL_ERROR`) even with the warmed profile. If the CAPTCHA reappears, the
clearance has expired — it needs a human again, so do not build an unattended job that
assumes otherwise.

### macOS TCC can block LOCAL files, not just vendors

Not every access failure is a network one. On macOS, a sandboxed shell is denied
`~/Documents`, `~/Desktop` and `~/Downloads` by TCC unless the terminal has been granted
Full Disk Access — and the failure is easy to misread, because **`test -d` succeeds while
`ls` does not**:

```sh
$ [ -d ~/Documents/KiCad/9.0/footprints/open-pe.pretty ] && echo yes   # yes  (stat only)
$ ls  ~/Documents/KiCad/9.0/footprints/open-pe.pretty                  # Operation not permitted
$ ls  ~/Documents/                                                     # Operation not permitted
```

This bites here because **KiCad's user symbol and footprint libraries live under
`~/Documents/KiCad/<version>/`** by default. A global `fp-lib-table` entry can point at a
perfectly present library that this environment cannot read, so the library reads as
missing and the natural next move — substituting a different footprint — is the
tooling-driven design change `SKILL.md` warns about.

If a path stats but will not read, say so as a permissions result rather than a missing
file, and take one of: ask the user to run the copy themselves (in Claude Code, `! cmd`
puts the output in the conversation), have them grant Full Disk Access, or source the data
from somewhere readable and **label where it came from**. Vendoring footprints out of the
`.kicad_pcb` is a legitimate fallback — it provably matches what will be fabricated — but
it cannot capture a correction made in the original library since those instances were
placed, and the write-up has to say that.

---

## 1. Ask the user for what is missing

If a needed key is absent, **ask once, up front, in a single message**, naming what you
need and why. Do not start work intending to improvise later.

> I need distributor API access to verify part specs against datasheets. Missing:
> **element14** (free — register at <https://partner.element14.com/member/register>,
> then apply for a Product Search API key) and **Mouser Search API**
> (<https://www.mouser.com/api-hub/>). DigiKey needs a v4 app
> (<https://developer.digikey.com/>) giving a CLIENT_ID + CLIENT_SECRET.
> Without at least one I cannot confirm MPNs, stock or datasheet URLs, and I will not
> guess them.

Rough order of usefulness **for datasheets specifically**:

| source | auth | notes |
|---|---|---|
| **element14 / Farnell / Newark** | plain query param, no OAuth | easiest; hosts many PDFs on its own CDN |
| **Mouser** | API key in query param | `Description` field carries dense parametrics |
| **DigiKey** | two-legged OAuth, no browser | best parametric data; often links back to the vendor site |

Coverage is vendor-dependent, and a distributor API does **not** automatically route
around a blocked vendor. Observed: Vishay parts came back with a `farnell.com`-hosted
PDF, while Analog Devices parts returned a URL pointing straight back at `analog.com` —
the very host that was blocked. Check where the URL actually points before assuming the
key solved your problem.

Never write a key into a repo file, commit message, design document or published
artifact. Keys belong in the user's private config or a gitignored file.

---

## 2. Verify each key with one real call

A stored key that fails is worse than no key: it produces a confident "no API
configured" later, which is how parts get substituted. One call each, and read the
**body**, not just the status.

**element14** — `GET https://api.element14.com/catalog/products`
with `term=manuPartNum:<MPN>` (or `any:<free text>`), `storeInfo.id=<store>`,
`callInfo.apiKey=<KEY>`, `callInfo.responseDataFormat=json`,
`resultsSettings.responseGroup=medium`.
Response root is `manufacturerPartNumberSearchReturn`, or `keywordSearchReturn` for
`any:` terms. Store ids look like `uk.farnell.com`, `de.farnell.com`,
`www.newark.com`, `au.element14.com`.

- **`medium` is the level that populates `datasheets[]`.** Measured: `small` returns
  zero datasheets, `medium` and `large` both return them. `large` adds images and
  related products you do not need — no reason to pay for that payload.
- `resultsSettings.offset` / `resultsSettings.numberOfResults` are **optional**;
  omitting them returned the same results. Send them when you want to page.

**Mouser** — `POST https://api.mouser.com/api/v1/search/keyword?apiKey=<KEY>` with
`{"SearchByKeywordRequest":{"keyword":"<MPN>","records":5,"startingRecord":0}}`.

**DigiKey** — `POST https://api.digikey.com/v1/oauth2/token` with
`grant_type=client_credentials`, then
`GET /products/v4/search/<MPN>/productdetails` with `Authorization: Bearer …` and
`X-DIGIKEY-Client-Id`. No callback port, no token store, ~40 lines of `urllib`.

**That two-legged shortcut covers `products/*`, not the account-scoped APIs.** Order
history at `GET /orderstatus/v4/orders` needs a **three-legged** token, so there is a
callback and there is a token store. (`/mylists/v1/lists` is presumably the same, but
two-legged returns a 401 with an *empty body* there, which cannot distinguish "needs
three-legged" from "not subscribed" — inferred, not measured.) Two traps:

- **Refreshing ROTATES the refresh token and invalidates the old one** — verified by
  replaying a rotated token: `401 "Invalid RefreshToken"`. If another tool on the
  machine owns that token store, a refresh you did in passing silently breaks it.
  Write the new token back to the store you read it from, in the same format, or do
  not refresh at all.
- **The two token flows have very different lifetimes and neither is labelled.**
  Three-legged access token `expires_in: 1798` (~30 min); two-legged
  `client_credentials` `expires_in: 599` (10 min), measured on three apps; refresh
  token `7775999` (90 d). A long session hits `401` mid-batch — refresh and retry
  rather than reading it as a bad key.

### Failures that do not look like failures

- **Mouser returns HTTP 200 with a populated `Errors[]`.** `Invalid unique identifier`
  on the API Key field is an auth failure wearing a success status. Check `Errors[]`,
  not the status code. A dead key was handed over during this work and only the body
  revealed it — rejected by the Search *and* Order APIs, so not the usual
  Search-vs-Order mix-up either.
- **element14 HTTP 403 can mean RATE LIMITED, not a bad key.** Observed on one key:
  a burst of 4–5 calls returned 403, and the *identical* query then succeeded with a
  ~3 s gap. Whether the threshold is per-key, per-IP or per-window is not documented;
  start around 1 call/s and back off on 403 rather than concluding the key is bad.
- **DigiKey rate limits per key** — watch the `X-RateLimit-Remaining` header and back
  off; several registered apps can be rotated.
- **DigiKey `401 "Invalid Client-Id / You are not subscribed to this API"` is a
  SUBSCRIPTION failure wearing an auth error.** The credentials are fine; the
  registered app simply is not subscribed to that API *product* in the developer
  portal. Measured: identical 401 from all three registered apps on the whole v3
  `OrderDetails` product (`/History` *and* `/Status/{id}`), while the same tokens
  returned 200 on `products/v4/search`, and the one app holding a three-legged token
  returned 200 on `orderstatus/v4/orders`. Rotating keys will never fix it — subscribe
  the app, or find the API version that is subscribed. Same shape as the Mouser bullet
  above: read the *body*, and let a success elsewhere on the same token tell you the
  key is not the problem. **The wording varies, which is what makes it misreadable:**
  the same underlying failure also appears as `401 "Invalid clientId.
  X-DIGIKEY-Client-Id invalid for requested resource"` (an app not subscribed to
  `orderstatus/v4`) and as `400 "Invalid Account ID / Account ID must not be 0"` (an
  app that *is* subscribed, handed a two-legged token). Three wordings, one question:
  is this key, token type, or subscription?
- **`orderstatus/v4/orders` silently truncates when you omit `startDate`/`endDate`.**
  No dates returned **1** order; `startDate=2020-01-01&endDate=<today>` returned **14**.
  Not a page-size default either — `limit=100` and `pageSize=100` still return 1. The
  window is undocumented and one account's data could not bound it tighter than "under
  a year", so **do not write a number here**; explicit windows of 30/45/60/90/120/180 d
  all returned 1 and 365 d returned 4. Nothing in the response says it truncated: the
  anti-monotone shape, where a date-less query looks like a complete answer. Always send
  an explicit range and state the range you used.
- **Distributor parametric rows can be wrong.** Use the API to resolve MPNs, stock and
  datasheet URLs; take *specifications* from the PDF.
- **A malformed DigiKey parametric filter is IGNORED, not rejected — HTTP 200 with the
  UNFILTERED list.** `FilterOptionsRequest.ParameterFilterRequests` was silently
  dropped in every form tried (inside/outside `FilterOptionsRequest`, `Id` vs
  `ValueId`, string vs int): `ProductsCount` stayed at the unfiltered 875126 and
  `AppliedParametricFiltersDto` stayed `[]`, while `ManufacturerFilter` and
  `MinimumQuantityAvailable` in the *same* object worked. So "here are the 100 nF C0G
  0603 parts" can silently be the top of the whole catalogue — X7R 0402s. **Assert
  `AppliedParametricFiltersDto` is non-empty and `ProductsCount` actually dropped**, or
  your parametric query did nothing. Without that assertion the query prescribed in
  SKILL.md § BOM is a mute button.

---

## 3. Before reaching for a browser: try the vendor's asset host

A WAF is deployed per hostname, and a vendor's **document/asset host is frequently not
behind the one guarding its main site**. Check that before spending effort on §3a.

Measured: `www.analog.com` completed the TLS handshake and then dropped the request for
every curl invocation tried, while **`mds.analog.com`** — which serves ADI's Package
Index outline drawings — returned a 39 KB PDF on a plain `curl` with no UA, no cookies
and no browser. Same vendor, same session, same minute.

So when the main site blocks you, look for the host that actually serves the asset:
follow the link target from a distributor API's `datasheets[]` or from a search result
rather than assuming it lives on `www.`. Media/CDN subdomains (`mds.`, `media.`,
`docs.`, `www*.`) are worth one `curl -I` each — it costs seconds and can save the whole
browser dance.

This also explains a confusing symptom: **a vendor can look "blocked" and "reachable" at
the same time**, depending which hostname each attempt used. Record the exact URL with
any reachability claim.

## 3a. Programmatic browser fetch

`SKILL.md` gives a shell recipe (`open -na 'Google Chrome'` with a throwaway profile
that forces PDFs to download). Use that when you just need the file. Use the snippet
below when a script has to fetch and check many PDFs unattended.

Four details matter; skipping any one fails:

1. **`channel="chrome"`** — real Chrome, not bundled Chromium.
2. **A User-Agent without the `HeadlessChrome` token.** Either run headed, or stay
   headless and pass `user_agent=` — measured equivalent against ADI and ST, see the
   ladder in `SKILL.md`. `headless=True` with a corrected UA is the cheapest rung that
   works and the only one that survives SSH and CI; the persistent profile and
   `headless=False` below are the belt-and-braces version, not a requirement.
3. **Navigate to a page on the *same origin* as the PDF first.** This seeds clearance
   cookies, and it is a hard requirement for step 4: a cross-origin in-page `fetch`
   **throws** rather than returning a status, so the error path below never runs.
4. **Fetch from inside the page — when the wall is fingerprint-based.** Playwright's
   `context.request.get()` is a separate HTTP client that shares cookies but **not** the
   browser's TLS/HTTP2 fingerprint, so against ADI/ST it still gets 403 and only an
   in-page `fetch()` via `page.evaluate()` gets through.

   **This is WAF-specific, not a general rule.** Where the clearance is carried by a
   *cookie* rather than a fingerprint, `context.request.get()` works fine — measured on
   Mouser with the warmed profile: `200`, 1308247 B, a valid 16-page datasheet, and
   byte-identical to what a real download produced. So try `request.get()` first (it is
   far simpler and streams instead of base64-ing through CDP), and fall back to the
   in-page `fetch()` only if it 403s.

   For a PDF that must land on disk, prefer a genuine download over any body read —
   `page.goto()` on a PDF returns correct headers while the *body* is 536 bytes of
   viewer HTML:

   ```python
   # patch the profile's Default/Preferences BEFORE launching:
   #   plugins.always_open_pdf_externally = True
   #   download.default_directory = <dir>, download.prompt_for_download = False
   ctx = p.chromium.launch_persistent_context(..., accept_downloads=True)
   with pg.expect_download(timeout=90000) as dl:
       pg.evaluate("u => { window.location.href = u }", PDF_URL)
   dl.value.save_as(out)          # then check %PDF magic and pdfinfo Pages
   ```

```python
from playwright.sync_api import sync_playwright
import base64, pathlib

PROFILE_DIR = "/tmp/kicad-dl-profile"   # dedicated; never the user's live profile
VENDOR_HOME = "https://www.example.com/en/index.html"   # SAME ORIGIN as PDF_URL
PDF_URL     = "https://www.example.com/media/.../PART.pdf"
OUT         = "part.pdf"

JS = """async (u) => {
    const r = await fetch(u, {credentials: 'include'});
    if (!r.ok) return {status: r.status, b64: null};
    const v = new Uint8Array(await r.arrayBuffer());
    let s = ''; for (let i = 0; i < v.length; i++) s += String.fromCharCode(v[i]);
    return {status: r.status, b64: btoa(s)};
}"""

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE_DIR, channel="chrome", headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 900})
    try:
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.goto(VENDOR_HOME, wait_until="domcontentloaded", timeout=60000)
        res = pg.evaluate(JS, PDF_URL)          # raises if cross-origin
        if res["status"] != 200 or not res["b64"]:   # not assert: -O deletes those
            raise RuntimeError(f"blocked: {res['status']}")
        data = base64.b64decode(res["b64"])
        if b"%PDF" not in data[:1024]:
            raise RuntimeError("not a PDF")
        pathlib.Path(OUT).write_bytes(data)
    finally:
        ctx.close()                              # else a headed window and a locked profile leak
```

Verified end-to-end against a WAF-blocked vendor: the fetched file was byte-identical
(same MD5, 2042809 bytes, 23 pages) to a copy downloaded by hand in a normal browser.
The headless rung-3 variant returns that same 2042809-byte file.
The base64 round-trip through CDP is slow on large files — a 20 MB datasheet can look
hung for tens of seconds.

An **MCP** Playwright tool is usually not sufficient for these sites: it launches an
ephemeral context and still gets 403 against Akamai. It does work for plainer walls.

---

## 4. Validate every PDF before you trust it

Distributor and mirror URLs return files that are PDFs by MIME type and useless by
content. Two real cases in one afternoon: hand-guessed `farnell.com/datasheets/<id>.pdf`
ids returned **1-page stubs**, and another mirror returned a **0-page** file. Both were
reported by a prior agent as "PDF document, version 1.7" — technically true, entirely
worthless.

**A WAF challenge can arrive as a 2xx.** AWS WAF Bot Control (CloudFront; Infineon) answers
a blocked request with `202 Accepted` and a **zero-byte body**, so `curl -f`, `resp.ok` and
`if code >= 400: fail` all call it a success. Never gate on the status code alone — require
a nonzero length and the `%PDF` magic, and treat an `x-amzn-waf-action` response header as a
block regardless of status:

```sh
code=$(curl -sL -D h.txt -o x.pdf -w '%{http_code}' "$URL")
grep -qi '^x-amzn-waf-action:' h.txt && { echo "WAF challenge (http=$code) -- NOT a download"; exit 1; }
[ -s x.pdf ] || { echo "empty body, http=$code -- NOT a download"; exit 1; }
[ "$(head -c4 x.pdf)" = '%PDF' ] || { echo "no %PDF magic, http=$code"; exit 1; }
```

```sh
[ "$(file -b --mime-type x.pdf)" = application/pdf ] || { echo "not a PDF"; exit 1; }

# Pages.  An UNREADABLE pdf (corrupt, encrypted, absent) makes pdfinfo fail and the
# pipeline still exit 0 with EMPTY output -- and `[ "$pages" -eq 0 ]` on an empty
# string does not evaluate true, so "could not read it" silently becomes "fine".
# Capture the status, and treat empty as FAIL.
info=$(pdfinfo x.pdf) || { echo "pdfinfo failed -- unreadable, NOT clean"; exit 1; }
pages=$(printf '%s\n' "$info" | awk '/^Pages:/{print $2}')
[ -n "$pages" ] || { echo "no page count -- unverified, not a pass"; exit 1; }
[ "$pages" -ge 2 ] || { echo "$pages page(s) -- a stub, not an IC datasheet"; exit 1; }

# The MPN actually appears.  NOT `... | grep -ci "<MPN>" || true`: the `|| true`
# disarms the check permanently, `grep -c` prints a reassuring "0", and the
# pipeline status is grep's, so a pdftotext that failed outright is invisible.
txt=$(pdftotext -layout x.pdf -) || { echo "pdftotext failed -- unverified"; exit 1; }
n=$(printf '%s\n' "$txt" | grep -ci "<MPN>")
[ "$n" -ge 1 ] || { echo "MPN absent -- wrong part, or a stub PDF"; exit 1; }
```

**Judge the file by those three checks, never by the tools' stderr.** Poppler prints
`Syntax Error (…): Can't revert non decrypt streams` on some vendors' encrypted-stream
PDFs while parsing them perfectly — one such file reported its 23 pages correctly and
was byte-identical (same MD5) to a copy downloaded by hand. Gating on "pdfinfo printed
an error" rejects a good datasheet, which is the stub problem inverted: same mistake,
judging the file by the wrong signal.

**Never guess a datasheet id** — take it from the API's `datasheets[]` field.

**These three checks prove the file has *a* text layer. They say nothing about whether the
number you need is in it.** A real case: `ECS-2025-2033.pdf` passes all three cleanly —
`application/pdf`, 2 pages, the MPN four times, and `pdftotext -layout` returns every
parameter table — while **both** figures, the package drawing and the Suggested Land Pattern,
are 150-dpi JPEG rasters with no text at all. Every dimension in the part is unreachable, and
the preflight is green. That green was read as "the datasheet does not specify a land pattern"
and a wrong footprint shipped on the strength of it.

So when the parameter you want is a **drawing callout** — pad geometry, package dimensions,
land patterns — the preflight is not finished until you have confirmed it is *readable*:

```sh
# Is the figure text, or a picture of text?
pdfimages -list x.pdf | awk 'NR>2 && $3=="image"'   # any rows -> there are rasters
pdftotext -layout x.pdf - | grep -cE '<a dimension you can see in the figure>'
# 0 hits while the figure plainly shows it => the drawing is a raster. RENDER it:
pdftoppm -r 400 -png -f <page> -l <page> x.pdf out   # then look at out-<page>.png
```

`pdftocairo -svg` will not rescue this either — there are no vectors to extract. See
`SKILL.md` § *Reading the PDF*, fourth bullet, for what to do with the rendered image and for
the line between a **printed callout** (a datasheet number, usable) and a length **scaled off
the picture** (a model, and it must be labelled as one).

For the rest of what to do once the PDF is open — where package drawings hide, how to read a
not-to-scale land drawing — see `SKILL.md` § *Reading the PDF*.

---

## 5. Fail closed

If, after all of the above, you cannot read a datasheet you need:

- **Stop and say so.** Name the part, the URL, and what you tried.
- **Ask the user to supply the PDF.** They can usually fetch it in one click.
- **Do not substitute a part** because its datasheet was unreachable. If a substitution
  is genuinely forced, label it in the design document as an access decision rather
  than an engineering one, so it can be revisited. `SKILL.md` § *Getting the PDF*
  records what that cost on a real board.
- **Do not quote specs from memory** to keep moving.
