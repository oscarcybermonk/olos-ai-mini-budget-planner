# Devpost submission draft

## Project title

Olos-AI Mini Budget Planner

## Short description

A deliberately simple, deterministic personal budget tracker where people use a normal visual interface and compatible agents use structured WebMCP tools against the same local-first application logic.

## What it does

The planner records income, expenses, bills, savings, recurring commitments, revolving credit, Pay Later balances, and estimated fixed loans. It immediately shows actual and projected monthly cashflow without subscriptions, bank linking, advertising, telemetry, or an account system.

On the challenge branch it also registers eleven WebMCP tools. An agent can read a monthly summary, inspect upcoming commitments or recent activity, record a confirmed transaction, mark a recurring payment, record a credit repayment, or correct/delete an ordinary record. The normal UI visibly updates after tool mutations.

## Why this fits WebMCP

The useful idea is not another embedded “AI budget assistant.” The web application itself exposes its deterministic meaning and capabilities. WebMCP gives an agent typed dates, enums, IDs, limits, exact money fields, and application-authored descriptions instead of forcing it to infer intent from pixels or DOM layout.

## Better user experience

The person keeps the fast one-screen experience. When an agent is useful, it can perform a precise task such as recording a spoken expense or comparing upcoming bills before payday. There is no chatbot panel to learn and no duplicated financial engine. The resulting record appears in the same Recent Activity and summary the person already trusts.

## What humans and agents can do together

- A person says the amount, purpose, date, and payment method; the agent submits a validated draft as one transaction.
- The agent reads upcoming commitments; the person decides what action to take.
- The agent records an identified recurring loan repayment; the app updates cashflow and the linked liability exactly once.
- The agent finds a transaction by stable ID and corrects it while the human immediately sees the changed screen.
- The human can continue editing, deleting, backing up, or resetting through the ordinary touch-friendly UI.

## How WebMCP was implemented

`frontend/assets/webmcp.js` uses the imperative `document.modelContext.registerTool(...)` API. Each tool has a strict JSON input schema, concise description, supported annotations, an abort-aware lifecycle, and a serializable result. The callbacks call the existing same-origin FastAPI endpoints. Python remains the sole authority for validation, summaries, recurrence, credit balances, fixed-loan interest, and no-double-counting behaviour.

## WebMCP leverage

Eleven coherent tools cover meaningful reads and writes rather than one ceremonial integration. Exact schemas distinguish cash from available credit and fixed-loan liabilities; resolve recurrence IDs; enforce bounded reads; and require deliberate delete confirmation.

## Execution

The submission extends a complete pre-existing responsive PWA. Existing Quick Add, recurring management, calendar, backup/export, credit and loan workflows remain intact. Automated tests cover both the old application and actual tool callback execution.

## Potential impact

Simple personal budgeting has been burdened by subscriptions, logins, cloud dependency, bank access, advertising, and bloated dashboards. This project shows that a small local-first tool can stay private and understandable while still becoming agent-native at the browser boundary.

## Creativity and ambition

One deterministic web app has two first-class interfaces. The application does not embed AI, send private data to an LLM, or surrender its rules to prompting. It publishes just enough structured capability for a compatible agent to collaborate safely.

## Built with

Python, FastAPI, Pydantic, SQLite, HTML, CSS, JavaScript, PWA APIs, WebMCP imperative API, Node test runner, and Render Blueprint configuration.

## Testing instructions

1. Open the live URL in ChatGPT's compatible in-app browser or a WebMCP-enabled Chrome 149+ browser.
2. Confirm the page reports “WebMCP ready · 11 structured tools” under Data and backup tools.
3. Call `get_budget_summary` for the displayed month.
4. Call `add_transaction` with explicit synthetic data and observe Recent Activity refresh.
5. Call `list_upcoming_items`, then record one returned occurrence with `record_recurring_item`.
6. Use only synthetic demo data. Reset restores the isolated demo story.

## Links

- Public repository: https://github.com/oscarcybermonk/olos-ai-mini-budget-planner
- Live demo URL: **PLACEHOLDER — add after Render authorization/deployment**
- Public YouTube video: **PLACEHOLDER — add after recording and upload**
