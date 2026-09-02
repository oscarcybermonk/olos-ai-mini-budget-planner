# Product status

## Implemented scope

The first usable local version includes:

- transaction create, edit, confirmed delete and duplicate-submit protection;
- expense, income, bill and savings types using AUD cents;
- adjustable recurring income, bills and savings using every N weeks, months or years (with legacy fortnightly schedules preserved);
- projected upcoming, due, recorded and skipped occurrence states;
- one-action recording of a projected occurrence into actual history;
- deterministic selected-month summaries with planned-versus-actual separation;
- a deliberately small local transaction-text parser and browser speech capture;
- responsive, touch-friendly single-page UI with light/dark presentation;
- installable PWA metadata and cached application shell;
- CSV transaction export, complete JSON backup and guarded JSON restore;
- localhost-only default and explicitly enabled trusted-LAN operation;
- automatic schema creation and default category seeding;
- backend and frontend-focused automated tests.
- field-level Quick Add validation, visible activity actions and a UI-only first-run example;
- compact recurring calendar markers and accessible recurring-action cues;
- optional generic credit/pay-later/fixed-loan facilities, editable rates, linked loan repayments and manual loan reconciliation;
- full recurring-rule and facility edit/confirmed-delete controls with historical preservation;
- protected typed-confirmation reset and a compact credit/liability position strip;
- lightweight Work, Business and Olos-AI expense categories/tags.

## Architecture

FastAPI serves both a REST API and the dependency-free production web interface. SQLite persistence is isolated in the public project's `.mini-runtime/data/` directory. Startup applies safe schema migration for facility types plus additive columns for payment methods, recurring intervals, loan links and balance events without resetting existing records. Domain helpers own exact money conversion, recurrence advancement, daily fixed-loan simple interest and voice-text parsing. Recurring database rows hold schedule definitions; only user-recorded or skipped occurrences are persisted.

The normal user path is one Python server and one browser tab. No Node process is needed for normal use.

## Deliberate exclusions

There is no bank connection, reconciliation, tax logic, investing or trading, account execution, multi-user identity, cloud sync, remote AI, receipt capture, financial advice, analytics, advertising or public hosting. The automated indicator describes a real-world payment; the application never transfers money.

## Known limitations

- One currency (AUD) is supported per installation.
- Restore accepts only version 1 backups produced by this application and replaces current records.
- LAN mode relies on trusted-network isolation and has no login or TLS. It must not be exposed to the public internet.
- Browser speech recognition differs by browser and may be unavailable on some iPhones. Keyboard dictation remains a supported fallback.
- PWA shell caching helps the interface load, but financial operations require connection to the local server.
- Revolving-account APR is display-only. Fixed-loan interest is a deterministic daily simple-interest estimate and may differ from a lender due to fees, posting rules, redraws or adjustments.
- Monthly recurrence uses calendar clamping while retaining the original anchor day. For example, a schedule started on the 31st uses February's last day, then returns to the 31st when available.
- Custom recurrence intervals multiply the selected unit deterministically: every 2 weeks advances 14 days, every 3 months advances three calendar months, and every 2 years advances 24 calendar months. Editing a rule changes future projections only.

## Next logical phase

Use the tool with real personal workflows, then prioritise only observed friction. Likely candidates are optional local-network access protection, category shortcuts, and richer backup review. Bank or tax expansion should remain a separate, explicitly scoped project.

## Possible future Apple client

A plausible next phase is an offline-first SwiftUI client for iPhone, iPad and macOS calling the existing local REST API, with native speech capture and a future optional private sync mechanism. A Watch companion should remain tiny: quick expense/income/savings and voice entry, payment-method/account choice where practical, marking a recurring item paid, and glances at available cash and upcoming commitments. No SwiftUI, WatchKit, cloud relay or Apple sync is implemented here.
