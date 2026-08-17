# Stop-latched close — Design

**Status:** Draft — ready for an implementation plan
**Date:** 2026-08-12
**Ships before:** the `cl_ord_id` idempotency work, tracked in `docs/BACKLOG.md`. That spec is revised to sit on top of this one (see §7); it is not on `main` yet, so it is deliberately not linked here.

## Problem

Hitting the trailing stop is the bot's only exit decision. Today that decision is
revocable by an API failure.

`tick_position` detects the breach and calls `close_position`, which places a
limit order. When `place_limit_order` returns `None` — a timeout, a rejection,
a rate-limit hit — `close_position` logs and returns **without writing anything**
(`trading/positions_manager.py:325-327`). Nothing records that the stop ever
fired. `is_open` is still `True`, so the next tick runs the whole of
`tick_position` again, and that body does not simply retry the close:

- **The breach can be silently cancelled by recalibration**
  (`positions_manager.py:220-224`). If ATR has drifted past `ATR_DESV_LIMIT`,
  `update_stop_price` recomputes the stop from `trailing_price` with the new ATR.
  A higher ATR widens the stop distance and moves the stop *away* from price, so
  the check at line 226 no longer triggers. The position that hit its exit one
  tick ago now runs on with a looser stop, and nothing anywhere records that it
  fired.
- **A bounce re-arms the trail** (`positions_manager.py:232-238`). If price
  recovers past `trailing_price`, the stop ratchets up and the position continues
  as though the stop had never been touched.

The same hole exists downstream: when `is_closing_complete` finds a terminal
order that cannot be finalized (`canceled`/`expired` with a remainder), it clears
the closing fields and the position "resumes management" — back into
`tick_position`, with the exit still owed.

So whether the bot exits or keeps holding depends on whether one HTTP call
happened to succeed. That is infrastructure leaking into strategy, and it is
wrong independently of which outcome turns out more profitable.

## Scope

**In:** the exit path from the moment the stop is breached until the position is
recorded closed — `is_open`, `close_position`, `reprice_closing_order`,
`is_closing_complete`'s terminal branch, and the scheduler's position block.

**Out:** everything in *Non-goals*. In particular this changes **no** stop
distance, no activation logic, and adds no new exit trigger. It makes the
existing trigger final.

## Design

### 1. `stop_at` — the latch

`trailing_state.closing_requested_at` is renamed to **`stop_at`** and its
lifecycle changes:

