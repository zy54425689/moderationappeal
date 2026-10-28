# -*- coding: utf-8 -*-
"""ModerationAppeal direct-mode tests — moderation appeals ruled on-chain.

Run: pytest tests/direct/ -v

The direct VM does not model the contract's own GEN balance, so these tests assert the contract's
internal accounting (compensation pool / committed / stakes). Real GEN movement is verified
end-to-end on Bradbury.

Amount unit: B = 0.001 GEN. Stake bounds are 0.01 .. 10 GEN, so a normal appeal stakes 100*B.
"""

B = 1000000000000000        # 0.001 GEN
STAKE = 100 * B             # 0.1 GEN
COMP = 400 * B              # 0.4 GEN
POOL = 1000 * B             # 1 GEN
DIGEST = "9f2c1a7be0d34f568192ab34cd56ef7890ab12cd34ef56789012ab34cd56ef78"


def _deploy(direct_vm, direct_deploy, admin):
    import pathlib
    import tempfile

    src = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "moderation_appeal.py"
    code = src.read_text(encoding="utf-8")
    tmp = pathlib.Path(tempfile.gettempdir()) / "moderation_appeal_local.py"
    tmp.write_text(code, encoding="utf-8")
    direct_vm.sender = admin
    return direct_deploy(str(tmp))


def _addr(v):
    from genlayer import Address
    if isinstance(v, (bytes, bytearray)):
        return Address("0x" + bytes(v).hex())
    if isinstance(v, str) and not v.startswith("0x"):
        return Address("0x" + v)
    return Address(v)


def _fund(direct_vm, c, who, amount):
    direct_vm.sender = who
    direct_vm.value = amount
    c.fund_compensation_pool()
    direct_vm.value = 0


def _appeal(direct_vm, c, who, platform="chirper", url="https://example.com/post",
            reason="harassment", comp=COMP, stake=STAKE, digest=DIGEST):
    direct_vm.sender = who
    direct_vm.value = stake
    aid = c.file_appeal(platform, url, reason, digest, comp)
    direct_vm.value = 0
    return aid


def _mock_unjustified(direct_vm, score=90):
    direct_vm.mock_web(".*", {"status": 200, "body": "x" * 300})
    direct_vm.mock_llm(".*", {"unjustified": True, "score": score,
                              "reason": "the page does not contain the material the reason names",
                              "confidence": "high"})


def _mock_upheld(direct_vm, score=85):
    direct_vm.mock_web(".*", {"status": 200, "body": "x" * 300})
    direct_vm.mock_llm(".*", {"unjustified": False, "score": score,
                              "reason": "the page matches the stated reason",
                              "confidence": "high"})


# --------------------------------------------------- 1. pool funding ----

