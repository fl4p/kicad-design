# Datasheet and sourcing preflight

Read this reference before any task that depends on component selection; circuitry, placement, or
layout around a critical component as defined in `SKILL.md`; a datasheet, reference design or
evaluation board; current lifecycle status; or stock, inventory, or distributor data. Skip it only
for purely graphical edits or file-format diagnoses that do not depend on device evidence.

## Contents

- [Run the preflight](#run-the-preflight)
- [Check owned inventory](#check-owned-inventory)
- [Use a source ladder](#use-a-source-ladder)
- [Handle inventory and distributor APIs safely](#handle-inventory-and-distributor-apis-safely)
- [Escalate through web defenses](#escalate-through-web-defenses)
- [Validate every downloaded PDF](#validate-every-downloaded-pdf)
- [Read the complete document](#read-the-complete-document)
- [Study reference implementations](#study-reference-implementations)
- [Fail closed](#fail-closed)

## Run the preflight

Establish what can be read before selecting or substituting a part or finalizing circuitry,
placement, or layout around a critical component:

1. Identify any user-declared inventory source and whether an already-authorized read-only
   interface is available when procurement is in scope.
2. Search the project's datasheet cache and other declared local sources.
3. Identify the exact vendor product page and document URLs for the datasheet and relevant
   reference-design or evaluation-board collateral; do not guess numeric asset IDs.
4. Check whether required inventory or distributor credentials are present without printing their
   values when procurement is in scope.
5. Confirm that a real browser can launch when a vendor may require one.
6. Confirm that PDF inspection tools are available.

Example checks:

```sh
rg -i --files datasheets 2>/dev/null | rg -i '<part-family>'

# Print matching variable NAMES only. Keep the match anchored so values cannot leak.
env | sed -nE 's/^([A-Za-z0-9_]*(INVENTREE|DIGIKEY|MOUSER|ELEMENT14|FARNELL)[A-Za-z0-9_]*)=.*/\1=<set>/Ip'

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

## Check owned inventory

Run this check only for component selection, substitution, or procurement validation. Do not add
inventory or account work to a graphical edit, fixed-part review, or local file-format diagnosis.
Before broad-market candidate search:

1. If the user has not declared an inventory source, ask whether one exists and which
   already-authorized read-only interface may be used. Do not infer that no declaration means an
   empty inventory.
2. Before treating a result as an empty inventory, verify that the query was unfiltered, fully
   paginated, and permission-scoped to the relevant inventory. Otherwise record
   `empty-source-untrusted` and ask whether another already-authorized source is available. For an
   exact-MPN or family lookup, query every identity-bearing search surface the source exposes, such
   as internal part records and manufacturer- or supplier-part mappings, and paginate each queried
   surface. Preserve the search scope and any unavailable or failed surface rather than calling a
   scoped miss exhaustive.
3. If a valid query reports that the inventory itself contains no parts, ask whether it is
   intentionally empty, stale, or uninitialized and whether to proceed without an inventory
   preference. Record `user-confirmed-empty` only for an intentionally empty source; record
   `empty-source-untrusted` for a stale or uninitialized source.
4. If a declared source is inaccessible, do not start authentication or request credentials. Ask
   whether another already-authorized source is available and record the resulting status.
5. If a non-empty inventory contains no candidate that satisfies all constraints, record
   `checked-no-qualified-match` and continue to market search without another inventory prompt.

Ask once per task and reuse the answer unless the user says the inventory changed. Re-query before
final selection or release when elapsed time could make recorded quantities unreliable. Distinguish
these evidence types rather than treating them as interchangeable fallback rungs:

- Evidence follows each record's provenance, not the name of the inventory application. Inspect
  stock-item notes, source links, import metadata, and quantity semantics before treating a number
  as on-hand. A native stock record with a known receiving and count process can establish
  **recorded** on-hand, reserved/allocated, and available-to-project quantities only as of the query
  timestamp. A record imported from order history remains historical-purchase evidence; record its
  ordered quantity as historical rather than inferring receipt, ownership, total holdings, or
  current remaining quantity. A verified receipt can establish quantity received, but only
  controlled stock transactions or a physical count can establish current remaining quantity.
  Require an exact manufacturer, MPN, and package mapping; an internal alias or family name is not
  enough.
- Query already-authorized order history when the user declares it as a parts source, but use it
  only for candidate discovery. It proves that an item was once ordered, not receipt, ownership,
  remaining quantity, condition, or current availability. If the history itself contains no items,
  ask whether another inventory source exists rather than inferring that current inventory is
  empty. Before preferring a historical candidate, use current inventory evidence or ask the user
  to confirm possession, condition, and available-to-project quantity.
- A distributor catalogue establishes distributor-reported availability and lifecycle state only
  as of the query timestamp; when lifecycle is load-bearing, verify it against current manufacturer
  product status or an explicit lifecycle, PDN, or EOL notice. Use other PCNs only for the claims
  they actually state. The vendor datasheet establishes technical suitability.

Prefer an owned part only when the inventory record establishes its exact manufacturer, MPN, and
package and the candidate satisfies every mandatory constraint with suitable condition and
sufficient available-to-project quantity. Record the lookup outcome and selection rationale when
the decision is made; inventory preference never compensates for missing engineering or lifecycle
evidence.

After checking exact replacements, sweep owned inventory by required function and classify each
relevant result as an exact replacement, a requirement-preserving value or package change, a
topology-changing alternative, or unsuitable. Treat power conversion, regulation, supervision,
series load switching, gate drive, and isolation as distinct circuit roles and requirement sets;
one part may satisfy several roles only when each is verified. Report topology-changing
candidates separately rather than hiding them under “no replacement” or presenting
non-interchangeable roles as drop-ins.

Inventory access failure is non-blocking after the required user clarification, but missing
load-bearing electrical, mechanical, safety, exact-MPN, lifecycle, or datasheet evidence retains
this skill's fail-closed behavior. Follow [`RELEASE.md`](RELEASE.md) for the inventory decision
record.

## Use a source ladder

This ladder obtains authoritative documents and catalogue facts; it does not replace the separate
inventory evidence types above. Escalate from the cheapest authoritative source:

1. **Project cache.** Prefer locally stored, provenance-recorded documents when their revisions
   cover the selected ordering code and board revision.
2. **Official vendor product page and document URLs.** Follow the vendor's datasheet, application
   note, reference-design, and evaluation-board links rather than inventing paths. Retrieve the
   user guide, schematic, BOM, layout, and design files when available.
3. **Distributor product API.** Use it to resolve exact MPNs, lifecycle, stock, and official
   datasheet links. Take electrical specifications from the PDF, not the catalogue row.
4. **Vendor asset host.** Try the exact media/CDN host serving the document; web defenses are often
   deployed per hostname.
5. **Real browser.** Use it when a direct HTTP client is rejected or receives a challenge.
6. **User-provided document.** Ask for the document when the available routes cannot establish the
   needed evidence.

Record the exact URL and route used. Label a mirror or footprint vendored from an existing board as
such; it proves what the current artefact contains, not that no newer vendor correction exists.

On macOS, distinguish a missing file from a TCC denial. A path can pass `test -d` while `ls` fails
with `Operation not permitted`, especially under `~/Documents`, `~/Desktop`, or `~/Downloads`.
Report that as a permissions result and ask the user to copy the file or grant access; do not
substitute a library item because its directory was unreadable.

## Handle inventory and distributor APIs safely

Use only already-authorized read-only access for inventory and order-history checks. Keep
credentials in environment variables, private configuration, or gitignored files. Never put keys,
tokens, authorization headers, raw account responses, sensitive stock locations, or private
instance URLs in a repository, prompt, log, design document, or published artefact.

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
merely to obtain a datasheet or when the user has not declared order history as a parts source;
those flows may rotate shared refresh tokens or require authorization unrelated to the design task.
When the user declares order history, query it only through already-authorized read-only access and
retain its result as historical-purchase evidence rather than current inventory.

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

## Study reference implementations

Apply the critical-component test in `SKILL.md`; the device need not already be labelled critical.
Then:

1. Inspect the current datasheet's typical-application circuit and the vendor product page's design
   resources for the exact device or applicable family.
2. Select and inspect the official application notes, reference designs, and evaluation-board user
   guides, schematics, BOMs, layouts, or design files that are relevant and comparable to the
   project's used functions and operating conditions. Verify the device variant, silicon revision,
   board revision, and operating conditions before treating them as comparable. Record what was
   examined and the basis for excluding superficially related collateral.
3. Compare the proposed design with those implementations across the applicable supply, support,
   control, configuration, protection, thermal, grounding, return-path, and layout-sensitive pins
   and paths. For a complex device, this does not require reviewing unused functional I/O or every
   reference platform in the product family.
4. Record intentional differences that affect a project requirement and why the project's
   operating conditions, interfaces, cost, assembly, compliance, or performance targets justify
   them. An unexplained omission is not an intentional simplification.
5. Check current errata and document revisions before copying a pattern. Evaluation boards may be
   feature-rich lab platforms, target different conditions, use obsolete parts, or omit
   production protection and compliance measures.

Treat reference implementations as strong design evidence, not normative requirements. The current
datasheet and errata establish device constraints; the project's requirements establish fitness.
If no relevant vendor implementation is available, record the product page, search terms, and
routes checked and mark the comparison `unverified`. Missing implementation collateral is blocking
only when it leaves a load-bearing design claim unsupported.

## Fail closed

When required evidence remains unavailable:

- Name the part, exact URL, and routes attempted.
- Ask the user to supply the document or required access.
- Do not quote specifications from memory.
- Do not substitute a part because its datasheet was easier to fetch.
- If an access-driven substitution is explicitly authorized, label it as an access decision in the
  design record so it can be revisited as an engineering choice.
