# `cl_ord_id`-based idempotent order placement — Design

**Status:** Draft — ready for an implementation plan
**Date:** 2026-08-05
**Backlog card:** `docs/BACKLOG.md` → 💤 Deferred → *`cl_ord_id`-based idempotent order placement*

## Problem

The bot identifies its orders only by the `txid` Kraken returns from `AddOrder`.
When that response is lost — a read timeout, a dropped connection — `_safe_call`
returns `None`, `place_limit_order` returns `None`, and the caller aborts. But
the order may be **live at Kraken** with its id never received. The position dict
keeps no trace of the attempt, so the next tick sees an open position with its
stop still hit and places a **second** exit for the same holding. The bot then
sells (or buys) twice what it intended, and the second order's `txid` is the only
one it tracks.

The same loss applies to the replacement order placed inside
`reprice_closing_order`: there the cancel has already succeeded, so a lost
response means the position is left with a dead `closing_order_id` and an
unknown live replacement.

There is no way to ask Kraken "did *my* order land?" without an identifier the
bot chose *before* sending the request. That identifier is `cl_ord_id`.

## Scope

**In:** the closing path — `close_position` and the replacement inside
`reprice_closing_order`. These are the only two call sites of
`place_limit_order` in the codebase (verified by grep); `create_position` places
no order at all — a "position" is a trailing stop over inventory the account
already holds, so there *is* no opening order to make idempotent. The scoping
question in the backlog card resolves itself: closing-path-only is not a
narrowing, it is the whole surface.

**Already fixed, do not re-specify:** sizing a replacement against a fill that
landed inside the cancel/replace window. PR #64 re-queries the order after a
successful cancel and sizes the replacement at `volume - vol_exec`.

**Out:** everything in *Non-goals* below.

## What Kraken gives us

Verified against the REST API docs (2026-08-05). Stated explicitly so a wrong
assumption is visible and cheap to correct:

| Endpoint | Fact the design depends on |
| --- | --- |
| `AddOrder` | Accepts a `cl_ord_id` **string** request field. Formats: long UUID (36 chars with dashes), short UUID (32 hex chars, no dashes), or free ASCII text ≤ 18 chars. Must be unique among the client's **open** orders. **Mutually exclusive with `userref`.** |
| `AddOrder` response | Returns `txid` + `descr`. The docs' example does **not** echo `cl_ord_id` back — the design never relies on it being echoed. |
| `QueryOrders` | Requires `txid` (schema `required: [nonce, txid]`) and additionally accepts `userref`. **Neither field can look up an order whose txid is unknown**, which is exactly our case — this is why resolution cannot reuse the existing `get_order_state`. |
| `OpenOrders` | Accepts `cl_ord_id` as a filter ("restrict results to given client order id"). Result is `{"open": {txid: order, ...}}`; each order object carries `cl_ord_id` back. |
| `ClosedOrders` | Accepts `cl_ord_id` as a filter, plus `start`/`end`/`ofs`. Returns the 50 most recent by default; `start` is **exclusive**, `end` inclusive, and `closetime` defaults to `both`. Result is `{"closed": {txid: order, ...}, "count": n}`. |

`userref` is rejected as the mechanism. It is a 32-bit integer that Kraken does
not enforce uniqueness on and that is designed to *group* orders rather than
identify one; it is mutually exclusive with `cl_ord_id`; and — decisively — it
saves no work, because `QueryOrders` requires `txid` regardless, so the lookup
still has to go through `OpenOrders`/`ClosedOrders` either way. Choosing
`userref` would swap a server-unique string for a client-managed int inside the
identical code path.

## Design

### 1. The id — one per order attempt, `uuid4().hex`

```python
# core/utils.py
def new_cl_ord_id() -> str:
    return uuid.uuid4().hex  # 32 hex chars = Kraken's "short UUID" form
```

- **One id per *attempt*, not per position.** Each reprice places a genuinely
  new order, and Kraken requires uniqueness among open orders — reusing one id
  across a cancel/replace risks a reject (the old order may still be
  cancel-pending) and makes a `ClosedOrders` lookup ambiguous. A fresh id per
  attempt also means a stale id can never resolve to a different order.