def test_fund_pool_requires_value(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    with direct_vm.expect_revert("Send GEN with the call"):
        c.fund_compensation_pool()


def test_pool_tracks_funding(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    _fund(direct_vm, c, direct_bob, POOL // 2)
    p = c.get_pools()
    assert int(p["compensation_pool"]) == POOL + POOL // 2
    assert int(p["slash_pool"]) == 0
    assert int(p["committed"]) == 0


# ---------------------------------------- 2. the appeal must be scoped ----

def test_platform_is_required(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("platform id is required"):
        c.file_appeal("  ", "https://example.com/p", "harassment", DIGEST, COMP)
    direct_vm.value = 0


def test_content_url_must_be_valid(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("valid content URL"):
        c.file_appeal("chirper", "not-a-url", "harassment", DIGEST, COMP)
    direct_vm.value = 0


def test_ban_reason_is_required(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("ban reason is required"):
        c.file_appeal("chirper", "https://example.com/p", "   ", DIGEST, COMP)
    direct_vm.value = 0


def test_evidence_digest_is_required(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("evidence digest"):
        c.file_appeal("chirper", "https://example.com/p", "harassment", "short", COMP)
    direct_vm.value = 0


def test_appeal_records_its_scope(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    aid = _appeal(direct_vm, c, direct_bob, platform="chirper", url="https://example.com/p",
                  reason="harassment")
    a = c.get_appeal(aid)
    assert a["platform"] == "chirper"
    assert a["content_url"] == "https://example.com/p"
    assert a["ban_reason"] == "harassment"
    assert a["evidence_digest"] == DIGEST
    assert int(a["state"]) == 0
    assert int(a["filed_at"]) > 0


# --------------------------------------- 3. one appeal per decision ----

def test_same_decision_cannot_be_appealed_twice(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _appeal(direct_vm, c, direct_bob)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("already on appeal"):
        c.file_appeal("chirper", "https://example.com/post", "harassment", DIGEST, COMP)
    direct_vm.value = 0


def test_different_reason_is_a_different_claim(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _appeal(direct_vm, c, direct_bob, reason="harassment")
    aid2 = _appeal(direct_vm, c, direct_bob, reason="spam")
    assert int(aid2) == 2


def test_upheld_appeal_frees_the_claim(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_upheld(direct_vm)
    c.judge_appeal(aid)
    assert int(c.get_appeal(aid)["state"]) == 2          # UPHELD
    aid2 = _appeal(direct_vm, c, direct_bob, digest="b" * 64)
    assert int(aid2) == 2


def test_claim_lookup_exposes_the_binding(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    assert int(c.get_claim("chirper", "https://example.com/post", "harassment")) == 0
    aid = _appeal(direct_vm, c, direct_bob)
    assert int(c.get_claim("chirper", "https://example.com/post", "harassment")) == int(aid)


# ------------------------------------------------ 4. bounded amounts ----

def test_stake_below_minimum_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = B                    # 0.001 GEN, under the 0.01 floor
    with direct_vm.expect_revert("below the minimum"):
        c.file_appeal("chirper", "https://example.com/p", "harassment", DIGEST, COMP)
    direct_vm.value = 0


def test_stake_above_maximum_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = 11 * 10 ** 18        # 11 GEN, over the 10 GEN ceiling
    with direct_vm.expect_revert("above the maximum"):
        c.file_appeal("chirper", "https://example.com/p", "harassment", DIGEST, COMP)
    direct_vm.value = 0


def test_compensation_below_minimum_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("below the minimum"):
        c.file_appeal("chirper", "https://example.com/p", "harassment", DIGEST, 0)
    direct_vm.value = 0


def test_compensation_above_maximum_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("above the maximum"):
        c.file_appeal("chirper", "https://example.com/p", "harassment", DIGEST, 101 * 10 ** 18)
    direct_vm.value = 0


def test_compensation_capped_relative_to_stake(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE                # 0.1 GEN
    with direct_vm.expect_revert("allowed multiple of the stake"):
        c.file_appeal("chirper", "https://example.com/p", "harassment", DIGEST, 101 * STAKE)
    direct_vm.value = 0


# ------------------------------------------------------ 5. judging ----

def test_judge_requires_appeal(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    with direct_vm.expect_revert("Appeal not found"):
        c.judge_appeal(99)


def test_judge_is_one_shot(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    with direct_vm.expect_revert("already been judged"):
        c.judge_appeal(aid)


def test_unreachable_content_upholds_the_ban(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    aid = _appeal(direct_vm, c, direct_bob)
    direct_vm.mock_web(".*", {"status": 500, "body": ""})
    direct_vm.mock_llm(".*", {"unjustified": False, "score": 40,
                              "reason": "the page offers nothing that could be checked",
                              "confidence": "medium"})
    c.judge_appeal(aid)
    assert int(c.get_appeal(aid)["state"]) == 2


def test_score_is_clamped(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    aid = _appeal(direct_vm, c, direct_bob)
    direct_vm.mock_web(".*", {"status": 200, "body": "x" * 300})
    direct_vm.mock_llm(".*", {"unjustified": True, "score": 250,
                              "reason": "clear mismatch with the stated reason",
                              "confidence": "high"})
    c.judge_appeal(aid)
    assert int(c.get_appeal(aid)["score"]) == 100


# ------------------------ 6. an overturn is committed and protected ----

def test_overturn_is_recorded_as_committed(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    p = c.get_pools()
    assert int(c.get_appeal(aid)["state"]) == 1          # OVERTURNED
    assert int(p["committed"]) == COMP
    assert int(p["withdrawable"]) == POOL - COMP


def test_platform_cannot_withdraw_committed_compensation(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("uncommitted"):
        c.withdraw_pool(POOL, _addr(direct_alice))
    c.withdraw_pool(POOL - COMP, _addr(direct_alice))


def test_withdraw_pool_admin_only(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the platform admin"):
        c.withdraw_pool(B, _addr(direct_bob))


# ------------------------------ 7. underfunded / timeout recovery ----

def test_underfunded_overturn_becomes_unfunded(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    assert int(c.get_appeal(aid)["state"]) == 4          # UNFUNDED
    assert int(c.get_pools()["committed"]) == 0


def test_unfunded_stake_can_always_be_reclaimed(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    direct_vm.sender = direct_bob
    c.reclaim_stake(aid)
    a = c.get_appeal(aid)
    assert int(a["state"]) == 5                          # WITHDRAWN
    assert a["stake_settled"] is True


def test_unfunded_can_be_activated_after_top_up(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    assert int(c.get_appeal(aid)["state"]) == 4
    _fund(direct_vm, c, direct_alice, POOL)
    c.activate_unfunded(aid)
    assert int(c.get_appeal(aid)["state"]) == 1
    assert int(c.get_pools()["committed"]) == COMP


def test_reclaim_is_appellant_only(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Only the appellant"):
        c.reclaim_stake(aid)


def test_stale_appeal_can_be_reclaimed_after_timeout(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    aid = _appeal(direct_vm, c, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("still within the judging window"):
        c.reclaim_stake(aid)
    direct_vm.warp("2026-11-30T00:00:00.000Z")
    direct_vm.sender = direct_bob
    c.reclaim_stake(aid)
    assert int(c.get_appeal(aid)["state"]) == 5
    assert c.get_appeal(aid)["stake_settled"] is True


def test_overdue_view_matches_the_reclaim_window(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    aid = _appeal(direct_vm, c, direct_bob)
    assert c.get_appeal(aid)["overdue"] is False
    assert c.is_overdue(aid) is False
    direct_vm.warp("2026-11-30T00:00:00.000Z")
    assert c.is_overdue(aid) is True


def test_reclaim_voids_the_claim(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    direct_vm.sender = direct_bob
    c.reclaim_stake(aid)
    assert int(c.get_claim("chirper", "https://example.com/post", "harassment")) == 0
    aid2 = _appeal(direct_vm, c, direct_bob, digest="c" * 64)
    assert int(aid2) == 2


# --------------------------------------------------- 8. settlement ----

def test_overturned_appeal_paid_and_stake_returned(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    c.settle_appeal(aid)
    a = c.get_appeal(aid)
    assert int(a["state"]) == 3                          # SETTLED
    assert a["stake_settled"] is True
    p = c.get_pools()
    assert int(p["compensation_pool"]) == POOL - COMP
    assert int(p["committed"]) == 0


def test_upheld_appeal_stake_slashed(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_upheld(direct_vm)
    c.judge_appeal(aid)
    c.settle_appeal(aid)
    p = c.get_pools()
    assert int(p["slash_pool"]) == STAKE
    assert int(p["compensation_pool"]) == POOL
    assert int(c.get_appeal(aid)["state"]) == 3


def test_settle_requires_judgement(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    aid = _appeal(direct_vm, c, direct_bob)
    with direct_vm.expect_revert("has not been judged"):
        c.settle_appeal(aid)


def test_appeal_cannot_settle_twice(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    c.settle_appeal(aid)
    with direct_vm.expect_revert("already settled"):
        c.settle_appeal(aid)


def test_unfunded_cannot_be_settled_directly(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    with direct_vm.expect_revert("unfunded"):
        c.settle_appeal(aid)


def test_full_lifecycle(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)

    direct_vm.mock_web(".*", {"status": 200, "body": "x" * 300})
    direct_vm.mock_llm(".*win-marker.*", {"unjustified": True, "score": 92,
                                          "reason": "the reason names material absent from the page",
                                          "confidence": "high"})
    direct_vm.mock_llm(".*lose-marker.*", {"unjustified": False, "score": 88,
                                           "reason": "the page matches the stated reason",
                                           "confidence": "high"})

    won = _appeal(direct_vm, c, direct_bob, url="https://example.com/win-marker", digest="a" * 64)
    c.judge_appeal(won)
    c.settle_appeal(won)

    lost = _appeal(direct_vm, c, direct_bob, url="https://example.com/lose-marker", digest="b" * 64)
    c.judge_appeal(lost)
    c.settle_appeal(lost)

    p = c.get_pools()
    assert int(p["compensation_pool"]) == POOL - COMP
    assert int(p["slash_pool"]) == STAKE
    assert int(p["committed"]) == 0
    assert int(p["appeal_count"]) == 2


def test_stake_always_settles_one_way_or_the_other(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    aid = _appeal(direct_vm, c, direct_bob)
    _mock_unjustified(direct_vm)
    c.judge_appeal(aid)
    c.settle_appeal(aid)
    assert c.get_appeal(aid)["stake_settled"] is True


def test_contract_balance_view_exists(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    assert int(c.get_contract_balance()) >= 0
    assert int(c.get_appeal_count()) == 0
