# Telegram Group Signal Ingest Implementation Plan

> **For Codex:** Implement this plan task-by-task with test-first changes. This workspace is not a Git repository, so do not create branches or commits.

**Goal:** Replace the manual signal shortcut with a local Telegram personal-account listener that parses configured source-group messages and injects validated football orders into the existing follow engine without forwarding messages to another group.

**Architecture:** A dedicated Telethon worker owns the user session and listens only to configured chat IDs. Pure parser and resolver helpers convert a supported text signal into the existing order JSON shape; match resolution calls the local read-only games API and requires one unique provider-number or team-name match before appending to `orders/telegram_signal.jsonl`. The WebUI exposes login, source selection, start/stop, dry-run/automatic mode, and recent event logs. Secrets never enter browser storage or API responses.

**Tech Stack:** Python 3.9, Telethon 1.42, stdlib HTTP/JSON/process tools, existing HDZZ WebUI server, Vanilla JavaScript/CSS, `unittest`, Node test runner.

---

## Safety Rules

- Default to `dry_run=true`; parsed messages are visible but cannot create orders until the user explicitly enables automatic following.
- Accept messages only from exact configured Telegram chat IDs.
- Deduplicate by `chat_id:message_id`; edited or replayed messages never create a second order.
- Require a unique match. Prefer exact `GNUM_H/GNUM_C`, then exact normalized home/away names; never bet an ambiguous fuzzy match.
- Preserve the incoming score and let the existing follow engine score guard stop stale live orders.
- Do not forward, reply to, edit, or delete any Telegram message.
- Store the Telethon `StringSession` and API credentials in a mode-0600 local secrets file. Return only masked status to the browser.
- The supplied `/Users/zsc5200x/Desktop/tg转发/` static prototype contains no API credentials. Per the user's direction, valid `TG_API_ID/TG_API_HASH` values are migrated once from `/Users/zsc5200x/Desktop/c/.env` into the app-local mode-0600 secrets file and are not requested in the UI.

## Task 1: Signal Text Parser

**Files:**
- Create: `src/hdzz/core/telegram_signal/__init__.py`
- Create: `src/hdzz/core/telegram_signal/parser.py`
- Create: `tests/test_telegram_signal_parser.py`

1. Write tests for the supplied live-handicap sample.
2. Assert monitor name, member, order ID, date/time, sport, live mode, market, league, provider team numbers, home/away names, incoming score, selection, signed split line, odds, and amount.
3. Add tests for total-over/under, CRLF/full-width punctuation, unsupported messages, and missing required fields.
4. Run the test file and confirm failures are caused by the missing parser.
5. Implement `parse_telegram_bet_signal(text)` and `build_order_from_signal(signal, match)` with no Telegram dependency.
6. Run tests until green.

## Task 2: Match Resolution and Order Injection

**Files:**
- Create: `src/hdzz/core/telegram_signal/ingest.py`
- Create: `tests/test_telegram_signal_ingest.py`

1. Write tests for exact home/away provider-number matching, exact team-name fallback, reversed/mismatched numbers, ambiguity rejection, and no-match errors.
2. Write tests that an injected order contains `raw._EVENT`, `GID`, `GNUM_H/GNUM_C`, `TEAM_H/TEAM_C`, `ORDER_TYPE`, `MARKET`, `WAGERSTYPE`, score fields, and the original Telegram identifiers.
3. Verify tests fail before implementation.
4. Implement the smallest resolver and atomic JSONL append helper.
5. Run parser and ingest tests.

## Task 3: Secret, Settings, State, and Logs

**Files:**
- Create: `src/hdzz/core/telegram_signal/store.py`
- Create: `tests/test_telegram_signal_store.py`

1. Test default dry-run settings, exact chat-ID normalization, secret masking, mode-0600 writes, bounded dedupe keys, and bounded event logs.
2. Verify failures.
3. Implement settings at `data/telegram_signal.json`, secrets at `data/telegram_signal_secrets.json`, state at `.run/telegram_signal_state.json`, and logs at `data/telegram_signal_events.jsonl`.
4. Run tests.

## Task 4: Telethon Login and Listener

**Files:**
- Create: `src/hdzz/core/telegram_signal/client.py`
- Create: `src/hdzz/core/telegram_signal/watcher.py`
- Create: `app/telegram_signal_watch.py`
- Create: `tests/test_telegram_signal_watcher.py`

1. Test worker decisions using injected adapters: ignore unconfigured chats, ignore duplicates, log parse failures, dry-run valid signals, reject unresolved matches, and append one resolved automatic order.
2. Verify failures.
3. Implement StringSession login helpers for send-code, verify-code, optional 2FA password, logout, and dialog listing.
4. Implement the NewMessage worker with no outbound Telegram methods.
5. Resolve games through `http://127.0.0.1:2299/api/chashui/status` and `/api/chashui/games`, selecting live/today/early modes based on the signal.
6. Run watcher tests and syntax checks.

## Task 5: WebUI API and Process Control

**Files:**
- Modify: `src/hdzz/api/webui/server.py`
- Modify: `app/app.py`
- Create: `tests/test_telegram_signal_webui.py`

1. Test sanitized status/config payloads and allowed settings fields.
2. Add GET endpoints for status, chats, and recent logs.
3. Add POST endpoints for credentials, send-code, verify-code, settings, start, stop, and logout.
4. Add a worker/PID definition that can run from source now and as a packaged worker later.
5. Ensure API credential hashes, session strings, phone numbers, codes, and 2FA passwords are never returned or logged.
6. Run focused WebUI tests.

## Task 6: TG Signal Page

**Files:**
- Create: `app/webui/tg-signal/index.html`
- Create: `app/webui/tg-signal/app.js`
- Create: `app/webui/tg-signal/style.css`
- Modify: `app/webui/index.html`
- Create: `tests/test_telegram_signal_page.js`

1. Write source-level UI tests for the new shortcut, required controls, no target-group controls, and no browser secret persistence.
2. Verify failures.
3. Build a compact operations page matching the existing AG Matrix palette and density.
4. Use a three-stage rail as the signature interaction: `账号连接 -> 来源群 -> 信号入单`.
5. Include account state, phone/code/2FA dialog, searchable source group list, dry-run/automatic segmented control, start/stop button, parsed-message table, and explicit rejection reasons.
6. Remove all forwarding-target and forwarding-rule concepts.
7. Run Node tests and syntax checks.

## Task 7: Runtime Deployment and Verification

**Files:**
- Copy only changed static assets and source-launchable scripts into `/Users/zsc5200x/Library/Application Support/HD Sports后台系统 2299/app/` where applicable.
- Update runtime cache-buster versions.

1. Run all new Python and Node tests plus existing match-search/manual-signal focused tests.
2. Restart only the source-backed WebUI on port 2299 if server code changed.
3. Start the Telegram signal worker only after credentials and source chats exist; otherwise keep it stopped with a clear `credentials_missing` state.
4. Use the in-app browser to verify the shortcut, responsive layout, login states, source selection, dry-run behavior, and logs.
5. Feed the supplied sample through a local dry-run API/test fixture and verify the UI shows the parsed match without writing a live order.
6. Report that real Telegram login remains blocked if the actual `api_id/api_hash` values are still unavailable.