| | `closing_requested_at` (today) | `stop_at` |
| --- | --- | --- |
| Written | on a **successful** placement | on the **first** placement attempt |
| Rewritten | never (a reprice keeps it, per #63) | never |
| Cleared | with the closing tuple on a terminal outcome | never, while the position exists |
| Gone | when the row is deleted | when the row is deleted |

It means *"the stop was breached and an exit is owed"*. Once set, the position
is out of `tick_position`'s hands for good; the only remaining work is achieving
the exit.

> Implemented as a plain assignment in `tick_position`, not the `setdefault` in
> `close_position` this section originally proposed. Same three properties, one
> fewer indirection — see below.

The write lives in `tick_position`, on the breach branch, immediately before the
`close_position` call:

```python
pos["stop_at"] = now_utc()
logging.info(f"[{pair}] ⛔ Stop price … hitted …", to_telegram=True)
close_position(pair, pos, last_prices)
```

Three properties follow. It runs before anything that can raise, so even an
exception on the breach tick leaves the position latched and retried. It runs
before `place_limit_order`, so a failed placement latches too — which is the
whole point. And it cannot overwrite an earlier breach, because `tick_position`
only runs while `is_open` is `True` and the assignment is what makes it `False`;
`setdefault` would be dead defensiveness against a path that does not exist.

Latching at the breach rather than inside `close_position` also keeps the
decision where it is made: `close_position` becomes a placement primitive that
takes an already-latched position, which is what lets `manage_close_position`
reuse it for every retry.

### 2. `is_open` becomes one condition

```python
def is_open(pos: dict[str, Any] | None) -> bool:
    return bool(pos) and not pos.get("stop_at")
```

``closing_order_id`` needs no clause here: it is set only by ``close_position``,
which latches ``stop_at`` first, or by ``reprice_closing_order``, which only
ever runs on an already-latched position. Either way `stop_at` subsumes it.
This is the single choke point — a latched position can never reach
`tick_position`, so no recalibration and no bounce can un-decide the exit.

**Why a dedicated field rather than reusing an existing one.** The latch must
*never* be cleared while the position lives; every other closing field must be
cleared when its attempt dies (a spent `closing_order_id` re-adopted next tick
would loop forever). Opposite lifecycles cannot share a field, and an explicit
`stop_at` also survives someone later adding a field to a clearing tuple.

### 3. `manage_closing_order` — one owner for an owed exit

> **Partly superseded.** Shipped as `manage_close_position` returning a
> three-value `ClosingState` (`FILLED`/`PENDING`/`UNMANAGED`) instead of a
> `bool`, and callers gate on `is_closing(pos)`, so the "no `stop_at`" row below
> no longer exists. `FILLED` also absorbed the finalize branch, which is why the
> scheduler still performs every DB write. The sub-state reasoning is unchanged.

`reprice_closing_order` is renamed and generalized. It now owns every state
between the breach and the fill:

```python
def manage_closing_order(
    pair: str,
    pos: dict[str, Any],
    balance: dict[str, Any],
    last_prices: dict[str, float],
    trailing_state: dict[str, Any],
) -> bool:
    """Drive an owed exit toward a resting order. Returns False when the pair
    could not be driven and must be marked failed."""
```

It dispatches on the sub-state and delegates; the branch bodies stay separate
helpers so the function itself is a dispatcher, not a 60-line block:

| Sub-state | Branch |
| --- | --- |
| no `stop_at` | return `True` — the position is open, nothing owed |
| `closing_order_id` set | `reprice_closing_order`, whose `bool` return propagates out |
| `stop_at` set, no `closing_order_id` | **re-place**: `refresh_position`, then `close_position` |

The re-place branch calls `refresh_position` first, for two reasons. A latched
position never enters `tick_position`, so nothing else resizes it, and a stale
volume may be exactly why Kraken rejected the previous attempt. More importantly
it is the **only natural termination** for a position that can never be placed:
`refresh_position` drops the position when its value falls under `MIN_VALUE`, the
scheduler's `finally` deletes the row, and the retry loop ends. The reprice
branch deliberately does **not** refresh — its volume must match what rests at
Kraken, which is what PR #64's remainder sizing computes.

`close_position` gains a `bool` return (placed / not placed) so the dispatcher
can report upward. `reprice_closing_order` gains one too, and the dispatcher
returns it: the reprice path can cancel the resting exit and then fail to place
a replacement, which leaves the pair just as unmanaged as a failed placement.

**`False` means "the pair is latched with nothing resting at Kraken", not "the
position is gone".** A drop by `refresh_position` returns `True`: the pair was
resolved, there is nothing left to place, and marking it failed would alert on a
normal dust outcome. `False` covers exactly two shapes — a placement attempted
and failed, and a confirmed cancel with no replacement placed (a failed or
non-terminal post-cancel re-query, or a failed `place_limit_order`). Every
`reprice_closing_order` early return that leaves the original order on the book
returns `True`.

One ordering consequence to accept deliberately: the manager runs *before*
`create_position` in the per-pair block, so a drop here frees the pair to open a
fresh position on the same tick, where a drop inside `tick_position` (which runs
after) would wait a tick. It is harmless, but not because the two calls agree:
`refresh_position` passes `force_side=side` (`positions_manager.py:113`) while
`create_position` does not (`:19`), so the latter picks whichever of
`buy_value`/`sell_value` is larger. A latched SELL dropped for a sub-`MIN_VALUE`
sell remainder can therefore open a **BUY** on the same tick. That is ordinary
inventory rebalancing — the same decision `create_position` would have made a
tick later before this change — and the new position is still refused below
`MIN_VALUE` on its own side. The only thing this ordering changes is *when* it
happens.

### 4. Failure reporting: `failed_pairs`, not a per-tick message

> **Superseded by §9.** The move away from a per-tick message stands; routing it
> through the *session* failure streak does not, and neither does the
> first-attempt gating in the last paragraph — the breach and the placement are
> two separate messages now, so neither needs a gate.

`close_position`'s placement error drops `to_telegram=True` and becomes a plain
`logging.error`; `manage_closing_order` returns `False` and the scheduler appends
the pair to `failed_pairs`.

Without this, a Kraken outage would send one Telegram error per pair per tick.
Routing through `failed_pairs` reuses the edge-triggered consecutive-failure
alert — one message per episode — and is the same reasoning already applied to a
pair with no price: a latched pair with no resting order is an *unmanaged* pair,
so it must not pass as a successful session. The operator is not left blind in
the meantime: the breach itself sends `"⛔ Stop price … hitted"` to Telegram on
the tick it happens. That line is also gated on the first attempt — it is emitted
by `close_position`, which the manager now calls every retry tick, so without the
gate it would flood exactly like the error it replaces.

### 5. Scheduler wiring

The position block changes by one branch. Today:

```python
if is_closing_complete(trailing_state.get(pair)):
    ...
elif (trailing_state.get(pair) or {}).get("closing_order_id"):
    reprice_closing_order(pair, trailing_state[pair], last_prices)
```

becomes:

```python
if is_closing_complete(trailing_state.get(pair)):
    ...
elif (trailing_state.get(pair) or {}).get("stop_at"):
    if not manage_closing_order(pair, trailing_state[pair], current_balance, last_prices, trailing_state):
        failed_pairs.append(pair)
```

**The manager runs after `is_closing_complete`, and the order is load-bearing.**
Two requirements pull in opposite directions and only one can be first:

- A terminal order that cannot be finalized must be **cleared before** the
  manager, so the replacement goes out on the same tick.
- A recovered order (once the idempotency spec lands, §7) would ideally be
  **adopted before** the finalize check, so a fill is recorded on the same tick.

Finalize-first costs one tick on the second: an order that already filled is
recorded 60 s later, with no effect on the fill price or the PnL. Manager-first
costs one tick on the first: a breached position sits with no order on the book
for a full interval. One is bookkeeping, the other is risk, so finalize wins.
This supersedes §5 of the idempotency spec, which put resolution before
`is_closing_complete`.

The `elif` chain is preserved for the same reason it exists today, and the
remaining steps are untouched: `create_position` is skipped because the row still
exists, and `tick_position` is skipped because `is_open` is `False`.

### 6. `is_closing_complete` keeps the latch

Its clearing tuple loses one member:

```python
for key in ("closing_order_id", "closing_price"):
    pos.pop(key, None)
```

A terminal order with a remainder still clears its own fields, but `stop_at`
survives, so the position does not resume trailing — the same tick's
`elif stop_at` branch re-places instead. Its warning text changes from "resuming
position management" to "re-placing the exit". The finalize path is untouched;
`record_position_closed` deletes the row, taking `stop_at` with it.

### 7. What this changes in the idempotency spec

> This section applies to the `cl_ord_id` idempotency work when it lands on its
> own branch; that spec is not on `main`, and rebasing it is out of scope here.

This spec makes that one smaller, and it must be revised when it lands:

- **§4 resolver.** The "order never landed" outcome no longer clears the closing
  fields and reopens the position. It clears `closing_request_id` /
  `closing_price` only, keeps `stop_at`, and falls through to the re-place branch
  — which is now a shared code path rather than a second placement site.
- **§5 scheduler step 3a is removed.** Resolution moves *inside*
  `manage_closing_order`, in front of its re-place branch: if a
  `closing_request_id` is present, look it up first and adopt the txid if Kraken
  has one; otherwise place with a fresh id. A failed lookup is the same `False`
  return as any other undrivable pair.
- **§6 is subsumed.** `is_open` needs no `closing_request_id` clause — the
  position is already not open by virtue of `stop_at`.
- **§2 migration** rebases: its `down_revision` moves from `20260616_01` to this
  spec's revision.
- The unified state model is then exactly what the two specs together describe:
  *open* (no `stop_at`), or *exit owed* with three sub-states — nothing
  outstanding, an unresolved request id, or a live order.

### 8. Storage and read-side changes

- **`core/db/models.py`** — `TrailingState.closing_requested_at` → `stop_at`,
  and the key in `to_dict`.
- **`core/db/positions.py`** — the field in `_state_entry_to_trailing_record`
  and `_trailing_record_to_state_entry`.
- **Migration** under `scripts/migrations/versions/`, `down_revision =
  "20260616_01"`: `op.alter_column("trailing_state", "closing_requested_at",
  new_column_name="stop_at")` and the inverse in `downgrade`. A rename, not a
  drop/add — live rows keep their value, and the old value's meaning ("when we
  asked to close") is close enough to the new one that no backfill is needed.
- **`api/schemas.py`** — `PositionDetail.closing_requested_at` → `stop_at`.
- **`services/grafana/dashboards/botc.json`** — the trailing-state panel's
  `closing_requested_at AS "Close Requested"` → `stop_at AS "Stop Hit"`.

`closed_positions` is **not** extended; see *Non-goals*.

### 9. Alerting: three independent signals, no error detail

Supersedes §4. Dropping the per-tick message was right; routing pair failures
into the *session* failure streak was not.

**What breaks in the §4 model.** It conflates three things:

- **A pair failure marks the whole session `failed`.** The per-pair `try/except`
  exists precisely so one pair does not stop the others — the session did
  complete its work for every other pair. With two pairs configured, one flaky
  pair paints half the Grafana Sessions row red.
- **The alert carries `failure_reason`, captured on the single tick the streak
  crossed the threshold.** If the cause changes while the streak continues — one
  pair error resolves and a different one starts — the operator keeps reading the
  first, now-stale reason and is never told about the second.
- **The streak's flag is cleared only by a `completed` session.** The latch
  introduces a class of *permanent* pair failure (API permission revoked, pair
  suspended at Kraken, an order rejected at a size `refresh_position` never drops
  below `MIN_VALUE`), so every session can be `failed` forever: one alert, then
  silence, and no later failure of any *other* pair ever alerts again.

**Status describes the session, not its pairs:**

`running` | `completed` | `pair_error` | `failed` | `paused`

`failed` means the session could not do its work — balance or prices
unavailable, an unhandled exception, a session row that could not be written.
`pair_error` means the session completed and one or more pairs were skipped.
`pair_error` is 10 characters and `sessions.status` is `String(16)` with no check
constraint, so the new value needs no migration.

**Three independent edge-triggered signals**, each with its own streak in
`core/runtime.py`, all reusing `SESSION_FAILURE_ALERT_THRESHOLD`:

| Signal | Counts | Resets on |
| --- | --- | --- |
| Session failure | consecutive `failed` sessions | a session that is not `failed` |
| Pair failure (**per pair**) | consecutive sessions in which *that pair* failed | a session in which that pair succeeded |
| Overrun | consecutive completed sessions with `elapsed >= SLEEPING_INTERVAL` | a completed session under the interval |

Keeping the threshold rather than alerting on the first occurrence is
deliberate: a single failed tick usually heals on the next one, and at threshold
1 every transient Kraken hiccup costs two messages — the alert and its recovery
— for a non-event.

Per-pair keying is what fixes the third bullet above: a permanently broken pair
holds only its own flag, so a new failure on a different pair still alerts.

A `failed` session leaves every pair streak untouched — neither incremented nor
reset. No pair ran, so there is nothing to record about any of them.

**Overrun stays out of the status enum.** It is orthogonal to the outcome: a
session can complete on time, complete late, complete with pair errors on time,
or complete with pair errors late. Collapsing both axes into one column lets one
signal mask the other — and the July 2026 CPU-starvation incident produced
exactly that combination, sessions overrunning *while* pairs began to fail.
Overrun also needs no column: it is derivable from `ended_at - started_at`.

**No error detail over Telegram.** A message says that something started failing
or recovered, and never why — the reason can change under a streak that never
resets, and a stale reason misleads worse than no reason. `failure_reason` and
`log_messages` keep the detail on the session row, and the message points there.
Pair messages *do* name the pair: that is a fact about the present, not a
snapshot, and without it the operator cannot tell which pair recovered.

Recovery is announced per signal and, for pairs, per pair — a pair that starts
succeeding again is announced even while another keeps failing and the session
stays `pair_error`.

**What this removes from `positions_manager`.** Four failure-detail messages
lose `to_telegram`: the unusable-fill-price clear in `is_closing_complete`, the
unconfirmed post-cancel volume and the failed re-placement in
`reprice_closing_order`, and the placement failure in `close_position`.

That last one lets **`close_position`'s `first_attempt` parameter disappear**,
but not on its own: the flag has a second use, suppressing the `🏁 Placed
closing order` message on the breach tick so it does not duplicate the `⛔ Stop
price … hitted: placing LIMIT …` line that `tick_position` emits just before.
Resolve it by splitting the two responsibilities instead of gating them: the
breach line drops its `placing LIMIT …` tail and announces only the decision,
and `close_position` announces the placement unconditionally. The breach tick
then sends two messages that say different things — the stop fired, and an order
now rests at Kraken — which is strictly better than today, where the single
message claims a placement it never confirms.

The trading lifecycle keeps every message it has: position created, position
dropped, activation reached, stop hit, closing order placed, repriced, partial
fill during the cancel window, and the close with its PnL. Those are events, not
failure detail.

### 10. An order Kraken cannot resolve

> Closes the freeze this branch's code review found. Supersedes nothing above; it
> adds the one exception to §6's clearing rule and the missing `UNMANAGED` route.

`get_order_state` returned `None` for two different things: an API error, and a
reply in which the txid is simply absent. The closing flow reads both as "ask
again next tick", so a `closing_order_id` that stops resolving — a txid lost to a
Kraken-side purge, a wrong account, a corrupted id — latched the pair forever and
reported `PENDING`, which is exactly the state that never alerts.

**`exchange/kraken.py` becomes the anti-corruption boundary.** An `OrderStatus`
`StrEnum` (`PENDING` / `OPEN` / `CLOSED` / `CANCELED` / `NOT_FOUND` / `UNKNOWN`)
plus a public `map_order_status` translator, reusable by any later order lookup.
Kraken's `expired` folds into `CANCELED`: both mean off the book with no further
fills, and the distinction never drove a branch. Anything unmodelled becomes
`UNKNOWN` rather than reaching the strategy as a raw string. `NOT_FOUND` and
`UNKNOWN` have no Kraken counterpart — they are this wrapper's way of saying
"answered, unusable". `get_order_state` now returns `None` for the API error
alone, so a transient outage is distinguishable from an order that will never
resolve. `OrderState.status` is typed `OrderStatus`, and no code outside the
module compares status strings.

**The strategy treats both as unmanaged, not as terminal.** `is_closing_complete`
leaves the fields untouched and returns `False`; `reprice_closing_order` logs and
returns `False`, so `manage_close_position` returns `UNMANAGED`, the pair reaches
`failed_pairs`, and §9's per-pair streak alerts. Two decisions:

- **This is the one exception to §6** ("no branch leaves a terminal order's fields
  in place"). §6's reasoning holds only for statuses we can *call* terminal.
  Clearing the id here and re-placing could put a second exit against a position
  that may still have one resting — a double sell is unrecoverable, a frozen pair
  is not. The objection §6 answers (frozen forever, silently) is answered here by
  the alert instead, not by acting blind.
- **It surfaces through `reprice_closing_order`, not `is_closing_complete`.** The
  finalizer's job is to finalize, and `manage_close_position` already routes a
  still-set `closing_order_id` into the repricer, so the verdict comes out on the
  same tick with no extra plumbing and no third return value.

The post-cancel re-query flips from a blocklist to an allowlist: only `CLOSED` or
`CANCELED` gives a definitive `vol_exec`, so `NOT_FOUND`/`UNKNOWN` bail there too
rather than sizing a replacement from an unconfirmed `0.0` — the over-sell the
re-query exists to prevent.

## Edge cases

| Case | Behaviour |
| --- | --- |
| Placement fails on the breach tick | Position latched, no order. Next tick re-places. It never re-enters `tick_position`. |
| Placement keeps failing | Re-placed every tick; pair marked failed each time, so the consecutive-failure alert fires once per episode. |
| Price recovers above the stop after a failed placement | Irrelevant — the exit stands and is re-placed. This is the behaviour change. |
| ATR drifts after a failed placement | Irrelevant — no recalibration runs on a latched position. |
| Latched position falls below `MIN_VALUE` | `refresh_position` drops it, the `finally` deletes the row, the retry loop ends. The manager returns `True` — a drop is not a failure. |
| A dropped pair reaches `create_position` on the same tick | Possible now that the manager precedes step 5. `create_position` runs its own unforced `calculate_position`, so it may open the **opposite** side (a dropped SELL becoming a BUY) — normal inventory rebalancing, still refused under `MIN_VALUE`, one tick earlier than before. |
| Order `canceled`/`expired` with a remainder | `is_closing_complete` clears the order fields and keeps `stop_at`; the same tick re-places. |
| `closing_order_id` stops resolving at Kraken | `NOT_FOUND`/`UNKNOWN`: nothing is touched and the pair is reported `UNMANAGED` every tick, so the per-pair streak alerts (§10). |
| Order canceled but fully executed | Unchanged — finalized by `is_closing_complete`, row deleted. |
| Operator cancels the closing order by hand at Kraken | The bot re-places it. Deliberate: the exit is owed until it is filled. Cancelling an exit means removing the position's state, not cancelling its order. |
| `close_position` raises before placing | Already latched by `tick_position`; retried next tick. |
| Backtest / optimizer | Unaffected: `trading/engine.py` never fails to place, so the latch never engages and live/simulated behaviour stay identical. |

## Testing

Unit tests only, module-level monkeypatching, in the existing style.

**`trading/positions_manager.py`**
- `close_position` sets `stop_at` **before** `place_limit_order` — assert from
  inside the fake that it is already set.
- A failed placement leaves `stop_at` set, `closing_order_id` absent,
  `is_open(pos) is False`, and returns `False`.
- A retry does **not** overwrite an existing `stop_at`.
- `is_open` is `False` with only `stop_at` set; `True` with neither field.
- The breach line reaches Telegram on the first attempt only, not on each retry.
- `manage_closing_order`: no-op + no API call when `stop_at` is absent; the
  reprice body runs when `closing_order_id` is set (keep PR #64's existing tests
  under the new name); the re-place branch refreshes then calls
  `close_position`; `False` propagates when the placement fails; a drop by
  `refresh_position` returns `True` and places nothing.
- `is_closing_complete` keeps `stop_at` on a terminal-with-remainder outcome
  while still clearing `closing_order_id` and `closing_price`.
- `NOT_FOUND`/`UNKNOWN` (§10): `is_closing_complete` leaves every field in place,
  `reprice_closing_order` cancels and places nothing and returns `False`, and
  `manage_close_position` reports `UNMANAGED` end to end.
- The post-cancel re-query bails on `NOT_FOUND`/`UNKNOWN` as it does on
  `PENDING`/`OPEN`.

**`exchange/kraken.py`**
- `map_order_status` folds `expired` into `CANCELED` and maps anything
  unmodelled — including an empty or missing status — to `UNKNOWN`.
- `get_order_state` reports `NOT_FOUND` when the txid is absent from an otherwise
  successful reply, and `None` only on an API error.

**`core/scheduler.py`**
- A latched pair with no order id reaches `manage_closing_order` and is skipped
  by `tick_position` — the regression for the defect this spec exists to fix.
  It belongs here rather than in `positions_manager`: `tick_position` has no
  internal guard, `is_open` at the call site is the guard.
- A `False` return adds the pair to `failed_pairs` and the state is still
  persisted by the `finally`.
- Terminal-with-remainder → cleared by `is_closing_complete` → re-placed by the
  manager on the **same** tick.

**`core/db/`**
- `stop_at` round-trips through `save_trailing_state` / `load_trailing_state`
  and is absent from the dict when the column is `NULL`.
- `alembic upgrade head` on a fresh DB, and `downgrade` back.

## Non-goals

- **A global stop-loss or any new exit trigger.** The trailing stop remains the
  only exit; this makes it final, it does not add a second one.
- **Recording `stop_at` in `closed_positions`** for breach-to-fill analytics.
  Genuinely useful, but it is a second migration and a Grafana panel for a
  question nobody is asking yet.
- **The `cl_ord_id` work** — separate spec, layered on this one (§7).
- **A periodic orphan sweep** for orders left by a killed process.
- **Partial-fill reconciliation** beyond PR #64's remainder sizing.
- **Reducing the three `get_order_state` calls per closing tick** (deferred
  backlog card); this spec adds none.

## Residual risk

- **A latched position that can never be placed is frozen** — no resting exit
  and no trailing stop, until `refresh_position` drops it or an operator
  intervenes. This is deliberate and is the same trade the idempotency spec
  makes for a stuck lookup: freezing is safer than un-deciding an exit. It
  surfaces through the per-pair failure alert once the streak reaches the
  threshold (§9), and the breach itself is announced on the tick it happens.
- **An order Kraken cannot resolve freezes the pair too** (§10), and deliberately
  so: the alternative is re-placing against a possibly-live exit. It differs from
  the case above in that no automatic path recovers it — `refresh_position` never
  runs on this branch — so it stays latched until an operator checks Kraken. The
  per-pair alert is the whole mitigation.
- **A hard process kill between the latch and the tick's `finally`** loses
  the latch, exactly as it loses `closing_order_id` today. Unchanged exposure.
- **The exit price can be worse than the un-latched behaviour** in the specific
  case where a placement fails and price then recovers. That is the intended
  trade: the alternative makes the exit price a function of Kraken's
  availability.

## Design choices to record in CLAUDE.md

- **Hitting the trailing stop is a latched, irrevocable decision (`stop_at`).**
  Why the latch is written before the placement attempt rather than after a
  successful one (a failed `place_limit_order` must not un-decide the exit), why
  it is never cleared while the position lives (a terminal order clears its own
  id; the owed exit survives), and why it is a dedicated field rather than
  reusing `closing_order_id`/`closing_request_id` (opposite lifecycles).
- **`is_open` is `not stop_at`.** Extends the existing invariant: a position
  whose stop has fired is not open, whether or not an order was placed.
- **`manage_closing_order` owns everything between the breach and the fill**, and
  runs *after* `is_closing_complete` — finalize-first costs one tick of
  bookkeeping, manager-first would cost one interval of unprotected exposure.
- Update the `reprice_closing_order` and `is_closing_complete` lifecycle text:
  a terminal order with a remainder no longer "resumes position management", it
  re-places the exit.
- **A session's status describes the session, not its pairs** (§9). Why
  `pair_error` is not `failed` (the per-pair guard exists so the session still
  completes), why the pair streak is keyed per pair (a permanently broken pair
  must not swallow a new failure elsewhere), and why overrun stays out of the
  enum (orthogonal axis; collapsing them lets one signal mask the other).
- **Telegram alerts carry no failure reason.** One message when a signal starts
  failing, one when it recovers, and nothing about why: the cause can change
  under a streak that never resets, so a reason captured at threshold-crossing
  goes stale and misleads worse than no reason at all. The detail stays in
  `failure_reason` and `log_messages`. This replaces the existing
  session-failure design-choice bullet, which describes the superseded model.
- **An order Kraken cannot resolve is reported unmanaged, never guessed at**
  (§10). Why it is the one exception to the "no branch leaves a terminal order's
  fields in place" rule (these statuses are not known to be terminal, and a
  double exit is unrecoverable while a frozen pair is not), and why the verdict
  comes out of `reprice_closing_order` rather than `is_closing_complete`. Plus a
  note in the exchange-wrapper section: `kraken.py` is the anti-corruption
  boundary, `OrderStatus`/`map_order_status` are its vocabulary, and `None` from
  `get_order_state` now means "could not ask" and nothing else.
