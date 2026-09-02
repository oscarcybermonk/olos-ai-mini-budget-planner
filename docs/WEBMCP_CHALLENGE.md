# WebMCP Challenge implementation

## Objective and provenance

Olos-AI Mini Budget Planner demonstrates one deterministic web application with two first-class interfaces: the existing human UI and a structured agent tool surface. The normal product was already functional before challenge work began.

- Pre-WebMCP baseline: `3b937556a1c26116ec59487f0e30d2a86e42a428`
- Development branch: `hackathon/webmcp`
- Implementation started: 1 September 2026
- The WebMCP feature work remains confined to this branch; shared publication-safety fixes may also be applied independently to `main`.

## Current WebMCP assumptions

Implementation follows the current imperative API documented by Chrome and the WebMCP specification:

- Tools register with `document.modelContext.registerTool({ name, description, inputSchema, annotations, execute }, { signal })`.
- Registration is asynchronous. An `AbortController` supports unregister/re-register lifecycle; page teardown otherwise ends the context.
- `execute(input, { signal })` returns a serializable value and passes cancellation to `fetch`.
- Current annotations are `readOnlyHint` and `untrustedContentHint`. There is currently no `destructiveHint`, `outputSchema`, or MCP-style `structuredContent` field in the browser API.
- The delete tool therefore uses explicit destructive wording plus an exact `confirmation: "DELETE"` schema requirement.
- Tool exposure remains same-origin; cross-origin tool exposure is neither needed nor enabled.

Primary references:

- <https://developer.chrome.com/docs/ai/webmcp/imperative-api>
- <https://developer.chrome.com/docs/ai/webmcp/secure-tools>
- <https://webmachinelearning.github.io/webmcp/>
- <https://openai.com/webmcp-challenge/>
- <https://webmcp.devpost.com/rules>

## Architecture

```text
Human HTML/CSS/JavaScript UI ─┐
                              ├─> existing FastAPI endpoints
WebMCP imperative tool layer ─┘             │
                                            v
                              deterministic domain logic
                                            │
                                            v
                                    current SQLite data
```

The adapter lives in `frontend/assets/webmcp.js`. It does not calculate summaries, projections, liability changes, or loan interest. It converts an exact decimal amount string into transport cents and calls the same API routes as the UI. Successful tool mutations dispatch one same-page event; the existing `refresh()` then redraws the human view.

## Implemented tools

Read-only:

- `get_budget_summary`
- `list_upcoming_items`
- `list_recent_activity`
- `list_credit_and_loans`
- `get_calendar_month`

Mutating:

- `add_transaction`
- `create_recurring_item`
- `record_recurring_item`
- `record_credit_payment`
- `update_transaction`
- `delete_transaction`

Schemas reject additional properties, constrain IDs/enums/ranges, use ISO dates, and represent money as exact AUD decimal strings. Returned objects use `{ ok, tool, data }`. HTTP validation errors become concise failed tool executions.

## Hosted demo design

`OLOS_HACKATHON_DEMO_MODE=true` activates browser-session isolation:

1. The server creates a random UUID cookie with `HttpOnly`, `SameSite=Lax`, and `Secure` under HTTPS.
2. The request context maps that opaque ID to one SQLite file under `OLOS_HACKATHON_DEMO_DATA_DIR`.
3. A new file receives schema plus small synthetic transactions, recurring items, one card, and one fixed loan.
4. All API calls, UI actions, and WebMCP tools in that browser share the same session database.
5. Reset removes that session's changes and reseeds its synthetic story.
6. Files older than 24 hours are removed from the demo-only directory; Render's ephemeral filesystem also clears on restart or spin-down.

Normal local startup does not enable this middleware and uses the ignored, project-specific `.hackathon-runtime` directory. No external database, backup, export, or personal amount is copied into the demo seed.

## Safety decisions

- Read freely; mutate transparently; delete deliberately.
- Amounts are never inferred. Write tools require an explicit exact amount.
- Credit-funded purchases and repayments retain the original no-double-counting rules.
- Recording a linked loan occurrence creates one cashflow transaction and one loan-balance event.
- User-entered text is declared untrusted tool content.
- The health endpoint reveals a storage mode label, never an absolute filesystem path.
- No CORS, banking, transfer, LLM, analytics, authentication, cloud personal storage, or financial advice was added.

## Testing

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
npm.cmd run test:frontend
npm.cmd run build
npm.cmd run lint
```

The Node WebMCP tests execute actual registered tool callbacks against a controlled fetch host, verify schemas and annotations, inspect exact request bodies, and observe the UI refresh event. Backend tests exercise the same endpoints, accounting behaviour, filters, reset/reseed, and two separate demo cookies/databases.

For a real browser test, use ChatGPT's compatible in-app browser or Chrome 149+ with `chrome://flags/#enable-webmcp-testing`, open the served HTTPS/local page, inspect `document.modelContext.getTools()`, and call tools with the DevTools WebMCP helper. Do not claim this step passed unless the compatible host actually exposed the tools.

### Browser acceptance completed on 1 September 2026

Using the actual ChatGPT in-app browser against a localhost server with a fresh isolated demo directory:

- all 11 page-defined tools were discovered with their schemas and annotations;
- `get_budget_summary` and `list_upcoming_items` returned structured data;
- `add_transaction` created an exact-cent record and Recent Activity refreshed without reloading;
- `update_transaction` corrected that record and bounded recent-activity filtering worked;
- `record_recurring_item` recorded the linked demo loan payment once and reduced the estimated balance once;
- `record_credit_payment` reduced owed/raised available credit once and remained separate from ordinary spending;
- manual Quick Add still saved through the human UI;
- dark mode and a 390 × 844 responsive viewport showed no visual artefacts or horizontal overflow;
- the Reset dialog opened with its submit control disabled before the exact typed confirmation.

The destructive WebMCP delete call was not executed during live browser testing. Its schema and exact `DELETE` gate were discovered in-browser; callback and backend deletion/account-reversal behaviour remain covered by automated tests.

## Known limitations

- Browser support is experimental and version-sensitive.
- Render's free tier can cold-start after inactivity and uses ephemeral storage by design.
- Demo state is isolated per cookie, not an authenticated account; it must never hold real data.
- A compatible agent/browser may add its own confirmation UI. The application still enforces exact delete confirmation.
- The Budget Planner records state only. It does not execute bank transactions or provide advice.
