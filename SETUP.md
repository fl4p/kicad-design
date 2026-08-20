# Datasheet and sourcing preflight

Read this reference before any task that depends on a datasheet, current lifecycle status, stock,
or distributor data. Skip it for work that can be completed entirely from authoritative local
files, such as a graphical edit or file-format diagnosis.

## Contents

- [Run the preflight](#run-the-preflight)
- [Use a source ladder](#use-a-source-ladder)
- [Handle distributor APIs safely](#handle-distributor-apis-safely)
- [Escalate through web defenses](#escalate-through-web-defenses)
- [Validate every downloaded PDF](#validate-every-downloaded-pdf)
- [Read the complete document](#read-the-complete-document)
- [Fail closed](#fail-closed)

## Run the preflight

Establish what can be read before selecting or substituting a part:

1. Search the project's datasheet cache and other declared local sources.
2. Identify the exact vendor or distributor document URL; do not guess numeric asset IDs.
3. Check whether required distributor credentials are present without printing their values.
4. Confirm that a real browser can launch when a vendor may require one.
5. Confirm that PDF inspection tools are available.

Example checks:

```sh
rg -i --files datasheets 2>/dev/null | rg -i '<part-family>'

# Print matching variable NAMES only. Keep the match anchored so values cannot leak.
env | sed -nE 's/^([A-Za-z0-9_]*(DIGIKEY|MOUSER|ELEMENT14|FARNELL)[A-Za-z0-9_]*)=.*/\1=<set>/Ip'

python3 -c "from playwright.sync_api import sync_playwright
p=sync_playwright().start(); b=p.chromium.launch(channel='chrome'); print('chrome channel ok'); b.close(); p.stop()"

missing=
for tool in pdfinfo pdftotext pdftoppm; do
  command -v "$tool" >/dev/null 2>&1 || { echo "MISSING $tool"; missing=1; }
done
[ -z "$missing" ]
```

Do not reduce the browser check to importing its Python module; installed bindings do not prove
that a browser binary or the requested channel can launch. Do not check several commands with one
`command -v` invocation; shell behavior differs and can report success when only one resolves.

Treat reachability as a property of the exact URL, current IP, date, and client. A successful home
page request does not prove that the document host works, and a failed home page does not prove the
asset host is blocked.

## Use a source ladder

Escalate from the cheapest authoritative source:

1. **Project cache.** Prefer a locally stored, provenance-recorded PDF when its revision covers the
   selected ordering code.
2. **Official vendor document URL.** Follow the vendor's document link or a distributor-provided
   URL rather than inventing a path.
3. **Distributor product API.** Use it to resolve exact MPNs, lifecycle, stock, and official
   datasheet links. Take electrical specifications from the PDF, not the catalogue row.
4. **Vendor asset host.** Try the exact media/CDN host serving the document; web defenses are often
   deployed per hostname.
5. **Real browser.** Use it when a direct HTTP client is rejected or receives a challenge.
6. **User-provided document.** Ask for the PDF when the available routes cannot establish the
   needed evidence.

Record the exact URL and route used. Label a mirror or footprint vendored from an existing board as
such; it proves what the current artefact contains, not that no newer vendor correction exists.

On macOS, distinguish a missing file from a TCC denial. A path can pass `test -d` while `ls` fails
with `Operation not permitted`, especially under `~/Documents`, `~/Desktop`, or `~/Downloads`.
Report that as a permissions result and ask the user to copy the file or grant access; do not
substitute a library item because its directory was unreadable.

## Handle distributor APIs safely

Keep credentials in environment variables, private configuration, or gitignored files. Never put
keys in a repository, prompt, log, design document, or published artefact.

Validate each credential with one real product query and inspect the response body:

- Treat HTTP 200 with a populated error object as failure.
- Distinguish authentication, subscription, token-flow, and rate-limit errors before rotating a
  key.
- Back off on throttling; do not reinterpret a transient 403 or 429 as a dead credential.
- Assert that requested parametric filters appear in the API's applied-filter response and reduce
  the result set. Some APIs silently ignore malformed filters and return an unfiltered catalogue.
- Resolve stock and lifecycle from current catalogue data, then verify package and electrical
  performance against the vendor PDF.
- Treat purchase history as evidence that something was once ordered, not evidence of current
  inventory.

Use the simplest supported product-search flow. Do not access account-scoped order or list APIs
merely to obtain a datasheet; those flows may rotate shared refresh tokens or require user
authorization unrelated to the design task.

## Escalate through web defenses

Identify the response shape before choosing a workaround. Inspect status, headers, content type,
body length, document magic, and HTML title. Do not maintain a permanent table of which vendors are
"blocked"; those observations expire.

Expect false-success responses:

- a 2xx with an empty body;
- a 2xx HTML denial page;
- a JavaScript challenge page with a normal-looking title or content length;
- correct PDF headers followed by viewer HTML instead of the document body.

Try these rungs in order:

1. Direct request to the exact asset URL.
2. Direct request with coherent, realistic browser headers when the service requires them.
3. Real browser with a normal User-Agent.
4. Browser navigation on the document's origin followed by an in-page fetch or genuine download.
5. Dedicated persistent profile only when a service requires a one-time human challenge.

Never use the user's live browser profile. Use a dedicated profile, keep it outside the repository,
and work from a copy if a failed automated run could invalidate a human solve. Do not claim an
unattended route when a CAPTCHA still requires a person.

For programmatic browser download, prefer a genuine download event. A PDF tab can expose viewer
HTML to response-body APIs even while displaying the actual PDF:

```python
from pathlib import Path
from playwright.sync_api import sync_playwright

pdf_url = "https://vendor.example/path/PART.pdf"
output = Path("PART.pdf")

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        "/tmp/kicad-datasheet-profile",
        channel="chrome",
        headless=True,
        accept_downloads=True,
        user_agent="<current normal Chrome user agent>",
    )
    try:
        page = context.pages[0] if context.pages else context.new_page()
        with page.expect_download(timeout=90_000) as pending:
            page.evaluate("url => { window.location.href = url }", pdf_url)
        pending.value.save_as(output)
    finally:
        context.close()
```

Configure the dedicated profile to open PDFs externally when the browser otherwise uses its viewer.
If a cookie-authenticated service permits `context.request.get()`, prefer that simpler streaming
path. If the defense checks the browser's network fingerprint, use same-origin browser navigation
and in-page `fetch`; cross-origin page fetches are subject to CORS and may fail before returning a
status.

## Validate every downloaded PDF

Gate on content, not the URL, HTTP status, extension, or MIME type alone:

```sh
pdf=PART.pdf
mpn='<exact-MPN>'

[ -s "$pdf" ] || { echo 'empty file'; exit 1; }
[ "$(head -c4 "$pdf")" = '%PDF' ] || { echo 'missing PDF magic'; exit 1; }
[ "$(file -b --mime-type "$pdf")" = application/pdf ] || { echo 'wrong MIME'; exit 1; }

info=$(pdfinfo "$pdf") || { echo 'unreadable PDF'; exit 1; }
pages=$(printf '%s\n' "$info" | awk '/^Pages:/{print $2}')
[ -n "$pages" ] && [ "$pages" -gt 0 ] || { echo 'no readable pages'; exit 1; }

text=$(pdftotext -layout "$pdf" -) || { echo 'text extraction failed'; exit 1; }
printf '%s\n' "$text" | rg -qi --fixed-strings "$mpn" || {
  echo 'exact MPN absent; wrong document or image-only title'; exit 1;
}
```

Treat the exact-MPN check as evidence, not as a universal parser: a scanned sheet may require visual
verification. Reject stubs, unrelated family brochures, viewer shells, and challenge pages even
when a tool calls them PDFs. Do not reject a document solely because a parser prints a warning;
judge whether the required pages and content were actually recovered.

## Read the complete document

Use the whole PDF before concluding that a requirement or drawing is absent:

1. Extract all pages and search parameter names, abbreviations, and test-method vocabulary.
2. Inspect the electrical-characteristics table with layout preserved. Capture value, unit, test
   condition, and temperature together.
3. Inspect package, ordering, and land-pattern pages near the end of the document.
4. Render pages containing drawings. Text extraction can omit rasterized callouts entirely.
5. Search environmental and qualification rows such as endurance, damp heat, humidity, life, and
   temperature cycling before claiming that an aged value is unspecified.

For a drawing marked “not to scale,” use printed callouts as datasheet evidence. Use vector or
raster geometry only to associate a callout with a feature. If an unlabelled dimension must be
scaled from the image, record it as an approximate model and widen downstream tolerances.

When same-net lands are merged or notched, derive the union from the drawing and independently
cross-check pin pairing against the pin-function table. Stop when geometry and pin mapping disagree;
do not renumber until the independent sources agree.

An absence claim needs evidence. Cite the pages and vocabulary checked, or say “not found in this
search; full-sheet absence not established.”

## Fail closed

When required evidence remains unavailable:

- Name the part, exact URL, and routes attempted.
- Ask the user to supply the document or required access.
- Do not quote specifications from memory.
- Do not substitute a part because its datasheet was easier to fetch.
- If an access-driven substitution is explicitly authorized, label it as an access decision in the
  design record so it can be revisited as an engineering choice.
