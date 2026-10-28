# ModerationAppeal

A ban is a decision with no appeal. This gives the appeal a verdict.

Platforms moderate at scale: content gets removed, accounts get restricted, and the reason a user is
shown is often a category label rather than an argument. If the label is wrong, the only recourse is
a support ticket handled by the same team that made the call. There is no record, no reasoning and
no consequence for getting it wrong.

ModerationAppeal turns that into a decision with an outcome behind it. The platform funds a
compensation pool. A banned user files an appeal that is **bound to the platform, the content URL and
the stated ban reason**, with a stake behind it. An on-chain jury fetches the content and rules
whether the stated reason actually matches what is on the page. A wrong ban pays compensation from
the pool and returns the stake; a justified ban forfeits the stake into the pool, so filing hopeless
appeals costs money.

---

## Why this needs GenLayer

Judging an appeal is not a lookup. It needs the actual content, read the same way a human moderator
would read it, and compared against a written reason. That is a subjective judgement over
unstructured text — something a deterministic smart contract cannot do and a plain oracle cannot be
trusted to do alone.

GenLayer supplies exactly the missing piece: an LLM jury that fetches the page itself
(`gl.nondet.web.get`) and reasons over it (`gl.nondet.exec_prompt`) under optimistic-democracy
consensus, with a validator that checks the shape and internal consistency of the verdict. The money
stays deterministic: custody, commitments, payouts and refunds are all plain contract state.

---

## Contract

Bradbury testnet: `0x0000000000000000000000000000000000000000`

### Money flow

| Step | Call | What moves |
|---|---|---|
| 1 | `fund_compensation_pool()` — **payable** | Platform → custody, credited to `compensation_pool` |
| 2 | `file_appeal(platform, content_url, ban_reason, evidence_digest, requested_compensation)` — **payable** | Appellant → custody, booked as the appeal's `stake`; the decision is registered against `(platform, url, reason)` |
| 3 | `judge_appeal(id)` | No funds move. The jury fetches the content and returns `{unjustified, score, reason, confidence}`. An overturn with pool cover is booked into `committed`; without cover it becomes `UNFUNDED` |
| 4a | `settle_appeal(id)` — OVERTURNED | `compensation_pool -= compensation`, `committed -= compensation`; the compensation **and** the stake are paid to the appellant via `emit_transfer(..., on='finalized')` |
| 4b | `settle_appeal(id)` — UPHELD | The stake is forfeited: `slash_pool += stake` |
| 4c | `activate_unfunded(id)` | Permissionless. An `UNFUNDED` overturn becomes `OVERTURNED` once `compensation_pool - committed` covers it |
| 4d | `reclaim_stake(id)` — appellant only | Returns the stake and voids the claim. Allowed while `UNFUNDED`, or after an unjudged appeal has aged past the 7-day window |
| 5 | `withdraw_pool(amount, to)` — admin | Forfeited stakes and **only the uncommitted** pool leave the contract |

### Guarantees the contract enforces

**The appeal is scoped**

- A blank `platform`, a non-http(s) `content_url`, a blank `ban_reason` or a digest shorter than 16
  characters each revert. The finding cannot be filed against nothing.

**One appeal per decision**

- The claim key is `platform | content_url | ban_reason` and is unique. Filing it again reverts with
  `This decision is already on appeal`.
- An **upheld** ban frees the key, because it was never paid out — so a user may re-appeal the same
  decision with better evidence once the first attempt has been judged and lost.
- Reclaiming a stake voids the claim, so a refunded decision can never be paid afterwards.

**Bounded amounts**

- `0.01 GEN <= stake <= 10 GEN`
- `0.001 GEN <= compensation <= 100 GEN`
- `compensation <= 100 x stake`, so a claim cannot be inflated far beyond the money behind it.

**Overturned bans are protected**

- A jury overturn is immediately recorded in `committed`.
- `withdraw_pool` can only reach `slash_pool + (compensation_pool - committed)`, so compensation the
  platform already owes cannot be withdrawn (`Amount exceeds the uncommitted pool`).
- `get_pools()` exposes `committed` and `withdrawable` so this is checkable from outside.

**Nobody gets stuck**

- An overturn the pool cannot cover becomes `UNFUNDED`, never a silent loss.
- The appellant always recovers the stake: on demand while `UNFUNDED`, or after the 7-day judging
  window on an unjudged appeal.
- A topped-up pool promotes an `UNFUNDED` appeal back to payable, by anyone.

**General**

- An appeal can only be judged once, and only settled after it is judged.
- A stake settles exactly once — `settle_appeal` reverts on a second call.
- The jury's score is clamped to 0–100 before it is stored.

---

## Repository layout

```
contracts/moderation_appeal.py           the Intelligent Contract
tests/direct/test_moderation_appeal.py   38 direct-mode tests
index.html                               web UI (no build step, opens straight in a browser)
genlayer.config.yaml                     network configuration (Bradbury default)
ruff.toml                                lint configuration
```

---

## Run the tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install "genlayer-test>=0.29,<0.30" pytest ruff
pytest tests/direct/ -v
ruff check .
```

All 38 tests run in direct mode, which needs no network.

---

## Deploy

```bash
genlayer deploy --contract contracts/moderation_appeal.py
```

---

## Use it

```bash
# 1. the platform funds the compensation pool (payable)
genlayer write <CONTRACT> fund_compensation_pool --value 0.5

# 2. a banned user files a scoped appeal with a stake (payable)
genlayer write <CONTRACT> file_appeal \
  --args "chirper" "https://example.com/post" "harassment" "<64-char digest>" 0.3 --value 0.1

# 3. the jury reads the content on-chain
genlayer write <CONTRACT> judge_appeal --args 1

# 4. settle: compensate (overturned) or forfeit the stake (upheld)
genlayer write <CONTRACT> settle_appeal --args 1

# 5. if the pool could not pay, top up and activate
genlayer write <CONTRACT> activate_unfunded --args 1

# 6. the appellant recovers the stake when unfunded, or after the 7-day window
genlayer write <CONTRACT> reclaim_stake --args 1

# 7. the platform withdraws only the uncommitted pool
genlayer write <CONTRACT> withdraw_pool --args 0.1 <recipient>
```

---

## Verified on Bradbury

PENDING_VERIFICATION

### Browser wallet path

The web UI is not a mock: a browser wallet signs the transaction and the contract state changes.

PENDING_WALLET_TX

The signing path in `index.html`:

- `createClient` is **not** given `window.ethereum` as the account. The connected address is supplied
  per call as a JSON-RPC account object `{address, type:"json-rpc"}`, and the signing provider is
  wrapped so the SDK cannot fall back to `window.ethereum`. With more than one extension installed
  that fallback answers `4100 unauthorised` and the wallet never signs at all.
- The provider probe prefers OKX (`window.okxwallet`) and then MetaMask / Rabby / any injected
  provider, so the wallet the user actually connected with is the one that signs.
- `wallet_switchEthereumChain` runs before every write. `4902` triggers `wallet_addEthereumChain`, and
  `4001` / `-32002` / `4100` each map to an explicit message instead of a silent failure.
  `eth_chainId` is compared against `4221` before anything is signed.
- A transaction hash is never treated as success: the receipt is awaited to `ACCEPTED` / `FINALIZED`
  and the execution result is checked first.

---

## License

MIT
