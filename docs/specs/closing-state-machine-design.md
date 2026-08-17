# Closing state machine — idempotent placement and single dispatch

**Status:** Draft — ready for an implementation plan
**Date:** 2026-08-17
**Builds on:** [`stop-latched-close-design.md`](stop-latched-close-design.md) — the `stop_at` latch, `ClosingState`, and the `OrderStatus` enum are assumed to be in place.
**Backlog card:** `docs/BACKLOG.md` → 💤 Deferred → *`cl_ord_id`-based idempotent order placement*
**Supersedes:** the 2026-08-05 draft of this file, written before the latch and the status enum. Its research (the Kraken endpoint facts, the `userref` rejection, the rate-limit findings) survives; its wiring does not, because it was organized around a state model that no longer exists.

## Problem

Two problems that turn out to be the same problem.

**1. A lost `AddOrder` response is indistinguishable from a rejection.** The bot
identifies its orders only by the `txid` Kraken returns. When that response is
lost — a read timeout, a dropped connection — `_safe_call` returns `None`,
`place_limit_order` returns `None`, and the caller reports a failed placement.
But the order may be **live at Kraken** with its id never received. The position
keeps no trace of the attempt, so the next tick places a **second** exit for the
same holding. The bot then sells twice what it intended, and only the second
order's `txid` is tracked.

The `stop_at` latch made this worse, not better. Before it, the re-place attempt
only happened while the stop was still breached; now the re-place branch retries
every tick until an order rests. Same failure, more exposure.

There is no way to ask Kraken "did *my* order land?" without an identifier chosen
*before* sending the request. That identifier is `cl_ord_id`.

**2. The order's state is re-derived three times per tick.** `is_closing_complete`
queries the order and branches on its status; `reprice_closing_order` queries the
same order again and branches on the same status, overlapping on `None`,
`PENDING`, `OPEN` and the unresolvable statuses; then it queries a third time
after cancelling. Each function re-validates what the caller already established
— that a position exists, that it is latched, that an id is set. Adding a fourth
sub-state (a placement whose outcome is unknown) to that shape would multiply the
overlap rather than absorb it.

So this spec does both at once: it introduces the client id **and** collapses the
management logic into one selector with one status dispatch.

## Scope

**In:** the closing path — `manage_close_position` and everything it calls, plus
the exchange wrapper's lookup surface.

**Out of scope by construction:** the opening order. `create_position` places no
order at all — a "position" is a trailing stop over inventory the account already
holds. The closing path is not a narrowing, it is the whole order surface
(verified: `place_limit_order` has exactly two call sites).

