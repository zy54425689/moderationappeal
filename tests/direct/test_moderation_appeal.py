# -*- coding: utf-8 -*-
"""ModerationAppeal direct-mode tests — moderation appeals ruled on-chain.

Run: pytest tests/direct/ -v

Unit: B = 0.001 GEN. Stake bounds are 0.01 .. 10 GEN, so a normal appeal stakes 100*B.

The appeal flow is now decision-backed: a platform operator commits a decision on chain, and only
the account named in that decision may appeal it. These tests cover the adversarial cases the
reviewer asked for: fabricated bans, unauthorised appellants, unsupported digests and validator
disagreement.
"""

B = 1000000000000000
STAKE = 100 * B
COMP = 400 * B
POOL = 2000 * B
DIG = "9f2c1a7be0d34f568192ab34cd56ef7890ab12cd34ef56789012ab34cd56ef78"
CHASH = "sha256:deadbeefcafe1234"
PLAT = "chirper"


def _deploy(direct_vm, direct_deploy, admin):
    import pathlib
    import tempfile

    src = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "moderation_appeal.py"
    tmp = pathlib.Path(tempfile.gettempdir()) / "moderation_appeal_local.py"
    tmp.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    direct_vm.sender = admin
    return direct_deploy(str(tmp))


def _addr(v):
    from genlayer import Address
    if isinstance(v, (bytes, bytearray)):
        return Address("0x" + bytes(v).hex())
    if isinstance(v, str) and not v.startswith("0x"):
        return Address("0x" + v)
    return Address(v)


def _register(direct_vm, c, operator, platform=PLAT):
    direct_vm.sender = operator
    c.register_platform(platform)


def _decide(direct_vm, c, operator, appellant, platform=PLAT, reason="harassment",
            chash=CHASH, digest=DIG):
    direct_vm.sender = operator
    return c.record_decision(platform, chash, reason, appellant, digest)


def _fund(direct_vm, c, who, amount, platform=PLAT):
    direct_vm.sender = who
    direct_vm.value = amount
    c.fund_platform_pool(platform)
    direct_vm.value = 0


def _appeal(direct_vm, c, who, did, url="https://example.com/post", digest=DIG, comp=COMP, stake=STAKE):
    direct_vm.sender = who
    direct_vm.value = stake
    aid = c.file_appeal(did, url, digest, comp)
    direct_vm.value = 0
    return aid


def _mock(direct_vm, unjustified, view, score=90, body="x" * 400):
    direct_vm.mock_web(".*", {"status": 200, "body": body})
    direct_vm.mock_llm(".*", {"unjustified": unjustified, "score": score,
                              "reason": "the fetched content is examined here",
                              "evidence_view": view, "confidence": "high"})


# ------------------------------------------------- platform registry ----