- **`uuid4`, not a readable scheme.** The free-text form is capped at 18 ASCII
  characters, which does not comfortably hold pair + timestamp + enough entropy.
  Operator correlation is served instead by logging the id and storing it in the
  DB row.
- **Generated in `positions_manager`**, immediately before the placement, and
  written into the position dict **before** the `place_limit_order` call — see §3.

`place_limit_order` gains an optional parameter:

```python
def place_limit_order(pair, side, price, volume, cl_ord_id: str | None = None) -> str | None
```

It adds `"cl_ord_id": cl_ord_id` to the `AddOrder` payload only when not `None`.
Optional (rather than required) so existing tests and any future non-idempotent
call site stay valid; both production call sites always pass one.

### 2. Where it is persisted

New optional key on the position dict: **`closing_request_id`**, alongside the
existing `closing_order_id` / `closing_price` / `closing_requested_at`.

The name deliberately differs from Kraken's: the position dict and the DB column
use the domain name `closing_request_id`, while everything that produces or
transports the Kraken-format identifier keeps the API's vocabulary —
`new_cl_ord_id()`, `place_limit_order`'s `cl_ord_id` parameter and payload key,
`find_order_by_cl_ord_id`. Both refer to the same value; the boundary is the
exchange wrapper.

That requires:

- **`core/database.py`** — `TrailingState.closing_request_id = Column(Text, nullable=True)`,
  plus the field in `to_dict`, `_state_entry_to_trailing_record` and
  `_trailing_record_to_state_entry` (the latter only when not `None`, matching
  the other optional fields).
- **A new Alembic migration** under `scripts/migrations/versions/`
  (`down_revision = "20260616_01"`), a single
  `op.add_column("trailing_state", sa.Column("closing_request_id", sa.Text(), nullable=True))`
  and the matching `drop_column` in `downgrade`. No index (lookups are by the
  `pair` primary key) and no check constraint. Per CLAUDE.md, model and
  migration are updated in the same change — CI builds the schema from
  migrations.
- **`closed_positions` is *not* extended.** Its audit key is the unique
  `closing_order_id`, which by definition exists for every recorded close (a
  position only reaches `record_position_closed` after `is_closing_complete`
  confirmed a fill). Adding the column there would be dead weight.

`positions_manager` still never imports `core.database`: it only mutates the
dict. Persistence stays the scheduler's job through `_persist_pair_state` in the
per-pair `finally` — the same guarantee that already exists for
`closing_order_id` (A5) now covers the client id.

**The crash window, stated plainly.** Because persistence happens in the tick's
`finally`, an id generated just before `AddOrder` is durable only once the pair
block ends. A hard process kill *between* the HTTP send and that `finally` loses
it, and that attempt is unrecoverable — exactly the residual already accepted
for `closing_order_id`. This design closes the *lost-response* exposure (the
common one: a Kraken timeout, where the process keeps running and the `finally`
executes), not the *killed-process* one.

The alternative that would close both was considered and rejected: pre-mint a
`next_cl_ord_id` at position creation and rotate it after each use, so the id
for the next attempt is always durable a tick in advance. It works, but it
cannot distinguish "this reserved id was never used" from "it was used and the
response was lost", so every tick with an open position would need an
unconditional `OpenOrders` probe, and the position dict would carry a two-id
state machine. That is a real per-tick cost and real complexity to close a
window strictly narrower than the one Design A closes. Revisit only if
crash-mid-placement is ever observed.

### 3. Placement changes

**The state invariant this whole design rests on:**

> `closing_request_id` set **and** `closing_order_id` absent
> ⇒ an `AddOrder` was sent whose outcome is unknown.

Both call sites are shaped to produce exactly that state and nothing else.

**`close_position`** — write the pending fields *before* placing:

```python
cl_ord_id = new_cl_ord_id()
pos.update({
    "closing_request_id": cl_ord_id,
    "closing_price": current_price,      # still an estimate, unchanged semantics
    "closing_requested_at": now_utc(),
})
closing_order = place_limit_order(pair, side, current_price, volume, cl_ord_id=cl_ord_id)
if not closing_order:
    logging.error(...)   # existing Telegram error stays
    return               # pending state persists; §4 resolves it next tick
pos.update({"volume": round(volume, 8), "closing_order_id": closing_order})
```

