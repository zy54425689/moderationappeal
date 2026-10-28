# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
import datetime
from dataclasses import dataclass
from genlayer import *

S_FILED = 0          # appeal waiting for the jury
S_OVERTURNED = 1     # the ban was wrong: the user is owed compensation, committed
S_UPHELD = 2         # the ban was right: the stake is forfeited
S_SETTLED = 3        # paid out or slashed, exactly once
S_UNFUNDED = 4       # overturned, but the pool cannot cover it yet
S_WITHDRAWN = 5      # the appellant took the stake back

MIN_STAKE = 10 ** 16          # 0.01 GEN
MAX_STAKE = 10 * 10 ** 18     # 10 GEN
MIN_COMPENSATION = 10 ** 15   # 0.001 GEN
MAX_COMPENSATION = 100 * 10 ** 18
COMP_TO_STAKE_CAP = 100
JUDGE_TIMEOUT_SECS = 7 * 24 * 3600

@allow_storage
@dataclass
class Appeal:
    appellant: Address
    platform: str
    content_url: str
    ban_reason: str
    evidence_digest: str
    requested_compensation: u256
    stake: u256
    score: u256
    state: u8
    filed_at: u256
    verdict_reason: str
    confidence: str
    stake_settled: bool

@gl.evm.contract_interface
class _Payee:
    class View:
        pass

    class Write:
        pass