**Already shipped, not re-specified:** sizing a replacement against a fill that
landed inside the cancel/replace window (PR #64), and the `stop_at` latch with
its `ClosingState`/`OrderStatus` vocabulary (PR #67).

**Out:** everything in *Non-goals*.

## What Kraken gives us

Verified against the REST API docs (2026-08-05, re-checked 2026-08-16). Stated
explicitly so a wrong assumption is visible and cheap to correct.

| Endpoint | Fact the design depends on |
| --- | --- |
| `AddOrder` | Accepts a `cl_ord_id` **string** request field. Formats: long UUID (36 chars with dashes), short UUID (32 hex chars, no dashes), or free ASCII text ≤ 18 chars. Must be unique among the client's **open** orders. **Mutually exclusive with `userref`.** |
| `AddOrder` response | Returns `txid` + `descr`. The docs' example does **not** echo `cl_ord_id` back — this design never relies on it being echoed there. |
| `QueryOrders` | Requires `txid` (schema `required: [nonce, txid]`). **Cannot look up an order whose txid is unknown**, which is exactly the lost-response case — this is why resolution cannot reuse `get_order_state`. |
| `OpenOrders` | Accepts `cl_ord_id` as a filter. Result is `{"open": {txid: order, ...}}`; each order object carries `cl_ord_id` back. |
| `ClosedOrders` | Accepts `cl_ord_id` as a filter, plus `start`/`end`/`ofs`. Returns the 50 most recent by default; `start` is **exclusive**, `end` inclusive, `closetime` defaults to `both`. Result is `{"closed": {txid: order, ...}, "count": n}`. |
| `CancelOrder` | Accepts `cl_ord_id` as well as `txid` (confirmed by the operator, 2026-08-17). The design keeps cancelling by `txid` anyway — see §7. |

`userref` is rejected as the mechanism. It is a 32-bit integer Kraken does not
enforce uniqueness on, designed to *group* orders rather than identify one; it is
mutually exclusive with `cl_ord_id`; and decisively, it saves no work, because
`QueryOrders` requires `txid` regardless, so the lookup goes through
`OpenOrders`/`ClosedOrders` either way. Choosing it would swap a server-unique
string for a client-managed int inside the identical code path.

## Design

### 1. The state model

`stop_at` set means an exit is owed (unchanged). Within that, three sub-states,
keyed on **which id is present**:

| Sub-state | Fields | Meaning |
| --- | --- | --- |
| Confirmed | `closing_order_id` set | An `AddOrder` succeeded; we hold Kraken's own id for the order. |
| Unconfirmed | `closing_request_id` set, `closing_order_id` absent | An `AddOrder` was sent and its outcome is unknown. |
| Nothing outstanding | neither | No placement is in flight. |

**The routing key is confirmation, not existence — and the two cannot be
collapsed into one.** This is the load-bearing decision of the whole design, so
it is worth stating why plainly.

It is tempting to make `closing_request_id` the single handle: we mint it before
every placement, so every order has one, and one lookup would serve both states.
It does not work, because *"Kraken does not have this order"* means **opposite
things** in the two states:

- **Unconfirmed** — we never received a txid. The request was outstanding for the
  full `KRAKEN_HTTP_TIMEOUT` read window (30 s) before we gave up, so by the next
  tick anything that landed is visible. "Not found" is genuine evidence that
  nothing landed, and re-placing is correct.
- **Confirmed** — Kraken *told us* the order exists. "Not found" now means the
  lookup failed to see it: a pagination window, filter semantics we guessed
  wrong, eventual consistency. Re-placing would leave two live exits. The only
  safe answer is `UNMANAGED`, which is exactly the rule the latch spec already
  established (*an order Kraken cannot resolve is reported unmanaged, never
  guessed at*).

A single key would force one interpretation on both, and the wrong direction is a
double sell. So the distinction stays — it happens to be free, since the presence
of `closing_order_id` already encodes it.

### 2. The id — one per attempt, `uuid4().hex`

```python
# core/utils.py
def new_cl_ord_id() -> str:
    """A client order id for one placement attempt (Kraken's 'short UUID' form)."""
    return uuid.uuid4().hex
```

- **One id per *attempt*, not per position.** Each reprice places a genuinely new
  order, and Kraken requires uniqueness among open orders — reusing one id across
  a cancel/replace risks a reject (the old order may still be cancel-pending) and
  makes a `ClosedOrders` lookup ambiguous. A fresh id per attempt also means a
  stale id can never resolve to a different order.
- **`uuid4`, not a readable scheme.** The free-text form caps at 18 ASCII
  characters, too small for pair + timestamp + enough entropy. Operator
  correlation comes from the log line and the DB row instead.
- **Generated in `positions_manager`**, immediately before the placement, and
  written into the position dict **before** the `place_limit_order` call.

`place_limit_order` gains an optional parameter:

```python
def place_limit_order(pair, side, price, volume, cl_ord_id: str | None = None) -> str | None
```

It adds `"cl_ord_id": cl_ord_id` to the `AddOrder` payload only when not `None`.
Optional rather than required so existing tests and any future non-idempotent
call site stay valid; both production call sites always pass one.

### 3. Persistence

New optional key on the position dict: **`closing_request_id`**, alongside
`closing_order_id` / `closing_price`.

The name deliberately differs from Kraken's: the position dict and the DB column
use the domain name, while everything that produces or transports the
Kraken-format identifier keeps the API's vocabulary — `new_cl_ord_id()`,
`place_limit_order`'s `cl_ord_id` parameter and payload key,
`find_order_by_cl_ord_id`. Same value; the exchange wrapper is the boundary.

That requires:

- **`core/db/models.py`** — `TrailingState.closing_request_id = Column(Text, nullable=True)`,
  plus the field in `to_dict`, `_state_entry_to_trailing_record` and
  `_trailing_record_to_state_entry` in `core/db/positions.py` (the latter only
  when not `None`, matching the other optional fields).
- **A new Alembic migration** under `scripts/migrations/versions/`
  (`20260817_01`, `down_revision = "20260812_01"`), a single
  `op.add_column("trailing_state", sa.Column("closing_request_id", sa.Text(), nullable=True))`
  and the matching `drop_column`. No index (lookups are by the `pair` primary
  key), no check constraint. Per CLAUDE.md, model and migration change together —
  CI builds the schema from migrations.
- **`closed_positions` is *not* extended.** Its audit key is the unique
  `closing_order_id`, which by definition exists for every recorded close.

`positions_manager` still never imports `core.database`: it only mutates the
dict. Persistence stays the scheduler's job through `_persist_pair_state` in the
per-pair `finally` — the same A5 guarantee that already covers `closing_order_id`
now covers the client id.

**The crash window, stated plainly.** Because persistence happens in the tick's
`finally`, an id generated just before `AddOrder` is durable only once the pair
block ends. A hard process kill *between* the HTTP send and that `finally` loses
it, and that attempt is unrecoverable — exactly the residual already accepted for
`closing_order_id`. This design closes the *lost-response* exposure (the common
one: a Kraken timeout, where the process keeps running and the `finally`
executes), not the *killed-process* one.

The alternative that would close both was considered and rejected: pre-mint a
`next_cl_ord_id` at position creation and rotate it after each use, so the id for
the next attempt is always durable a tick in advance. It works, but it cannot
distinguish "this reserved id was never used" from "it was used and the response
was lost", so every tick with an open position would need an unconditional
`OpenOrders` probe, and the position would carry a two-id state machine. Real
per-tick cost and real complexity to close a window strictly narrower than the
one this design closes. Revisit only if crash-mid-placement is ever observed.

### 4. `manage_close_position` — the selector

The one entry point, reduced to routing. It picks the sub-state, obtains the
order's state once, and hands it to the pieces that act on it.

```python
def manage_close_position(pair, pos, balance, last_prices, trailing_state) -> ClosingState:
    """Drive a latched position from the stop breach to the fill; FILLED reports it, never writes it."""
    if txid := pos.get("closing_order_id"):
        # Confirmed: Kraken gave us this id, so "not found" can only mean unmanaged.
        if (outcome := _drive_closing_order(pair, pos, get_order_state(txid), last_prices)) is not None:
            return outcome

    elif cl_ord_id := pos.get("closing_request_id"):
        # Unconfirmed: an AddOrder went out and we never learned what happened to it.
        found = find_order_by_cl_ord_id(cl_ord_id)
        if found is None:
            return ClosingState.UNMANAGED          # could not ask; decide nothing
        if found.txid:
            pos["closing_order_id"] = found.txid   # it landed after all
            logging.warning(f"[{pair}] Recovered closing order {found.txid} from its client id.", to_telegram=True)
            if (outcome := _drive_closing_order(pair, pos, found.state, last_prices)) is not None:
                return outcome
        else:
            _clear_closing_fields(pos)             # it never landed; stop_at survives

    # Nothing outstanding — either from the start, or just cleared above.
    if not refresh_position(pair, pos, balance, last_prices, trailing_state):
        return ClosingState.PENDING                # dropped: a resolved pair, not a failure
    return ClosingState.PENDING if close_position(pair, pos, last_prices) else ClosingState.UNMANAGED
```

`_clear_closing_fields(pos)` pops `closing_order_id`, `closing_request_id` and
`closing_price`, and **never** `stop_at`: the exit stays owed.

**Why resolution runs before `refresh_position`.** `refresh_position` can *drop*
the position when it falls below `MIN_VALUE`, and dropping a position that may
have a live order at Kraken orphans that order with nobody left to reclaim it.
Resolve first, size second.

**Why the adoption is evaluated on the same tick.** `ClosedOrders`/`OpenOrders`
return the full order object, so `find_order_by_cl_ord_id` hands back the
`OrderState` alongside the txid at no extra cost. Feeding it straight into the
same dispatch removes the tick of delay the earlier draft accepted: a recovered
order that already filled is finalized immediately.

### 5. `_drive_closing_order` — the single dispatch

The **only** place in the codebase that branches on `OrderStatus` for a closing
order. `None` means "the order is gone and the fields are cleared — place a new
one now", which is what lets the selector fall through on the same tick.

```python
def _drive_closing_order(pair, pos, state, last_prices) -> ClosingState | None:
    if state is None or state.status is OrderStatus.PENDING:
        return ClosingState.PENDING          # could not ask, or not on the book yet
    if state.status in UNRESOLVABLE_STATUSES:
        logging.error(f"[{pair}] Cannot resolve closing order (status={state.status}); leaving it untouched.")
        return ClosingState.UNMANAGED        # never guessed at
    if state.status is OrderStatus.OPEN:
        return ClosingState.PENDING if reprice_closing_order(pair, pos, state, last_prices) else ClosingState.UNMANAGED
    if finalize_close(pos, state):           # CLOSED or CANCELED
        return ClosingState.FILLED
    logging.warning(f"[{pair}] Closing order ended as {state.status} with no usable fill; re-placing the exit.")
    _clear_closing_fields(pos)
    return None
```

`PENDING` returns without cancelling deliberately: an order Kraken has accepted
but not yet put on the book cannot be repriced, and cancel/replace on it is pure
churn.

**No branch leaves a terminal order's fields in place.** A terminal status can
never change again, so a position that kept them would be frozen with no exit
ever re-placed. `UNRESOLVABLE_STATUSES` is the single exception, and precisely
because those are the statuses we cannot call terminal.

### 6. `finalize_close(pos, state) -> bool`

`is_closing_complete` renamed and reduced: it no longer queries Kraken, no longer
checks whether a position or an id exists, and no longer clears anything. It
receives an order already known to be terminal and answers one question — is
there a usable fill? — writing the real `closing_price` and `pnl_percent` when
there is.

The interpretation logic is carried over unchanged, because it is the most
hard-won code in the module:

- Two outcomes finalize: a `CLOSED` order with a positive average fill price, and
  a `CANCELED` order that turned out to be fully executed. The second is a cancel
  that raced a complete fill — Kraken confirms the cancellation but nothing is
  left to manage, so it is a finished trade, and it records `volume` as the
  order's real `vol_exec`.
- **Fullness is measured against the order's own `vol`, never `pos["volume"]`,**
  which can drift from what actually rests at Kraken. When Kraken omits `vol` it
  reads `0.0` and the check fails closed.
- Anything else returns `False`; the caller clears and re-places.

Clearing moves out to `_drive_closing_order` on purpose: state transitions belong
to the selector, interpretation belongs here.

### 7. `reprice_closing_order(pair, pos, state, last_prices) -> bool`

Receives an order already known to be `OPEN`. Four guards disappear — the
`if not order_id` check, the `get_order_state` call, the `state is None` check
and the `status != OPEN` check — all now the caller's business. What remains is
the part that is genuinely about repricing:

```python
def reprice_closing_order(pair, pos, state, last_prices) -> bool:
    """Chase the fill of an open closing order; False only when it is off the book unreplaced."""
    order_id = pos["closing_order_id"]
    if state.vol_exec > 0:
        return True                          # executing at its price; don't fragment the fill
    current_price = last_prices[pair]
    if round_price(pair, current_price) == round_price(pair, pos.get("closing_price")):
        return True                          # identical limit; re-placing would only lose queue priority
    if not cancel_order(order_id):
        return True                          # cancel unconfirmed: it likely still rests, or filled

    # A fill can land during the cancel round trip, so re-query for the definitive
    # vol_exec — only a terminal status gives one. Bailing is safe: nothing placed.
    post_cancel = get_order_state(order_id)
    if post_cancel is None or post_cancel.status not in TERMINAL_STATUSES:
        return False                         # confirmed cancel, no replacement: unmanaged

    remaining = post_cancel.vol - post_cancel.vol_exec
    if remaining <= 0:
        return True                          # nothing left; branch 1 finalizes it next tick

    cl_ord_id = new_cl_ord_id()
    pos.update({"volume": remaining, "closing_price": current_price, "closing_request_id": cl_ord_id})
    pos.pop("closing_order_id", None)        # confirmed canceled; its txid carries no more information
    new_order = place_limit_order(pair, pos["side"], current_price, remaining, cl_ord_id=cl_ord_id)
    if not new_order:
        return False                         # unconfirmed: the next tick resolves it
    pos["closing_order_id"] = new_order
    return True
```

Four things worth defending here.

**1. The remainder is sized from the order, not from the position.** Today it is
`pos["volume"] - vol_exec`. But `place_limit_order` rounds the volume to the
pair's `lot_decimals` before sending, so `pos["volume"]` and the order's `vol`
differ by up to one lot tick. That drift produces a real bug: an order that
executed *completely* against its own `vol` yields a dust `remaining` of ~1e-8,
the replacement is rejected below `ordermin`, and a finished trade is never
recorded. Sizing from `post_cancel.vol - post_cancel.vol_exec` uses the
exchange's own numbers, makes `remaining <= 0` correct in that case, and lets
branch 1 finalize it on the next tick. It also fails closed when Kraken omits
`vol`: `remaining` goes negative, nothing is placed, and the next tick's
terminal-but-unusable path clears and re-places.

**2. The state is written before the call, not after.** `volume`,
`closing_price` and `closing_request_id` all land in `pos` first. If the response
is lost but the order landed, the persisted state must already describe the order
that exists — today's code writes the volume only on success, which would leave
stale sizing behind for the resolver to work from.

**3. The dead `closing_order_id` is dropped.** Today it is deliberately kept, so
the next tick's terminal-status branch clears it and the re-place branch fires on
that same tick. That trick is now unnecessary *and* harmful: keeping it would put
the position in the Confirmed sub-state while a `closing_request_id` for a
possibly-live new order sits unresolved, and branch 1 would never look for it.
The protection it provided is now provided better — a failed replacement is no
longer permanently unmanaged, because branch 2 resolves it next tick.

**4. Cancellation stays by `txid`.** `CancelOrder` accepts `cl_ord_id`, but the
txid is already in hand at this point (branch 1 routed here on it), and cancelling
by the id the exchange itself assigned avoids depending on the filter semantics
this design otherwise has to verify. `cl_ord_id` earns its place where it is the
*only* option — resolving an order whose txid we never received.

### 8. `close_position`

The estimate and the id are written before the call, for the same reason as
above:

```python
cl_ord_id = new_cl_ord_id()
current_price = last_prices[pair]
pos.update({"closing_request_id": cl_ord_id, "closing_price": current_price})
closing_order = place_limit_order(pair, pos["side"], current_price, volume, cl_ord_id=cl_ord_id)
if not closing_order:
    logging.error(f"[{pair}] Closing order not confirmed; it remains owed and will be resolved next tick.")
    return False
pos["closing_order_id"] = closing_order
```

`closing_price` keeps its meaning exactly — an estimate until `finalize_close`
overwrites it with the real fill. Only the line it is written on moves.

The error message changes from "failed to place" to "not confirmed", because that
is now what a `None` means: the order may or may not exist.

### 9. `find_order_by_cl_ord_id` — the exchange wrapper

```python
@dataclass(frozen=True)
class OrderLookup:
    txid: str | None          # None when neither endpoint knows the id
    state: OrderState | None  # the matched order, ready for the dispatch


def find_order_by_cl_ord_id(cl_ord_id: str) -> OrderLookup | None:
    """Resolve a client order id to Kraken's txid and the order's state.

    None when the lookup itself failed — the caller must treat that as 'unknown',
    never as 'absent'. OrderLookup(txid=None) only when BOTH endpoints answered
    and neither contained the id."""
```

Three-valued on purpose, mirroring `get_order_state`'s `OrderState | None`:
"absent" licenses a re-place, "unknown" must not.

**`ClosedOrders` first, then `OpenOrders`.** The resolver runs on the tick after a
lost response, on a limit order placed at the market price — which most often has
already filled. Trying the likely endpoint first makes the common case one call
instead of two. The ordering is a pure cost choice with no effect on correctness:
a conclusive "absent" requires both to have succeeded either way, and with
per-attempt UUIDs an order cannot be in both lists, so there is no precedence
question to resolve.

**The match is verified, not assumed.** Each returned order carries `cl_ord_id`
back, and the wrapper checks it before adopting the txid. If Kraken ever ignored
the filter instead of applying it, the response would not come back empty — it
would come back with *every* order, and taking "the single key" would adopt an
unrelated one. The check is free and it is the difference between a wrong
assumption failing loudly and failing silently. More than one match cannot happen
with per-attempt UUIDs; if it does, log an error and prefer the open order.

**No `start`/`end` bound on the `ClosedOrders` call, deliberately.** `start` is
documented as *exclusive* and is compared against the order's own timestamps
(`closetime` defaults to `both`), while any bound we could compute comes from our
clock a moment *before* the order exists at Kraken. If the two land on the same
whole second, the bound excludes the very order being resolved and the resolver
reads "absent" — the one error direction that leads to a second exit. It also
buys nothing: the resolver runs on the tick after the placement, so the order is
among the newest and the default page is the 50 most recent. If pagination ever
proves to be a real problem, add `start` with an explicit margin, never a tight
bound.

Both are private calls, so neither is covered by `_wait_rate_limit` (which wraps
only the public path) — consistent with every other private call in the module.
They run only on the unconfirmed path, which is rare.

### 10. What the scheduler sees

Nothing changes. It still asks `is_closing(pos)`, makes one call, and reacts to
one of three `ClosingState` values, with `record_position_closed` on `FILLED` and
`failed_pairs` on `UNMANAGED`. The single-writer rule (A5) is untouched:
`positions_manager` mutates the dict, the scheduler writes the DB.

`is_open` stays `not stop_at`. The earlier draft added a `closing_request_id`
clause to it; that is unnecessary now, because a position with a pending request
is latched by definition and was never open.

## Call budget

Per tick of a closing position, private Kraken calls:

| Path | Today | After |
| --- | --- | --- |
| Order resting or filled, no reprice | 1 | 1 |
| Reprice (cancel + replace) | 3 | 2 |
| Unconfirmed placement, order found | — | 1 (2 if still open) |
| Unconfirmed placement, absent | — | 2 |

The reprice path drops from three `get_order_state` calls to two, because the
finalize check and the pre-cancel check are now one lookup whose result flows
into both. That closes the deferred *"`get_order_state` is called three times per
closing tick"* backlog card as a side effect.

## Edge cases

| Case | Behaviour |
| --- | --- |
| `AddOrder` response lost, order landed | Next tick's branch 2 adopts the txid and drives it on the same tick — finalized, repriced, or cleared as its status dictates. |
| `AddOrder` response lost, order never landed | Branch 2 proves absence, clears the closing fields, keeps `stop_at`, and re-places with a **new** id on the same tick. |
| Lookup itself fails | `UNMANAGED`, state untouched, retried next tick; a sustained failure surfaces through the per-pair alert streak. |
| Confirmed order goes `NOT_FOUND`/`UNKNOWN` | `UNMANAGED`, fields untouched. Never re-placed — see §1. |
| Recovered id resolves to an already-terminal order | No special path: the `OrderState` from the lookup goes through the same dispatch. |
| Replacement fails after a confirmed cancel | Now recoverable: the position holds only `closing_request_id`, and branch 2 resolves it next tick. Today this state is permanently unmanaged. |
| Order fully executed but `pos["volume"]` drifted by rounding | `remaining` computed from the order's own `vol` is `<= 0`, nothing is placed, and branch 1 finalizes on the next tick (§7). |
| Stale id from a previous position | Cannot mis-resolve: ids are per-attempt UUIDs, so a stale id can only ever match its own order. A closed position's row is deleted by `record_position_closed`; every other path clears the id with the rest of the tuple. |
| Kraken rejects a duplicate `cl_ord_id` | Cannot arise — no id is ever sent twice. If it somehow did, `_safe_call` turns the reject into `None` and the unconfirmed path handles it. |
| Kraken ignores the `cl_ord_id` filter | Caught by the echoed-id check (§9); the wrapper reports absent rather than adopting a stranger's txid. |
| Crash between the HTTP send and the tick's `finally` | Not covered. See §3 and *Residual risk*. |
| Opening order | Does not exist — `create_position` places no order. |

## Testing

Unit tests only, module-level monkeypatching, in the existing style
(`monkeypatch.setattr(positions_manager, "place_limit_order", ...)`,
`monkeypatch.setattr(kraken.api, "query_private", ...)` with a captured payload
dict, as `test_place_limit_order_rounds_to_pair_precision` already does).

**`exchange/kraken.py`**
- `place_limit_order` includes `cl_ord_id` in the `AddOrder` payload when given
  and omits the key entirely when `None`.
- `find_order_by_cl_ord_id`: hit in `ClosedOrders` (no `OpenOrders` call made);
  miss in `ClosedOrders` + hit in `OpenOrders`; miss in both →
  `OrderLookup(txid=None)`; `ClosedOrders` errors → `None`; `ClosedOrders` empty +
  `OpenOrders` errors → `None` (the load-bearing one: an error must never read as
  "absent"); a returned order whose `cl_ord_id` does not match is not adopted.

**`trading/positions_manager.py`**
- `manage_close_position` routes on the sub-state: confirmed → `get_order_state`
  and no lookup call; unconfirmed → lookup and no `get_order_state` call; neither
  → straight to `close_position`.
- Branch 2, all three outcomes: adopted (txid written, driven on the same tick),
  absent (fields cleared, `stop_at` kept, a **new** order placed on the same
  tick), lookup error (`UNMANAGED`, every field untouched, no placement).
- The adopted-and-already-filled case returns `FILLED` on the **same** tick.
- `_drive_closing_order`: one test per `OrderStatus`, including that
  `NOT_FOUND`/`UNKNOWN` leave the fields untouched and that a terminal-unusable
  status clears them and returns `None`.
- `finalize_close` takes an `OrderState` and makes no API call; the fully-executed
  cancel and the `vol`-vs-`pos["volume"]` fullness cases are carried over from the
  existing `is_closing_complete` tests.
- `reprice_closing_order` takes an `OrderState` and makes no pre-cancel query;
  the remainder comes from `post_cancel.vol - post_cancel.vol_exec`; the dust case
  (`vol_exec == vol` with a larger `pos["volume"]`) places nothing and returns
  `True`; on the placement path the old `closing_order_id` is dropped and the new
  id set **before** the call; a lost response leaves exactly the unconfirmed state.
- `close_position` writes `closing_request_id` before calling `place_limit_order`
  — assert from inside the fake that `pos["closing_request_id"]` is already set
  and equals the `cl_ord_id` argument.
- `refresh_position` is never reached while a lookup is unresolved (the drop
  hazard of §4).

**`core/db/`**
- `closing_request_id` round-trips through `save_trailing_state` /
  `load_trailing_state`, and is absent from the dict when the column is `NULL`.

**Only a live account can prove:**

- That Kraken accepts `cl_ord_id` on `AddOrder` for this account's verification
  tier and does not reject the order.
- That `ClosedOrders` / `OpenOrders` filtered by `cl_ord_id` actually return the
  order, and how soon after placement it becomes visible.
- Whether `ClosedOrders` with a `cl_ord_id` filter searches beyond its default
  time window / first page.
- The real behaviour on a duplicate `cl_ord_id`.

## Rollout

Two ordered PRs with a live check between them, no feature flag.

**PR 1 — placement.** `new_cl_ord_id()`, the `place_limit_order` parameter, the
column and migration, and both call sites writing the id before placing. Nothing
reads it. The routing in `manage_close_position` is unchanged, so the bot behaves
exactly as it does today and the id is pure diagnostics.

**Live gate between the PRs.** Four checks against one order the bot actually
places: Kraken accepts it with the `cl_ord_id`; `ClosedOrders` filtered by that id
returns it once terminal; `OpenOrders` filtered by it returns it while resting;
and how long it takes to become visible.

The gate is not ceremony. If the filter does not behave as documented, a lookup
that *succeeds* and returns empty is indistinguishable from a genuine absence —
the resolver would read "never landed", clear, and place a second exit. That is
the worst outcome in the system, and it is the one failure mode that cannot be
made fail-safe from inside the code: an API error we can detect and freeze on, a
confidently wrong answer we cannot. Everything in PR 2 rests on this behaving as
the docs say, so it gets confirmed before it is depended on.

**PR 2 — the state machine.** The selector, `_drive_closing_order`, the
`finalize_close` and `reprice_closing_order` refactors, `find_order_by_cl_ord_id`,
and the CLAUDE.md updates. All the behaviour change lands here.

## Non-goals

- The opening order (there is none).
- Partial-fill reconciliation beyond the remainder sizing — `refresh_position`
  still converges the size on the next tick.
- A periodic orphan sweep (`OpenOrders` unfiltered, matched against known ids) to
  catch orders left by a killed process. Worth doing later; different mechanism,
  different trigger, and its lookup would need its own bounding strategy since the
  "always among the newest" guarantee does not transfer.
- Making `pos["volume"]` match the submitted order by construction. §7 sizes the
  remainder from the exchange's numbers, which removes the drift from *this* path;
  the general fix is the *Exchange-Synchronized Order Amounts* backlog card.
- Any change to `record_position_closed`, PnL, or the meaning of `closing_price`
  as an estimate until the fill is confirmed.
- Migrating away from `krakenex`, or adding a retry/backoff layer.

## Residual risk

- **Killed process mid-placement.** §3: the id is durable only from the end of the
  pair block. Unchanged from today's exposure for `closing_order_id`, and the
  orphan sweep above is the eventual fix.
- **Resolution stuck for a long outage.** While the lookup keeps failing, the
  position is frozen and unmanaged — its trailing stop does not move. A deliberate
  trade (freezing is safer than possibly double-exiting), visible through the
  per-pair failure alert, and it ends as soon as Kraken answers.
- **A confirmed order that stops resolving is still frozen forever.** §1 keeps the
  latch spec's rule intact, and nothing here recovers such a pair automatically.
  The alert is the mitigation; an operator check at Kraken is the resolution.
- **Up to two extra private calls** on the unconfirmed path, inside a session
  whose duration is already alarmed on (`SLEEPING_INTERVAL` overrun). Rare enough
  not to move the needle, and the reprice path gives one call back.

## Resolved questions

Settled with the operator against the API docs on 2026-08-05, re-verified
2026-08-16, and extended 2026-08-17. Kept because each one shaped a decision
above.

1. **Pagination on the `ClosedOrders` leg is a non-issue** — but not for the
   obvious reason. Closed orders accumulate quickly (a chasing exit produces
   roughly one per tick). What makes the default 50-result page sufficient is
   *timing*: the resolver runs on the tick immediately after the lost response, so
   the order is always among the newest. That guarantee does **not** transfer to
   any lookup running long after placement — an orphan sweep would need its own
   bounding strategy.
2. **`cl_ord_id` is returned on the order objects** the bot reads back, which is
   what makes the echoed-id verification in §9 possible, and what would let a
   future orphan sweep prove an unknown open order is ours.
3. **`ClosedOrders` costs +1 on the REST call counter, not +4.** An earlier draft
   claimed +4, a number transplanted from the trading limiter (question 4). Only
   ledger/trade-history calls cost more than +1. The unconfirmed path therefore
   costs +2 at worst, which reinforces the decision not to gate the second leg:
   exhausting the counter already degrades safely into "unknown" + a failed pair,
   and gating would add a state machine to avoid a handled failure. **The
   account's verification tier is still unconfirmed** — the ceiling and refill
   rate depend on it, so treat any budget here as provisional.
4. **`AddOrder`/`CancelOrder` bill a second, independent trading limiter** — the
   earlier draft said they cost 0, true only of the REST counter.
   `CancelOrder`'s penalty scales inversely with how long the order rested, so the
   reprice loop bills it on every chase. At `SLEEPING_INTERVAL = 60` the cost
   clears between ticks at any realistic pair count, so no action is needed. What
   matters is the direction: **shortening the interval raises the per-cancel
   penalty while shrinking the decay window, from both ends at once.** The trading
   limiter, not the REST counter, is what bounds `SLEEPING_INTERVAL` from below.
5. **`CancelOrder` accepts `cl_ord_id`** (operator, 2026-08-17). It does not
   change the design: the txid is in hand wherever a cancel happens, so §7 keeps
   using it and `cl_ord_id` stays confined to the one job only it can do.

## Design choices to record in CLAUDE.md

- **Every order the bot places carries a per-attempt `cl_ord_id`, so a lost
  `AddOrder` response is recoverable.** Why per attempt and not per position
  (Kraken's open-order uniqueness + cancel/replace), why `cl_ord_id` and not
  `userref` (32-bit, groups orders, mutually exclusive), and why resolution goes
  through `ClosedOrders`/`OpenOrders` rather than `QueryOrders` (which cannot look
  up an order by anything but its txid).
- **A closing position routes on whether the placement was *confirmed*, not on
  whether an order exists.** `closing_order_id` present means Kraken gave us the
  id, so "not found" is `UNMANAGED`; only `closing_request_id` present means the
  outcome is unknown, so "not found" is genuine evidence nothing landed. The same
  lookup answer means opposite things, and the wrong direction is a double sell —
  which is why the two ids are not collapsed into one.
- **`_drive_closing_order` is the only place that branches on `OrderStatus`**, and
  it returns `None` to mean "the order is gone, place a new one on this tick".
  `finalize_close` interprets a terminal order and `reprice_closing_order` chases
  an open one; neither queries Kraken or re-derives what the selector established.
- **The reprice remainder is sized from the order's `vol - vol_exec`, not from
  `pos["volume"]`** — the two differ by up to one lot tick because submission
  rounds, and that drift used to turn a completed trade into an unplaceable dust
  order.
- **`SLEEPING_INTERVAL` is bounded from below by Kraken's trading rate limiter,
  not by the REST call counter** — the reprice loop cancels and replaces once per
  tick, and cancelling a short-lived order costs more (*Resolved questions* 4).
  Fine at 60 s; check before ever lowering it.