Moving `closing_requested_at` a few lines earlier changes nothing observable —
it already means "when we asked to close".

**`reprice_closing_order`** — after PR #64's post-cancel re-query and the
`remaining > 0` check, and *before* placing the replacement:

```python
cl_ord_id = new_cl_ord_id()
pos["volume"] = remaining          # confirmed by the post-cancel re-query
pos["closing_price"] = current_price
pos["closing_request_id"] = cl_ord_id
pos.pop("closing_order_id", None)  # the old order is confirmed canceled
new_order = place_limit_order(pair, side, current_price, remaining, cl_ord_id=cl_ord_id)
if not new_order:
    logging.error("Failed to re-place closing order after cancel.", to_telegram=True)
    return
pos["closing_order_id"] = new_order
```

Three deliberate moves here:

1. **`volume = remaining` before the call, not after.** If the response is lost
   but the order landed, the persisted volume must already be the remainder;
   PR #64 writes it only on success, which would leave stale sizing behind.
2. **Dropping the old `closing_order_id`.** It is confirmed canceled, so its
   txid carries no further information, and keeping it would create an ambiguous
   mixed state where `is_closing_complete` resolves the *old* order and wipes
   the pending client id along with it. Order of the scheduler's steps also
   depends on this — see §5.
3. **This supersedes a documented decision.** Today, "cancel succeeded,
   replacement failed" deliberately keeps the dead `closing_order_id` so
   `is_open` stays `False` and the next tick's terminal-status branch clears it.
   Under this design the same protection comes from `closing_request_id` (§6),
   and the clearing is done by the resolver. The CLAUDE.md text describing the
   old behaviour must be updated in the same PR.

   Note the knock-on: a replacement that Kraken *rejects outright* (not lost —
   e.g. insufficient funds) now also goes through the resolver, which finds
   nothing and clears the fields. Same end state as today, one extra lookup.

The early-return paths added by PR #64 (post-cancel re-query failed,
`remaining <= 0`) send no `AddOrder` and are untouched: they keep the old
`closing_order_id` and today's behaviour.

### 4. Resolution

**New exchange wrapper — `exchange/kraken.py`:**

```python
@dataclass(frozen=True)
class OrderLookup:
    txid: str | None      # None when the order does not exist
    status: str | None    # from the matched order, when Kraken reports one


def find_order_by_cl_ord_id(cl_ord_id: str) -> OrderLookup | None:
    """Resolve a client order id to Kraken's txid.

    Returns None when the lookup itself failed (API error) — the caller must
    treat that as 'unknown', never as 'absent'. Returns OrderLookup(txid=None)
    only when BOTH OpenOrders and ClosedOrders answered successfully and
    neither contained the id."""
```

Three-valued on purpose, mirroring `get_order_state`'s `OrderState | None`:
"absent" licenses a retry, "unknown" must not.

Flow: `OpenOrders {"cl_ord_id": ...}` → if `result["open"]` is non-empty, take
its single key. Otherwise `ClosedOrders {"cl_ord_id": ...}` → if
`result["closed"]` is non-empty, take the most recent entry. If *either* call
returns `None` from `_safe_call`, return `None` — a conclusive "absent" requires
both to have succeeded. More than one match cannot happen with per-attempt
UUIDs; if it does, log an error and prefer the open one.

**No `start`/`end` bound on the `ClosedOrders` call, deliberately.** `start` is
documented as *exclusive* and is compared against the order's own timestamps
(`closetime` defaults to `both`), while `closing_requested_at` comes from our
clock a moment *before* the order exists at Kraken. If the two land on the same
whole second, the bound excludes the very order being resolved and the resolver
reads "absent" — the one error direction that leads to a second exit. The bound
also buys nothing: the resolver runs on the tick after the placement, so the
order is among the newest closed orders and the default page is the 50 most
recent. If pagination ever proves to be a real problem, add `start` with an
explicit margin (`closing_requested_at - N seconds`), never a tight bound.

These are private calls, so they are not covered by `_wait_rate_limit` (which
wraps only the public path) — consistent with every other private call in the
module. They run only on the pending path, which is rare.

**New function — `trading/positions_manager.py`:**