def test_platform_id_must_be_meaningful(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Platform id too short"):
        c.register_platform("ab")


def test_platform_cannot_be_registered_twice(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("already registered"):
        c.register_platform(PLAT)


def test_registration_records_the_operator(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    p = c.get_platform(PLAT)
    assert p["operator"].lower() == _addr(direct_alice).as_bytes.hex().join(["0x", ""])
    assert int(p["pool"]) == 0
    assert int(p["decision_count"]) == 0


# ------------------------------------- decision commitment (point 2) ----

def test_only_the_operator_can_record_a_decision(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Adversarial: a third party cannot fabricate a decision for someone else's platform."""
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the operator"):
        c.record_decision(PLAT, CHASH, "harassment", direct_bob.hex(), DIG)


def test_decision_binds_platform_content_reason_and_account(direct_vm, direct_deploy,
                                                            direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob, reason="harassment")
    d = c.get_decision(did)
    assert d["platform_id"] == PLAT
    assert d["content_hash"] == CHASH.lower()
    assert d["ban_reason"] == "harassment"
    assert d["decision_digest"] == DIG
    assert d["challenged"] is False
    assert int(c.get_platform(PLAT)["decision_count"]) == 1


def test_decision_requires_a_content_hash(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Content hash required"):
        c.record_decision(PLAT, "  ", "harassment", direct_bob.hex(), DIG)


def test_decision_requires_a_digest(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Decision digest required"):
        c.record_decision(PLAT, CHASH, "harassment", direct_bob.hex(), "short")


# --------------------------- adversarial: fabricated bans and impostors ----

def test_fabricated_ban_cannot_be_appealed_without_a_decision(direct_vm, direct_deploy,
                                                              direct_alice, direct_bob):
    """Adversarial: inventing a ban and filing straight away must fail."""
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("No such decision"):
        c.file_appeal(1, "https://example.com/harmless", DIG, COMP)
    direct_vm.value = 0


def test_unauthorised_appellant_is_refused(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Adversarial: only the account named in the decision may appeal it."""
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob)          # bob is the banned account

    other = _addr("0x" + "11" * 20)
    direct_vm.sender = other
    direct_vm.value = STAKE
    with direct_vm.expect_revert("Not the banned account"):
        c.file_appeal(did, "https://example.com/harmless", DIG, COMP)
    direct_vm.value = 0


def test_the_named_appellant_can_appeal(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    a = c.get_appeal(aid)
    assert a["platform_id"] == PLAT
    assert int(a["decision_id"]) == int(did)


def test_a_decision_can_only_be_appealed_once(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    _appeal(direct_vm, c, direct_bob, did)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("Already on appeal"):
        c.file_appeal(did, "https://example.com/again", DIG, COMP)
    direct_vm.value = 0


def test_unsupported_evidence_digest_is_refused(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Adversarial: an appeal cannot be filed without a usable digest of the notice."""
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("Digest required"):
        c.file_appeal(did, "https://example.com/post", "abc", COMP)
    direct_vm.value = 0


# --------------------------------------------- bounded amounts ----

def test_stake_below_minimum_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    direct_vm.value = B
    with direct_vm.expect_revert("Stake under minimum"):
        c.file_appeal(did, "https://example.com/post", DIG, COMP)
    direct_vm.value = 0


def test_stake_above_maximum_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    direct_vm.value = 11 * 10 ** 18
    with direct_vm.expect_revert("Stake over maximum"):
        c.file_appeal(did, "https://example.com/post", DIG, COMP)
    direct_vm.value = 0


def test_compensation_capped_against_stake(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("over 100x stake"):
        c.file_appeal(did, "https://example.com/post", DIG, 101 * STAKE)
    direct_vm.value = 0


def test_compensation_below_minimum_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("Compensation under minimum"):
        c.file_appeal(did, "https://example.com/post", DIG, 0)
    direct_vm.value = 0


def test_invalid_url_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("Valid URL required"):
        c.file_appeal(did, "not-a-url", DIG, COMP)
    direct_vm.value = 0


# ---------------------------- adversarial: validator disagreement (point 1) ----

def test_equivalence_rejects_a_leader_view_the_validator_cannot_reproduce(direct_vm, direct_deploy,
                                                                        direct_alice):
    """Adversarial: a well-formed verdict whose reported page state does not match an independent
    reading is refused, so consensus cannot accept a substantively wrong decision."""
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    body = "cooking recipes and oven temperatures " * 20      # nothing about harassment
    # leader claims the page is "related" while an independent classification says "unrelated"
    assert c.check_verdict("harassment", body, "related", False, 90) is False


def test_equivalence_refuses_to_overturn_when_the_reason_material_is_present(direct_vm, direct_deploy,
                                                                            direct_alice):
    """Adversarial: the page really does contain the named material, so an overturn is unsafe even
    though the verdict is well-formed."""
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    body = "harassment and threats against a named person " * 20
    assert c.check_verdict("harassment", body, "related", True, 95) is False


def test_equivalence_refuses_a_confident_uphold_on_an_unrelated_page(direct_vm, direct_deploy,
                                                                    direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    body = "cooking recipes and oven temperatures " * 20
    assert c.check_verdict("harassment", body, "unrelated", False, 90) is False


def test_equivalence_accepts_a_consistent_overturn(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    body = "cooking recipes and oven temperatures " * 20
    assert c.check_verdict("harassment", body, "unrelated", True, 90) is True


def test_equivalence_accepts_a_consistent_uphold(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    body = "harassment and threats against a named person " * 20
    assert c.check_verdict("harassment", body, "related", False, 85) is True


def test_equivalence_rejects_an_unusable_page_state(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    body = "cooking recipes " * 20
    assert c.check_verdict("harassment", body, "made-up-state", False, 90) is False


def test_equivalence_handles_a_missing_page(direct_vm, direct_deploy, direct_alice):
    """Too little content to check: only an uphold is safe, an overturn is still refused."""
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    assert c.check_verdict("harassment", "", "missing", True, 90) is False
    assert c.check_verdict("harassment", "", "missing", False, 40) is True


def test_judge_actually_runs_the_equivalence_rule(direct_vm, direct_deploy, direct_alice, direct_bob):
    """A consistent verdict is accepted by the live judge path."""
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    did = _decide(direct_vm, c, direct_alice, direct_bob, reason="harassment")
    aid = _appeal(direct_vm, c, direct_bob, did)

    _mock(direct_vm, True, "unrelated", 90, body="cooking recipes and ovens " * 20)
    c.judge_appeal(aid)
    assert int(c.get_appeal(aid)["state"]) == 1


def test_consistent_overturn_is_accepted(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Leader and validator agree the page has nothing matching the reason -> overturn stands."""
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    did = _decide(direct_vm, c, direct_alice, direct_bob, reason="harassment")
    aid = _appeal(direct_vm, c, direct_bob, did)

    _mock(direct_vm, unjustified=True, view="unrelated", score=90, body="cooking recipes " * 40)
    c.judge_appeal(aid)
    assert int(c.get_appeal(aid)["state"]) == 1


def test_consistent_uphold_is_accepted(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    did = _decide(direct_vm, c, direct_alice, direct_bob, reason="harassment")
    aid = _appeal(direct_vm, c, direct_bob, did)

    _mock(direct_vm, unjustified=False, view="related", score=85,
          body="harassment threats against a named person " * 20)
    c.judge_appeal(aid)
    assert int(c.get_appeal(aid)["state"]) == 2


def test_unreachable_page_upholds_the_ban(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    did = _decide(direct_vm, c, direct_alice, direct_bob, reason="harassment")
    aid = _appeal(direct_vm, c, direct_bob, did)

    direct_vm.mock_web(".*", {"status": 500, "body": ""})
    direct_vm.mock_llm(".*", {"unjustified": False, "score": 40,
                              "reason": "nothing could be checked",
                              "evidence_view": "missing", "confidence": "medium"})
    c.judge_appeal(aid)
    assert int(c.get_appeal(aid)["state"]) == 2


# ----------------------------------- platform-scoped pools (point 2b) ----

def test_compensation_comes_from_the_platform_pool(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice, "chirper")
    _register(direct_vm, c, direct_alice, "otherplat")
    _fund(direct_vm, c, direct_alice, POOL, "chirper")
    _fund(direct_vm, c, direct_alice, POOL * 3, "otherplat")

    did = _decide(direct_vm, c, direct_alice, direct_bob, "chirper")
    aid = _appeal(direct_vm, c, direct_bob, did)
    _mock(direct_vm, True, "unrelated", 90, body="cooking recipes " * 40)
    c.judge_appeal(aid)
    c.settle_appeal(aid)

    assert int(c.get_platform("chirper")["pool"]) == POOL - COMP
    assert int(c.get_platform("otherplat")["pool"]) == POOL * 3      # untouched


def test_admin_cannot_withdraw_committed_payout(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    _mock(direct_vm, True, "unrelated", 90, body="cooking recipes " * 40)
    c.judge_appeal(aid)

    p = c.get_platform(PLAT)
    assert int(p["committed"]) == COMP
    assert int(p["withdrawable"]) == POOL - COMP

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Exceeds uncommitted pool"):
        c.withdraw_pool(PLAT, POOL, _addr(direct_alice))
    c.withdraw_pool(PLAT, POOL - COMP, _addr(direct_alice))


def test_withdraw_is_operator_only(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the operator may withdraw"):
        c.withdraw_pool(PLAT, B, _addr(direct_bob))


# ------------------------------- underfunded / timeout recovery ----

def test_underfunded_overturn_becomes_unfunded(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    _mock(direct_vm, True, "unrelated", 90, body="cooking recipes " * 40)
    c.judge_appeal(aid)
    assert int(c.get_appeal(aid)["state"]) == 4
    assert int(c.get_platform(PLAT)["committed"]) == 0


def test_unfunded_can_be_activated_after_top_up(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    _mock(direct_vm, True, "unrelated", 90, body="cooking recipes " * 40)
    c.judge_appeal(aid)
    _fund(direct_vm, c, direct_alice, POOL)
    c.activate_unfunded(aid)
    assert int(c.get_appeal(aid)["state"]) == 1
    assert int(c.get_platform(PLAT)["committed"]) == COMP


def test_unfunded_stake_can_be_reclaimed(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    _mock(direct_vm, True, "unrelated", 90, body="cooking recipes " * 40)
    c.judge_appeal(aid)
    direct_vm.sender = direct_bob
    c.reclaim_stake(aid)
    assert int(c.get_appeal(aid)["state"]) == 5
    assert c.get_appeal(aid)["stake_settled"] is True


def test_reclaim_is_appellant_only(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    _mock(direct_vm, True, "unrelated", 90, body="cooking recipes " * 40)
    c.judge_appeal(aid)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Only the appellant"):
        c.reclaim_stake(aid)


def test_stale_appeal_reclaimable_after_timeout(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Still in the judging window"):
        c.reclaim_stake(aid)
    direct_vm.warp("2026-12-31T00:00:00.000Z")
    direct_vm.sender = direct_bob
    c.reclaim_stake(aid)
    assert int(c.get_appeal(aid)["state"]) == 5


def test_reclaim_reopens_the_decision(direct_vm, direct_deploy, direct_alice, direct_bob):
    """After a reclaim the decision is no longer marked challenged, so it can be appealed again."""
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    _mock(direct_vm, True, "unrelated", 90, body="cooking recipes " * 40)
    c.judge_appeal(aid)
    direct_vm.sender = direct_bob
    c.reclaim_stake(aid)
    assert c.get_decision(did)["challenged"] is False
    aid2 = _appeal(direct_vm, c, direct_bob, did, digest="c" * 64)
    assert int(aid2) == 2


# ------------------------------------------------- settlement ----

def test_overturned_appeal_pays_compensation_and_returns_stake(direct_vm, direct_deploy,
                                                              direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    _mock(direct_vm, True, "unrelated", 90, body="cooking recipes " * 40)
    c.judge_appeal(aid)
    c.settle_appeal(aid)

    a = c.get_appeal(aid)
    assert int(a["state"]) == 3
    assert a["stake_settled"] is True
    p = c.get_platform(PLAT)
    assert int(p["pool"]) == POOL - COMP
    assert int(p["committed"]) == 0


def test_upheld_appeal_forfeits_the_stake(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    _mock(direct_vm, False, "related", 85, body="harassment threats " * 20)
    c.judge_appeal(aid)
    c.settle_appeal(aid)
    assert int(c.get_platform(PLAT)["pool"]) == POOL + STAKE


def test_settle_requires_judgement(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    with direct_vm.expect_revert("Not judged yet"):
        c.settle_appeal(aid)


def test_appeal_cannot_settle_twice(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    _mock(direct_vm, True, "unrelated", 90, body="cooking recipes " * 40)
    c.judge_appeal(aid)
    c.settle_appeal(aid)
    with direct_vm.expect_revert("Already settled"):
        c.settle_appeal(aid)


def test_unfunded_cannot_be_settled_directly(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, COMP // 2)
    did = _decide(direct_vm, c, direct_alice, direct_bob)
    aid = _appeal(direct_vm, c, direct_bob, did)
    _mock(direct_vm, True, "unrelated", 90, body="cooking recipes " * 40)
    c.judge_appeal(aid)
    with direct_vm.expect_revert("Unfunded"):
        c.settle_appeal(aid)


def test_full_lifecycle(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, c, direct_alice)
    _fund(direct_vm, c, direct_alice, POOL)

    direct_vm.mock_web(".*", {"status": 200, "body": "cooking recipes " * 40})
    direct_vm.mock_llm(".*win-marker.*", {"unjustified": True, "score": 92,
                                          "reason": "the reason names material absent from the page",
                                          "evidence_view": "unrelated", "confidence": "high"})
    direct_vm.mock_llm(".*lose-marker.*", {"unjustified": False, "score": 88,
                                           "reason": "the page matches the recorded reason",
                                           "evidence_view": "unrelated", "confidence": "high"})

    d1 = _decide(direct_vm, c, direct_alice, direct_bob, chash="sha256:win")
    a1 = _appeal(direct_vm, c, direct_bob, d1, url="https://example.com/win-marker", digest="a" * 64)
    c.judge_appeal(a1)
    c.settle_appeal(a1)

    assert int(c.get_platform(PLAT)["pool"]) == POOL - COMP
    assert int(c.get_appeal_count()) == 1
