# Setup: make sure you can actually read datasheets before you design

Companion to `SKILL.md`. **Run this preflight at the START of a board task, not when
you hit a wall.** `SKILL.md` forbids quoting a spec from memory — this file is how you
make that possible, and how to validate a PDF once you have one.

`SKILL.md` § *Getting the PDF: vendor WAFs* explains **why** vendor sites block you and
what the Akamai signature looks like. This file is the **operational** half: what to
check up front, how to get and verify API keys, the rate-limit traps that masquerade as
auth failures, a programmatic browser fetch, and what to do when you still cannot read
the PDF.

Commands in §0 and §3 are written for **macOS**. The reasoning is portable; the paths
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
command -v pdfinfo pdftotext pdftocairo || echo "brew install poppler"
```

Check 4 matters more than it looks. `python3 -c "import playwright"` succeeds when the
browser drivers are missing and when `channel="chrome"` is unavailable — it prints OK
in exactly the situation §3 cannot run. That is the anti-monotone false PASS: a guard
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
treat it as a hypothesis: try §3 before reporting "unreachable".

Do not pass a bare `-A "Mozilla/5.0"`. A version-less UA is itself a cheap bot tell and
can *manufacture* the block you are testing for. Send a full realistic UA or none.

Vendor reachability is per-IP, per-date and per-WAF-ruleset. Treat any list of "blocked
vendors" as an example of the *pattern*, not a status table — always run check 2
yourself. Observed once, from one residential IP in 2026-08: Analog Devices and ST
showed the fingerprint-rejection signature; onsemi returned a plain 403 to curl but
loaded fine in a browser; TI, Mouser, Farnell and Vishay were fine on plain curl.

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
- **Distributor parametric rows can be wrong.** Use the API to resolve MPNs, stock and
  datasheet URLs; take *specifications* from the PDF.

---

## 3. Programmatic browser fetch

`SKILL.md` gives a shell recipe (`open -na 'Google Chrome'` with a throwaway profile
that forces PDFs to download). Use that when you just need the file. Use the snippet
below when a script has to fetch and check many PDFs unattended.

Note the apparent conflict: `SKILL.md` says *"a headless browser does not help"*. That
is about **default headless Chromium**, whose UA advertises `HeadlessChrome`. **Headed
real Chrome with a persistent profile** is a different fingerprint and does work.

Four details matter; skipping any one fails:

1. **`channel="chrome"`** — real Chrome, not bundled Chromium.
2. **`launch_persistent_context(...)` with `headless=False`.**
3. **Navigate to a page on the *same origin* as the PDF first.** This seeds clearance
   cookies, and it is a hard requirement for step 4: a cross-origin in-page `fetch`
   **throws** rather than returning a status, so the error path below never runs.
4. **Fetch from inside the page.** Playwright's `context.request.get()` is a separate
   HTTP client that shares cookies but **not** the browser's TLS/HTTP2 fingerprint, and
   still gets 403. Only an in-page `fetch()` via `page.evaluate()` gets through.

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
        assert res["status"] == 200 and res["b64"], f"blocked: {res['status']}"
        data = base64.b64decode(res["b64"])
        assert b"%PDF" in data[:1024], "not a PDF"
        pathlib.Path(OUT).write_bytes(data)
    finally:
        ctx.close()                              # else a headed window and a locked profile leak
```

Verified end-to-end against a WAF-blocked vendor: the fetched file was byte-identical
(same MD5, 2042809 bytes, 23 pages) to a copy downloaded by hand in a normal browser.
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

```sh
file -b --mime-type x.pdf                              # application/pdf
pdfinfo x.pdf | awk '/Pages/{print $2}'                # 0 pages is always a failure;
                                                       #   1 page is a failure for an IC
pdftotext -layout x.pdf - | grep -ci "<MPN>" || true   # the part number actually appears
                                                       #   (grep exits 1 on no match)
```

**Never guess a datasheet id** — take it from the API's `datasheets[]` field.

For what to do once the PDF is open — where package drawings hide, how to read a
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