```python
def resolve_unconfirmed_closing_order(pair: str, pos: dict[str, Any] | None) -> bool:
    """Resolve a placement whose response was lost. Returns True when the
    position is safe to manage this tick (nothing pending, or the pending id was
    resolved / proven absent) and False when the lookup failed and the position
    must be left untouched."""
```

- Nothing pending (`not pos`, no `closing_request_id`, or `closing_order_id`
  already set) → `True`, no API call.
- Lookup returns a txid → `pos["closing_order_id"] = txid`, log to Telegram
  (this is a recovered order, the operator should know), return `True`. The
  resolver deliberately does **not** inspect the status: writing the txid in is
  enough, because the *same tick* then runs `is_closing_complete`, whose
  existing branches already handle every outcome — `closed` with a usable
  average price is finalized and recorded, and any other terminal status clears
  the closing fields and resumes management. No new terminal-state logic, and no
  wasted tick.
- Lookup returns "absent" → the order never landed. Clear
  `closing_request_id`, `closing_order_id`, `closing_price`,
  `closing_requested_at` (the same tuple `is_closing_complete` clears, extended
  with the new key), log a warning to Telegram, return `True`. The position is
  open again and the same tick's `tick_position` may legitimately re-close it —
  which is correct: nothing was placed.
- Lookup failed (`None`) → return `False`. Leave every field as it is and retry
  next tick.

**Why "absent" can be trusted.** The only way to reach the pending state is a
placement whose response never arrived — which means the request had already
been outstanding for the full `KRAKEN_HTTP_TIMEOUT` read window (30 s) before
the caller gave up. By the time the *next* tick queries, any order that landed
has existed for at least that long, far beyond any plausible propagation lag
into `OpenOrders`. `ClosedOrders` covers the case where it landed and already
filled or was canceled.

### 5. Scheduler wiring

Resolution becomes step 3a of the per-pair loop, **before** `is_closing_complete`
(a pending replacement must be resolved before any branch looks at
`closing_order_id`):

```python
if not resolve_unconfirmed_closing_order(pair, trailing_state.get(pair)):
    logging.error("Could not resolve an unconfirmed closing order; skipping this pair.")
    failed_pairs.append(pair)
    continue

if is_closing_complete(trailing_state.get(pair)):
    ...
```

The `continue` still runs the `finally`, so `_persist_pair_state` writes
whatever the tick changed — unchanged behaviour.

