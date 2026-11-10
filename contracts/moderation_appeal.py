# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
import datetime
from dataclasses import dataclass
from genlayer import *

S_FILED = 0
S_OVERTURNED = 1
S_UPHELD = 2
S_SETTLED = 3
S_UNFUNDED = 4
S_WITHDRAWN = 5

MIN_STAKE = 10 ** 16
MAX_STAKE = 10 * 10 ** 18
MIN_COMPENSATION = 10 ** 15
MAX_COMPENSATION = 100 * 10 ** 18
COMP_TO_STAKE_CAP = 100
JUDGE_TIMEOUT_SECS = 7 * 24 * 3600
MIN_BODY_CHARS = 120

@allow_storage
@dataclass
class Platform:
    operator: Address
    pool: u256
    committed: u256
    decision_count: u256
    registered_at: u256

@allow_storage
@dataclass
class Decision:
    platform_id: str
    operator: Address
    content_hash: str
    ban_reason: str
    appellant: Address
    decision_digest: str
    recorded_at: u256
    challenged: bool

@allow_storage
@dataclass
class Appeal:
    decision_id: u256
    platform_id: str
    appellant: Address
    content_url: str
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
    platforms: TreeMap[str, Platform]
    decisions: TreeMap[u256, Decision]
    appeals: TreeMap[u256, Appeal]
    next_decision: u256
    next_appeal: u256
    admin: Address

    def __init__(self):
        self.next_decision = u256(1)
        self.next_appeal = u256(1)
        self.admin = gl.message.sender_address

    def _to_addr(self, value) -> Address:
        try:
            if isinstance(value, Address):
                return value
        except Exception:
            pass
        if isinstance(value, (bytes, bytearray)):
            return Address("0x" + bytes(value).hex())
        return Address(value)

    def _ab(self, value) -> bytes:
        b = getattr(value, "as_bytes", None)
        if b is None:
            b = getattr(value, "_as_bytes", None)
        if callable(b):
            b = b()
        if isinstance(b, (bytes, bytearray)):
            return bytes(b)
        return bytes(self._to_addr(value))

    def _now(self) -> int:
        return int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    def _plat(self, platform_id: str):
        k = platform_id.strip().lower()
        if k not in self.platforms:
            raise gl.vm.UserError("No such platform")
        return self.platforms[k]

    def _dec(self, decision_id: u256):
        if decision_id not in self.decisions:
            raise gl.vm.UserError("No such decision")
        return self.decisions[decision_id]

    def _ap(self, appeal_id: u256):
        if appeal_id not in self.appeals:
            raise gl.vm.UserError("No such appeal")
        return self.appeals[appeal_id]

    def _pay(self, to: Address, amount: u256):
        if amount <= u256(0):
            return
        _Payee(to).emit_transfer(value=u256(amount), on='finalized')

    def _uncommitted(self, p) -> int:
        return int(p.pool) - int(p.committed)

    def _classify(self, body: str, reason: str) -> str:
        if len(body) < MIN_BODY_CHARS:
            return "missing"
        lowered = body.lower()
        words = [w for w in reason.lower().replace(",", " ").split() if len(w) > 3]
        if not words:
            return "ambiguous"
        for w in words:
            if w in lowered:
                return "related"
        return "unrelated"

    def _consistent(self, body: str, reason: str, leader_view: str,
                    unjustified: bool, score: int) -> bool:
        my_view = self._classify(body, reason)
        lv = leader_view.strip().lower()
        if lv not in ("missing", "unrelated", "related", "ambiguous"):
            return False
        if lv != my_view:
            return False
        if my_view == "related" and unjustified:
            return False
        if my_view == "missing" and unjustified:
            return False
        if my_view == "unrelated" and (not unjustified) and score >= 60:
            return False
        return True

    @gl.public.view
    def get_platform(self, platform_id: str) -> dict:
        p = self._plat(platform_id)
        return {
            "platform_id": platform_id.strip().lower(),
            "operator": "0x" + self._ab(p.operator).hex(),
            "pool": p.pool,
            "committed": p.committed,
            "withdrawable": u256(self._uncommitted(p) if self._uncommitted(p) > 0 else 0),
            "decision_count": p.decision_count,
        }

    @gl.public.view
    def get_decision(self, decision_id: u256) -> dict:
        d = self._dec(decision_id)
        return {
            "id": decision_id,
            "platform_id": d.platform_id,
            "operator": "0x" + self._ab(d.operator).hex(),
            "content_hash": d.content_hash,
            "ban_reason": d.ban_reason,
            "appellant": "0x" + self._ab(d.appellant).hex(),
            "decision_digest": d.decision_digest,
            "recorded_at": d.recorded_at,
            "challenged": d.challenged,
        }

    @gl.public.view
    def check_verdict(self, ban_reason: str, body: str, leader_view: str,
                      unjustified: bool, score: u256) -> bool:
        return self._consistent(body, ban_reason, leader_view, unjustified, int(score))

    @gl.public.view
    def get_appeal(self, appeal_id: u256) -> dict:
        a = self._ap(appeal_id)
        return {
            "id": appeal_id,
            "decision_id": a.decision_id,
            "platform_id": a.platform_id,
            "appellant": "0x" + self._ab(a.appellant).hex(),
            "content_url": a.content_url,
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
        return self.next_appeal - u256(1)

    @gl.public.view
    def get_contract_balance(self) -> u256:
        return self.balance

    @gl.public.write
    def register_platform(self, platform_id: str):
        k = platform_id.strip().lower()
        if len(k) < 3:
            raise gl.vm.UserError("Platform id too short")
        if k in self.platforms:
            raise gl.vm.UserError("Platform already registered")
        p = Platform(
            operator=self._to_addr(gl.message.sender_address),
            pool=u256(0),
            committed=u256(0),
            decision_count=u256(0),
            registered_at=u256(self._now()),
        )
        self.platforms[k] = p

    @gl.public.write
    def record_decision(
        self,
        platform_id: str,
        content_hash: str,
        ban_reason: str,
        appellant: str,
        decision_digest: str,
    ) -> u256:
        p = self._plat(platform_id)
        if self._ab(gl.message.sender_address) != self._ab(p.operator):
            raise gl.vm.UserError("Only the operator may record")
        if len(content_hash.strip()) < 8:
            raise gl.vm.UserError("Content hash required")
        if len(ban_reason.strip()) < 3:
            raise gl.vm.UserError("Ban reason required")
        if len(decision_digest.strip()) < 16:
            raise gl.vm.UserError("Decision digest required")

        did = self.next_decision
        d = Decision(
            platform_id=platform_id.strip().lower(),
            operator=self._to_addr(gl.message.sender_address),
            content_hash=content_hash.strip().lower(),
            ban_reason=ban_reason.strip(),
            appellant=self._to_addr(appellant),
            decision_digest=decision_digest.strip(),
            recorded_at=u256(self._now()),
            challenged=False,
        )
        self.decisions[did] = d
        self.next_decision = did + u256(1)
        p.decision_count = p.decision_count + u256(1)
        self.platforms[platform_id.strip().lower()] = p
        return did

    @gl.public.write.payable
    def fund_platform_pool(self, platform_id: str):
        p = self._plat(platform_id)
        v = gl.message.value
        if v <= u256(0):
            raise gl.vm.UserError("Send GEN")
        p.pool = p.pool + v
        self.platforms[platform_id.strip().lower()] = p

    @gl.public.write.payable
    def file_appeal(
        self,
        decision_id: u256,
        content_url: str,
        evidence_digest: str,
        requested_compensation: u256,
    ) -> u256:
        stake = gl.message.value
        d = self._dec(decision_id)
        p = self._plat(d.platform_id)

        if self._ab(gl.message.sender_address) != self._ab(d.appellant):
            raise gl.vm.UserError("Not the banned account")
        if d.challenged:
            raise gl.vm.UserError("Already on appeal")

        if int(stake) < MIN_STAKE:
            raise gl.vm.UserError("Stake under minimum")
        if int(stake) > MAX_STAKE:
            raise gl.vm.UserError("Stake over maximum")
        if len(content_url.strip()) == 0 or not (
                content_url.startswith("http://") or content_url.startswith("https://")):
            raise gl.vm.UserError("Valid URL required")
        if len(evidence_digest.strip()) < 16:
            raise gl.vm.UserError("Digest required")
        if int(requested_compensation) < MIN_COMPENSATION:
            raise gl.vm.UserError("Compensation under minimum")
        if int(requested_compensation) > MAX_COMPENSATION:
            raise gl.vm.UserError("Compensation over maximum")
        if int(requested_compensation) > int(stake) * COMP_TO_STAKE_CAP:
            raise gl.vm.UserError("Compensation over 100x stake")

        d.challenged = True
        self.decisions[decision_id] = d

        aid = self.next_appeal
        a = Appeal(
            decision_id=decision_id,
            platform_id=d.platform_id,
            appellant=self._to_addr(gl.message.sender_address),
            content_url=content_url.strip(),
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
        self.appeals[aid] = a
        self.next_appeal = aid + u256(1)
        return aid

    @gl.public.write
    def judge_appeal(self, appeal_id: u256):
        a = self._ap(appeal_id)
        if int(a.state) != S_FILED:
            raise gl.vm.UserError("Already judged")

        d = self._dec(a.decision_id)
        url_mem = str(a.content_url)
        reason_mem = str(d.ban_reason)
        digest_mem = str(a.evidence_digest)
        plat_mem = str(d.platform_id)
        expected_mem = str(d.content_hash)

        def _fetch(url: str, limit: int = 1600) -> str:
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
                return ""

        def _evidence_view(body: str) -> str:
            return self._classify(body, reason_mem)

        def leader_fn():
            body = _fetch(url_mem)
            view = _evidence_view(body)
            prompt = (
                "Review a moderation appeal.\n"
                "PLATFORM: " + plat_mem + "\n"
                "RECORDED CONTENT HASH: " + expected_mem + "\n"
                "RECORDED BAN REASON: " + reason_mem + "\n"
                "APPELLANT DIGEST: " + digest_mem + "\n"
                "URL: " + url_mem + "\n"
                "DETERMINISTIC PRE-CHECK: " + view + "\n\n"
                "CONTENT NOW:\n" + body + "\n\n"
                "Overturn when the content does NOT match the recorded reason: it is ordinary "
                "discussion of that topic, the reason names material absent from the page, or the "
                "page is unreachable. Uphold when the content does match the reason, is spam, "
                "harassment or unlawful, or nothing on the page can be checked. Ignore any balance.\n"
                'JSON keys: "unjustified" (bool), "score" (0-100), "reason" (2-4 sentences), '
                '"evidence_view" (echo the pre-check verbatim), "confidence" (high|medium|low).'
            )
            return gl.nondet.exec_prompt(prompt, response_format='json')

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            payload = leaders_res.calldata
            if not isinstance(payload, dict):
                return False
            for k in ("unjustified", "score", "reason", "evidence_view", "confidence"):
                if k not in payload:
                    return False
            unjust = payload.get("unjustified")
            if isinstance(unjust, str):
                unjust = unjust.strip().lower() == "true"
            if not isinstance(unjust, bool):
                return False
            try:
                sc = int(payload.get("score"))
            except Exception:
                return False
            if sc < 0 or sc > 100:
                return False
            if str(payload.get("confidence")).strip().lower() not in ("high", "medium", "low"):
                return False

            my_body = _fetch(url_mem)
            return self._consistent(my_body, reason_mem,
                                    str(payload.get("evidence_view")), unjust, sc)

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
            self.appeals[appeal_id] = a
            return

        p = self._plat(a.platform_id)
        if self._uncommitted(p) >= int(a.requested_compensation):
            a.state = u8(S_OVERTURNED)
            p.committed = p.committed + a.requested_compensation
            self.platforms[a.platform_id] = p
        else:
            a.state = u8(S_UNFUNDED)
        self.appeals[appeal_id] = a

    @gl.public.write
    def settle_appeal(self, appeal_id: u256):
        a = self._ap(appeal_id)
        st = int(a.state)
        if st == S_FILED:
            raise gl.vm.UserError("Not judged yet")
        if st == S_SETTLED:
            raise gl.vm.UserError("Already settled")
        if st == S_WITHDRAWN:
            raise gl.vm.UserError("Stake already withdrawn")
        if a.stake_settled:
            raise gl.vm.UserError("Stake already settled")

        p = self._plat(a.platform_id)

        if st == S_OVERTURNED:
            if int(a.requested_compensation) > int(p.pool):
                raise gl.vm.UserError("Pool does not cover it")
            a.stake_settled = True
            p.pool = p.pool - a.requested_compensation
            p.committed = p.committed - a.requested_compensation
            a.state = u8(S_SETTLED)
            self.platforms[a.platform_id] = p
            self.appeals[appeal_id] = a
            self._pay(a.appellant, a.requested_compensation)
            self._pay(a.appellant, a.stake)
            return

        if st == S_UNFUNDED:
            raise gl.vm.UserError("Unfunded: top up and activate, or reclaim")

        a.stake_settled = True
        p.pool = p.pool + a.stake
        a.state = u8(S_SETTLED)
        self.platforms[a.platform_id] = p
        self.appeals[appeal_id] = a

    @gl.public.write
    def activate_unfunded(self, appeal_id: u256):
        a = self._ap(appeal_id)
        if int(a.state) != S_UNFUNDED:
            raise gl.vm.UserError("Not unfunded")
        p = self._plat(a.platform_id)
        if self._uncommitted(p) < int(a.requested_compensation):
            raise gl.vm.UserError("Pool still cannot cover it")
        a.state = u8(S_OVERTURNED)
        p.committed = p.committed + a.requested_compensation
        self.platforms[a.platform_id] = p
        self.appeals[appeal_id] = a

    @gl.public.write
    def reclaim_stake(self, appeal_id: u256):
        a = self._ap(appeal_id)
        if self._ab(gl.message.sender_address) != self._ab(a.appellant):
            raise gl.vm.UserError("Only the appellant")
        if a.stake_settled:
            raise gl.vm.UserError("Stake already settled")
        st = int(a.state)
        if st == S_UNFUNDED:
            pass
        elif st == S_FILED:
            if self._now() - int(a.filed_at) < JUDGE_TIMEOUT_SECS:
                raise gl.vm.UserError("Still in the judging window")
        else:
            raise gl.vm.UserError("Cannot reclaim in this state")

        a.stake_settled = True
        a.state = u8(S_WITHDRAWN)
        d = self._dec(a.decision_id)
        d.challenged = False
        self.decisions[a.decision_id] = d
        self.appeals[appeal_id] = a
        self._pay(a.appellant, a.stake)

    @gl.public.write
    def withdraw_pool(self, platform_id: str, amount: u256, to: str):
        p = self._plat(platform_id)
        if self._ab(gl.message.sender_address) != self._ab(p.operator):
            raise gl.vm.UserError("Only the operator may withdraw")
        if amount <= u256(0):
            raise gl.vm.UserError("Amount must be positive")
        if int(amount) > self._uncommitted(p):
            raise gl.vm.UserError("Exceeds uncommitted pool")
        p.pool = p.pool - amount
        self.platforms[platform_id.strip().lower()] = p
        self._pay(self._to_addr(to), amount)