class ModerationAppeal(gl.Contract):

    appeals: TreeMap[u256, Appeal]
    next_id: u256
    compensation_pool: u256
    slash_pool: u256
    committed: u256
    claims: TreeMap[str, u256]
    admin: Address

    def __init__(self):
        self.next_id = u256(1)
        self.compensation_pool = u256(0)
        self.slash_pool = u256(0)
        self.committed = u256(0)
        self.admin = gl.message.sender_address

    def _to_addr(self, value) -> Address:
        # Public write functions must take `str`: an `Address`-typed parameter is rejected on the
        # real network even though the direct VM accepts it. Parse defensively here instead.
        try:
            if isinstance(value, Address):
                return value
        except Exception:
            pass
        if isinstance(value, (bytes, bytearray)):
            return Address("0x" + bytes(value).hex())
        return Address(value)

    def _addr_bytes(self, value) -> bytes:
        b = getattr(value, "as_bytes", None)
        if b is None:
            b = getattr(value, "_as_bytes", None)
        if callable(b):
            b = b()
        if isinstance(b, (bytes, bytearray)):
            return bytes(b)
        return bytes(self._to_addr(value))

    def _ap(self, appeal_id: u256):
        if appeal_id not in self.appeals:
            raise gl.vm.UserError("Appeal not found")
        return self.appeals[appeal_id]

    def _only_admin(self):
        if self._addr_bytes(gl.message.sender_address) != self._addr_bytes(self.admin):
            raise gl.vm.UserError("Only the platform admin can do this")

    def _only_appellant(self, a):
        if self._addr_bytes(gl.message.sender_address) != self._addr_bytes(a.appellant):
            raise gl.vm.UserError("Only the appellant can do this")

    def _now(self) -> int:
        return int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    def _claim_key(self, platform: str, content_url: str, ban_reason: str) -> str:
        return (platform.strip().lower() + "|" + content_url.strip().lower() + "|"
                + ban_reason.strip().lower())

    def _pay(self, to: Address, amount: u256):
        if amount <= u256(0):
            return
        _Payee(to).emit_transfer(value=u256(amount), on='finalized')

    def _uncommitted(self) -> int:
        return int(self.compensation_pool) - int(self.committed)

    @gl.public.view
    def get_pools(self) -> dict:
        return {
            "admin": "0x" + self._addr_bytes(self.admin).hex(),
            "compensation_pool": self.compensation_pool,
            "slash_pool": self.slash_pool,
            "committed": self.committed,
            "withdrawable": u256(self._uncommitted() if self._uncommitted() > 0 else 0),
            "contract_balance": self.balance,
            "appeal_count": self.next_id - u256(1),
        }

    @gl.public.view
    def get_appeal(self, appeal_id: u256) -> dict:
        a = self.appeals[appeal_id]
        return {
            "id": appeal_id,
            "appellant": "0x" + self._addr_bytes(a.appellant).hex(),
            "platform": a.platform,
            "content_url": a.content_url,
            "ban_reason": a.ban_reason,
            "evidence_digest": a.evidence_digest,
            "requested_compensation": a.requested_compensation,
            "stake": a.stake,
            "score": a.score,
            "state": a.state,
            "filed_at": a.filed_at,
            "verdict_reason": a.verdict_reason,
            "confidence": a.confidence,
            "stake_settled": a.stake_settled,
            "overdue": int(a.state) == S_FILED and self._now() - int(a.filed_at) >= JUDGE_TIMEOUT_SECS,
        }

    @gl.public.view
    def get_appeal_count(self) -> u256:
        return self.next_id - u256(1)

    @gl.public.view
    def get_contract_balance(self) -> u256:
        return self.balance

    @gl.public.view
    def get_claim(self, platform: str, content_url: str, ban_reason: str) -> u256:
        key = self._claim_key(platform, content_url, ban_reason)
        if key not in self.claims:
            return u256(0)
        return self.claims[key]

    @gl.public.view
    def is_overdue(self, appeal_id: u256) -> bool:
        a = self._ap(appeal_id)
        return int(a.state) == S_FILED and self._now() - int(a.filed_at) >= JUDGE_TIMEOUT_SECS

    @gl.public.write.payable
    def fund_compensation_pool(self):
        value = gl.message.value
        if value <= u256(0):
            raise gl.vm.UserError("Send GEN with the call")
        self.compensation_pool = self.compensation_pool + value

    @gl.public.write.payable
    def file_appeal(
        self,
        platform: str,
        content_url: str,
        ban_reason: str,
        evidence_digest: str,
        requested_compensation: u256,
    ) -> u256:
        stake = gl.message.value

        if int(stake) < MIN_STAKE:
            raise gl.vm.UserError("Stake is below the minimum for an appeal")
        if int(stake) > MAX_STAKE:
            raise gl.vm.UserError("Stake is above the maximum for an appeal")

        if len(platform.strip()) == 0:
            raise gl.vm.UserError("A platform id is required")
        if len(content_url.strip()) == 0 or not (
                content_url.startswith("http://") or content_url.startswith("https://")):
            raise gl.vm.UserError("A valid content URL is required")
        if len(ban_reason.strip()) == 0:
            raise gl.vm.UserError("The stated ban reason is required")
        if len(evidence_digest.strip()) < 16:
            raise gl.vm.UserError("An evidence digest of the decision is required")

        if int(requested_compensation) < MIN_COMPENSATION:
            raise gl.vm.UserError("Requested compensation is below the minimum")
        if int(requested_compensation) > MAX_COMPENSATION:
            raise gl.vm.UserError("Requested compensation is above the maximum")
        if int(requested_compensation) > int(stake) * COMP_TO_STAKE_CAP:
            raise gl.vm.UserError("Compensation exceeds the allowed multiple of the stake")

        key = self._claim_key(platform, content_url, ban_reason)
        if key in self.claims:
            prev_id = int(self.claims[key])
            if prev_id != 0:
                prev = self.appeals[self.claims[key]]
                if int(prev.state) != S_UPHELD:
                    raise gl.vm.UserError("This decision is already on appeal")

        a = Appeal(
            appellant=self._to_addr(gl.message.sender_address),
            platform=platform.strip(),
            content_url=content_url.strip(),
            ban_reason=ban_reason.strip(),
            evidence_digest=evidence_digest.strip(),
            requested_compensation=requested_compensation,
            stake=stake,
            score=u256(0),
            state=u8(S_FILED),
            filed_at=u256(self._now()),
            verdict_reason="",
            confidence="",
            stake_settled=False,
        )
        aid = self.next_id
        self.appeals[aid] = a
        self.next_id = aid + u256(1)
        self.claims[key] = aid
        return aid

    @gl.public.write
    def judge_appeal(self, appeal_id: u256):
        a = self._ap(appeal_id)
        if int(a.state) != S_FILED:
            raise gl.vm.UserError("Appeal has already been judged")

        platform_mem = str(a.platform)
        url_mem = str(a.content_url)
        reason_mem = str(a.ban_reason)
        digest_mem = str(a.evidence_digest)

        def _fetch(url: str, limit: int = 1400) -> str:
            try:
                resp = gl.nondet.web.get(url)
                if isinstance(resp, bytes):
                    raw = resp.decode("utf-8", errors="ignore")
                elif hasattr(resp, "body"):
                    raw = resp.body.decode("utf-8", errors="ignore")
                else:
                    raw = str(resp)
                return " ".join(raw.split())[:limit]
            except Exception:
                return "[could not fetch this content]"

        def leader_fn():
            page = _fetch(url_mem)
            prompt = (
                "You are reviewing a moderation appeal.\n\n"
                "PLATFORM: " + platform_mem + "\n"
                "THE PLATFORM SAYS THIS CONTENT WAS BANNED FOR: " + reason_mem + "\n"
                "EVIDENCE DIGEST SUPPLIED BY THE APPELLANT: " + digest_mem + "\n"
                "CONTENT URL: " + url_mem + "\n\n"
                "CONTENT FETCHED RIGHT NOW:\n" + page + "\n\n"
                "Decide only from the fetched content above. Overturn the ban when the content does "
                "NOT actually match the stated reason - for example the page is ordinary discussion "
                "of the very topic it is accused of, the reason refers to material that is not on "
                "the page, or the page is unreachable while the appellant's digest documents it. "
                "Uphold the ban when the content does match the stated reason, when it is spam, "
                "harassment or clearly unlawful, or when the page offers nothing that could be "
                "checked at all.\n\n"
                "Do NOT consider the compensation pool balance - whether the pool can pay is "
                "handled by the contract, and an overturned ban may legitimately end up UNFUNDED.\n\n"
                'Respond as JSON with exactly these keys:\n'
                '- "unjustified": true if the ban should be overturned, false if upheld\n'
                '- "score": integer 0-100 confidence that the ruling is correct\n'
                '- "reason": concise 2-4 sentence justification citing the fetched content\n'
                '- "confidence": one of "high", "medium", "low"'
            )
            return gl.nondet.exec_prompt(prompt, response_format='json')

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            payload = leaders_res.calldata
            if not isinstance(payload, dict):
                return False
            for k in ("unjustified", "score", "reason", "confidence"):
                if k not in payload:
                    return False
            u = payload.get("unjustified")
            if isinstance(u, str):
                u = u.strip().lower() == "true"
            if not isinstance(u, bool):
                return False
            try:
                sc = int(payload.get("score"))
            except Exception:
                return False
            if sc < 0 or sc > 100:
                return False
            if str(payload.get("confidence")).strip().lower() not in ("high", "medium", "low"):
                return False
            return True

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        unjust = result.get("unjustified", False)
        if isinstance(unjust, str):
            unjust = unjust.strip().lower() == "true"

        score = result.get("score", 0)
        try:
            score_val = int(score)
        except Exception:
            score_val = 0
        if score_val < 0:
            score_val = 0
        if score_val > 100:
            score_val = 100

        a.score = u256(score_val)
        a.verdict_reason = str(result.get("reason", ""))
        a.confidence = str(result.get("confidence", ""))

        if not bool(unjust):
            a.state = u8(S_UPHELD)
            return

        if self._uncommitted() >= int(a.requested_compensation):
            a.state = u8(S_OVERTURNED)
            self.committed = self.committed + a.requested_compensation
        else:
            a.state = u8(S_UNFUNDED)

    @gl.public.write
    def settle_appeal(self, appeal_id: u256):
        a = self._ap(appeal_id)
        st = int(a.state)
        if st == S_FILED:
            raise gl.vm.UserError("Appeal has not been judged yet")
        if st == S_SETTLED:
            raise gl.vm.UserError("Appeal already settled")
        if st == S_WITHDRAWN:
            raise gl.vm.UserError("Stake was already withdrawn")
        if a.stake_settled:
            raise gl.vm.UserError("Stake already settled")

        if st == S_OVERTURNED:
            if int(a.requested_compensation) > int(self.compensation_pool):
                raise gl.vm.UserError("The pool does not cover this compensation")
            a.stake_settled = True
            self.compensation_pool = self.compensation_pool - a.requested_compensation
            self.committed = self.committed - a.requested_compensation
            a.state = u8(S_SETTLED)
            self._pay(a.appellant, a.requested_compensation)
            self._pay(a.appellant, a.stake)
            return

        if st == S_UNFUNDED:
            raise gl.vm.UserError("Appeal is unfunded: wait for a top-up and activate it, or reclaim the stake")

        a.stake_settled = True
        self.slash_pool = self.slash_pool + a.stake
        a.state = u8(S_SETTLED)

    @gl.public.write
    def activate_unfunded(self, appeal_id: u256):
        a = self._ap(appeal_id)
        if int(a.state) != S_UNFUNDED:
            raise gl.vm.UserError("Appeal is not unfunded")
        if self._uncommitted() < int(a.requested_compensation):
            raise gl.vm.UserError("The pool still cannot cover this compensation")
        a.state = u8(S_OVERTURNED)
        self.committed = self.committed + a.requested_compensation

    @gl.public.write
    def reclaim_stake(self, appeal_id: u256):
        a = self._ap(appeal_id)
        self._only_appellant(a)
        if a.stake_settled:
            raise gl.vm.UserError("Stake already settled")

        st = int(a.state)
        if st == S_UNFUNDED:
            pass
        elif st == S_FILED:
            if self._now() - int(a.filed_at) < JUDGE_TIMEOUT_SECS:
                raise gl.vm.UserError("Appeal is still within the judging window")
        else:
            raise gl.vm.UserError("This appeal cannot be reclaimed in its current state")

        a.stake_settled = True
        a.state = u8(S_WITHDRAWN)
        key = self._claim_key(str(a.platform), str(a.content_url), str(a.ban_reason))
        if key in self.claims:
            self.claims[key] = u256(0)
        self._pay(a.appellant, a.stake)

    @gl.public.write
    def withdraw_pool(self, amount: u256, to: str):
        self._only_admin()
        if amount <= u256(0):
            raise gl.vm.UserError("Amount must be positive")

        available = int(self.slash_pool) + self._uncommitted()
        if int(amount) > available:
            raise gl.vm.UserError("Amount exceeds the uncommitted pool")

        if int(amount) <= int(self.slash_pool):
            self.slash_pool = self.slash_pool - amount
        else:
            rest = amount - self.slash_pool
            self.slash_pool = u256(0)
            if int(rest) > self._uncommitted():
                raise gl.vm.UserError("Amount exceeds the uncommitted pool")
            self.compensation_pool = self.compensation_pool - rest

        self._pay(self._to_addr(to), amount)