**Marking the pair failed is the alerting mechanism.** An unresolved order means
an unmanaged position with a possibly-live order — precisely the reasoning
already applied to a pair with no price ("an unpriced pair is an *unmanaged*
pair"). Reusing `failed_pairs` routes it into the existing edge-triggered
consecutive-failure Telegram alert instead of adding a new, floodable alert
channel: one message per episode, not one per tick during a Kraken outage. The
first failure is already announced by `close_position`'s existing
`to_telegram` error.

### 6. `is_open` must account for the pending state

```python
def is_open(pos) -> bool:
    return bool(pos) and not pos.get("closing_order_id") and not pos.get("closing_request_id")
```

Without this, a position in the pending state passes `is_open`, reaches
`tick_position`, and places the second exit this whole design exists to prevent.
`is_open` is the single choke point for "may I manage/close this position", and
the two keys are always cleared together, so the extra condition is safe.
This extends the CLAUDE.md invariant to: *a position with `closing_order_id`
**or** `closing_request_id` set is not open.*

## Edge cases

| Case | Behaviour |
| --- | --- |
| `AddOrder` response lost, order landed | Next tick's resolver adopts the txid; `is_closing_complete` on the same tick finalizes or clears it. |
| `AddOrder` response lost, order never landed | Resolver proves absence, clears the closing fields, position resumes; the same tick may re-close with a **new** id. |
| Lookup itself fails | Pair marked failed, state untouched, retried next tick; sustained failure surfaces through the existing consecutive-failure alert. |
| Id resolves to an already-terminal order (`canceled`/`expired`/`closed`) | No special path — the txid is written in and `is_closing_complete`'s existing terminal branch handles it on the same tick. |
| Stale id from a previous position | Impossible to mis-resolve: ids are per-attempt UUIDs, so a stale id can only ever match its own order. A closed position's row is deleted by `record_position_closed`; every other clearing path clears `closing_request_id` with the rest of the tuple. |
| Kraken rejects a duplicate `cl_ord_id` | Cannot arise — no id is ever sent twice. If it somehow did, `_safe_call` turns the reject into `None` and the pending path handles it. |
| Multiple matches for one id | Log an error, prefer the open order. Should be unreachable. |
| Crash between the HTTP send and the tick's `finally` | Not covered. See §2 and *Residual risk*. |
| Opening order | Does not exist — `create_position` places no order. |

## Testing

Unit tests only, module-level monkeypatching, in the existing style
(`monkeypatch.setattr(positions_manager, "place_limit_order", ...)`,
`monkeypatch.setattr(kraken.api, "query_private", ...)` with a captured payload
dict, as `test_place_limit_order_rounds_to_pair_precision` already does).

**`exchange/kraken.py`**
- `place_limit_order` includes `cl_ord_id` in the `AddOrder` payload when given
  and omits the key entirely when `None`.
- `find_order_by_cl_ord_id`: hit in `OpenOrders`; miss in `OpenOrders` + hit in
  `ClosedOrders`; miss in both → `OrderLookup(txid=None)`; `OpenOrders` errors →
  `None`; `OpenOrders` empty + `ClosedOrders` errors → `None` (the load-bearing
  one: an error must never read as "absent").

**`trading/positions_manager.py`**
- `close_position` writes `closing_request_id` **before** calling
  `place_limit_order` — assert from inside the fake `place_limit_order` that
  `pos["closing_request_id"]` is already set and equals the `cl_ord_id` argument.
- A `None` return leaves the pending state (`closing_request_id` set,
  `closing_order_id` absent) and `is_open(pos) is False`.
- `reprice_closing_order` on the placement path: old `closing_order_id` dropped,
  new id set, `volume == remaining`, all before the call; on success the new
  txid is written; on a lost response the pending state remains.
- PR #64's early-return paths still keep the old `closing_order_id` (regression).
- `resolve_unconfirmed_closing_order`: no-op + no API call when nothing pending;
  txid adopted on a hit; fields cleared on a proven miss; `False` + untouched
  state on a lookup error.
- `is_open` is `False` while only `closing_request_id` is set.

**`core/scheduler.py`**
- A pending pair whose lookup fails is added to `failed_pairs`, skips the rest
  of the position block, and is still persisted by the `finally`.
- Resolution followed by `is_closing_complete` finalizing on the **same** tick.

**`core/database.py`**
- `closing_request_id` round-trips through `save_trailing_state` /
  `load_trailing_state`, and is absent from the dict when the column is `NULL`.
- Whatever ORM/migration parity check the repo already runs covers the new
  column.

**Only a live account can prove:**

- That Kraken accepts `cl_ord_id` on `AddOrder` for this account tier and does
  not reject the order.
- That `OpenOrders` / `ClosedOrders` filtered by `cl_ord_id` actually return the
  order, and how soon after placement it becomes visible.
- Whether `ClosedOrders` with a `cl_ord_id` filter searches beyond its default
  time window / first page.
- The real behaviour on a duplicate `cl_ord_id`.

Recommended rollout: two ordered PRs with a live check between them, no feature
flag.

**PR 1 — placement only, no behaviour change.** Generate the id, send it, persist
it, log it. `is_open` is *not* tightened and `reprice_closing_order` keeps its
current field handling, so the bot behaves exactly as it does today; the id is
pure diagnostics. This ordering matters: tightening `is_open` before the resolver
exists would freeze a pending position with nothing able to clear it, which is
worse than the exposure being fixed.

**Live gate between the PRs:** confirm on the account that an order placed by the
bot carries the `cl_ord_id`, and that `OpenOrders`/`ClosedOrders` filtered by it
return that order.

**PR 2 — recovery.** The resolver, the `is_open` tightening, the
`reprice_closing_order` restructure, the scheduler step and the CLAUDE.md
updates. Only here does behaviour change.

## Non-goals

- The opening order (there is none).
- Partial-fill reconciliation beyond PR #64's remainder sizing — `refresh_position`
  still converges the size on the next tick.
- A periodic orphan sweep (`OpenOrders` with no filter, matched against known
  `closing_order_id`s) to catch orders left by a killed process. Worth doing
  later; it is a different mechanism with a different trigger.
- Any change to `record_position_closed`, PnL, or the meaning of `closing_price`
  as an estimate until `is_closing_complete` confirms the fill.
- Passing the `OrderState` down to avoid the double `get_order_state` per closing
  tick (separate deferred card).
- Migrating away from `krakenex` or adding a retry/backoff layer.

## Residual risk

- **Killed process mid-placement.** Covered in §2: the id is durable only from
  the end of the pair block. Unchanged from today's exposure for
  `closing_order_id`, and the orphan sweep above is the eventual fix.
- **Resolution stuck for a long outage.** While `find_order_by_cl_ord_id` keeps
  failing, the position is frozen and unmanaged — its trailing stop does not
  move. This is a deliberate trade (freezing is safer than possibly
  double-exiting), it is visible through the pair-failure alert, and it ends as
  soon as Kraken answers.
- **Up to two extra private API calls** on the pending path (`OpenOrders` +1,
  `ClosedOrders` +4 on the counter), inside a session whose duration is already
  alarmed on (`SLEEPING_INTERVAL` overrun). Rare enough that it should not move
  the needle; see *Resolved questions* 3 for the budget and why exhausting it
  degrades safely.

## Resolved questions

Settled with the operator against the API docs on 2026-08-05. Kept here because
each one shaped a decision above.

1. **Pagination on the `ClosedOrders` fallback is a non-issue** — but not for the
   obvious reason. Closed orders do accumulate quickly (every reprice cancels and
   replaces, so a chasing exit produces roughly one closed order per tick). What
   makes the default 50-result page sufficient is *timing*: the resolver runs on
   the tick immediately after the lost response, so the order is always among the
   newest. That guarantee does **not** transfer to any lookup that runs long
   after placement — an orphan sweep would need its own bounding strategy.
2. **`cl_ord_id` is returned on the order objects** the bot reads back, so a
   future orphan sweep can prove an unknown open order is ours.
3. **`ClosedOrders` costs +4 on the private rate-limit counter** (account-history
   endpoints), against a 20-point ceiling that refills at 1 point/sec;
   `OpenOrders` and `QueryOrders` cost +1, and `AddOrder`/`CancelOrder` cost 0.
   **The fallback leg is deliberately not gated.** It only runs when `OpenOrders`
   missed, so the common resolution costs +1; the worst case (every pair pending
   in the same tick) requires several `AddOrder` responses lost at once, i.e. an
   outage during which the lookups fail anyway; and exhausting the counter
   degrades safely — Kraken returns a rate-limit error, `_safe_call` yields
   `None`, the resolver reports "unknown", and the pair is marked failed and
   retried next tick. Gating logic would add a state machine to avoid a failure
   that is already handled. With `SLEEPING_INTERVAL = 60` the counter refills
   fully between sessions, so this is a within-session burst question only.

   Note the pair-count implication: at +4 per fallback plus the session's other
   private calls against a 20-point ceiling, the simultaneous-pending worst case
   is bounded at roughly 4 pairs. Confirm the account tier before growing beyond
   that — 20 points at 1/sec is Kraken's Pro tier; lower tiers have a smaller
   ceiling and a slower refill.

## Design choices to record in CLAUDE.md

On implementation, add to the **Design choices** section and adjust the
lifecycle text:

- **Every order the bot places carries a per-attempt `cl_ord_id`, so a lost
  `AddOrder` response is recoverable.** Why per attempt and not per position
  (Kraken's open-order uniqueness + cancel/replace), why `cl_ord_id` and not
  `userref` (32-bit, groups orders, mutually exclusive), and why resolution goes
  through `OpenOrders`/`ClosedOrders` rather than `QueryOrders` (which does not
  accept the field).
- **`closing_request_id` set with `closing_order_id` absent means "outcome
  unknown"**, and `is_open` is `False` for both — the single choke point that
  prevents a second exit.
- Update the `reprice_closing_order` description: the dead `closing_order_id` is
  no longer kept after a failed replacement; the pending client id now provides
  that protection.
