# Demo video script

Target: **2:42**. No music. Use only hosted synthetic data and a WebMCP-capable browser.

| Time | Screen action | Narration |
| --- | --- | --- |
| 0:00–0:18 | Open the clean hosted main screen. Briefly show summary, Quick Add, upcoming, and the disposable-demo label. | “Budgeting should answer three things quickly: what came in, what went out, and what remains. Olos-AI Mini Budget Planner does that without accounts, subscriptions, bank access, telemetry, or an embedded chatbot.” |
| 0:18–0:38 | In Quick Add, manually save a small synthetic debit expense. Show Recent Activity and summary update. | “The human experience stays deliberately ordinary. I can add a transaction in seconds, and deterministic Python logic updates the same screen.” |
| 0:38–0:48 | Expand Data and backup tools; show “WebMCP ready · 11 structured tools.” | “But the application also describes eleven real capabilities directly to compatible agents through WebMCP.” |
| 0:48–1:12 | Agent prompt: **“Record a $27.60 expense for Demo cafe lunch today, category Dining, paid by debit. Do not change anything else.”** Approve the write if prompted. Show the new row appear. | “This is not pixel clicking. The agent receives a strict schema for amount, date, category, type, and payment method. The tool calls the existing validated API, then the human UI refreshes.” |
| 1:12–1:34 | Agent prompt: **“List the next 30 days of upcoming items and tell me which are bills or loan repayments.”** Show structured result and corresponding Upcoming rows/calendar markers. | “Read tools expose bounded structured projections. The agent can distinguish income, savings, ordinary bills, and the recurring payment linked to the demo loan.” |
| 1:34–2:04 | Agent prompt: **“Record the returned Demo car repayment occurrence using its rule ID and due date.”** Show one Recent Activity repayment, monthly cashflow, and lower loan balance. | “Recording the occurrence reuses the same domain path as the Record button. One payment leaves cash once, accrues deterministic loan interest once, and reduces the estimated liability once. It is not double-counted as ordinary spending.” |
| 2:04–2:27 | Show a split view or README architecture diagram, then briefly show `document.modelContext.registerTool` in `webmcp.js`. | “The human UI and WebMCP tools are peer interfaces over one FastAPI and financial engine. No calculations live in prompts, and the Budget Planner itself sends nothing to an AI service.” |
| 2:27–2:42 | Return to the polished UI and repository page. | “Web apps don’t need AI embedded into them to become agent-native. They can expose reliable, structured capabilities while staying small, private, and useful.” |

## Fallbacks

- If the transaction mutation fails, use the already-tested prompt with `27.60`, `Dining`, today's ISO date, and `debit`; then refresh once.
- If the agent cannot select the repayment, first call `list_upcoming_items`, copy the returned `rule_id` and `due_date`, then call `record_recurring_item` explicitly.
- If loan recording was already performed in that session, use Reset all data with the visible `RESET` confirmation to reseed, or open a fresh private browser session.
- If the hosted free service is asleep, wake it before recording and begin only after `/api/health` returns `status: ok`.
- Never switch to the local personal database or show real financial data.
