"""Unit + integration tests for orchestrator/maker/quota.py — Step C7 gate."""
from __future__ import annotations

import pytest
import aiosqlite

from orchestrator.store import init_db
from orchestrator.maker.quota import (
    GoogleCSEQuota,
    QuotaExceeded,
    QuotaStatus,
    ConsumeResult,
    FREE_QUERIES_PER_DAY,
    COST_PER_QUERY_USD,
    WARN_AT_REMAINING,
)


# ---------------------------------------------------------------------------
# Fixture: in-memory SQLite with full schema
# ---------------------------------------------------------------------------

@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        await init_db(conn)
        yield conn


# ---------------------------------------------------------------------------
# check() — fresh DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_fresh_db(db):
    q = GoogleCSEQuota()
    status = await q.check(db)
    assert status.used_today == 0
    assert status.free_remaining == FREE_QUERIES_PER_DAY
    assert status.over_quota_cap_usd == 0.0
    assert status.approvals_in_force == []


@pytest.mark.asyncio
async def test_check_returns_quota_status_type(db):
    q = GoogleCSEQuota()
    status = await q.check(db)
    assert isinstance(status, QuotaStatus)


# ---------------------------------------------------------------------------
# consume() — within free tier
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consume_within_free_tier(db):
    q = GoogleCSEQuota(free_per_day=100)
    result = await q.consume(db, queries=10)
    assert isinstance(result, ConsumeResult)
    assert result.queries_used == 10
    assert result.total_used_today == 10
    assert result.is_paid is False
    assert result.cost_usd == 0.0


@pytest.mark.asyncio
async def test_consume_accumulates_usage(db):
    q = GoogleCSEQuota(free_per_day=100)
    await q.consume(db, queries=30)
    await q.consume(db, queries=20)
    status = await q.check(db)
    assert status.used_today == 50
    assert status.free_remaining == 50


@pytest.mark.asyncio
async def test_consume_exactly_at_free_limit(db):
    q = GoogleCSEQuota(free_per_day=10)
    result = await q.consume(db, queries=10)
    assert result.is_paid is False
    assert result.total_used_today == 10


# ---------------------------------------------------------------------------
# test_google_cse_over_quota_block — §11 gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_cse_over_quota_block(db):
    """Past free-limit + no approval → raises QuotaExceeded."""
    q = GoogleCSEQuota(free_per_day=5)
    await q.consume(db, queries=5)   # use up free quota
    with pytest.raises(QuotaExceeded) as exc_info:
        await q.consume(db, queries=1)
    assert exc_info.value.used == 5
    assert exc_info.value.cap == 5
    assert exc_info.value.approved_usd == 0.0


@pytest.mark.asyncio
async def test_over_quota_block_preserves_usage_count(db):
    """Failed consume() must not write to cost_ledger."""
    q = GoogleCSEQuota(free_per_day=5)
    await q.consume(db, queries=5)
    with pytest.raises(QuotaExceeded):
        await q.consume(db, queries=3)
    # usage should still be 5, not 8
    status = await q.check(db)
    assert status.used_today == 5


# ---------------------------------------------------------------------------
# approve_over_quota + test_google_cse_over_quota_approved — §11 gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_cse_over_quota_approved(db):
    """After quota approve $X is in force, paid queries proceed up to cap."""
    q = GoogleCSEQuota(free_per_day=5, cost_per_query=0.005)
    await q.consume(db, queries=5)           # exhaust free tier
    await q.approve_over_quota(db, dollars=0.10)  # approve $0.10 = 20 queries
    result = await q.consume(db, queries=10)
    assert result.is_paid is True
    assert result.cost_usd == pytest.approx(10 * 0.005)
    assert result.total_used_today == 15


@pytest.mark.asyncio
async def test_approve_persists_and_check_reflects_it(db):
    """Approval row shows up in QuotaStatus.approvals_in_force."""
    q = GoogleCSEQuota()
    await q.approve_over_quota(db, dollars=5.0, job_id="job-abc")
    status = await q.check(db)
    assert status.over_quota_cap_usd == pytest.approx(5.0)
    assert len(status.approvals_in_force) == 1
    assert status.approvals_in_force[0]["job_id"] == "job-abc"
    assert status.approvals_in_force[0]["approved_usd"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_approve_without_job_id(db):
    q = GoogleCSEQuota()
    await q.approve_over_quota(db, dollars=2.0)
    status = await q.check(db)
    assert status.approvals_in_force[0]["job_id"] is None


@pytest.mark.asyncio
async def test_multiple_approvals_sum(db):
    q = GoogleCSEQuota(free_per_day=5, cost_per_query=0.005)
    await q.approve_over_quota(db, dollars=0.05)
    await q.approve_over_quota(db, dollars=0.05)
    await q.consume(db, queries=5)          # exhaust free
    result = await q.consume(db, queries=10)  # $0.05 cost — within $0.10 total cap
    assert result.is_paid is True


@pytest.mark.asyncio
async def test_over_quota_blocked_even_with_insufficient_approval(db):
    q = GoogleCSEQuota(free_per_day=5, cost_per_query=0.01)
    await q.consume(db, queries=5)
    await q.approve_over_quota(db, dollars=0.05)  # only covers 5 over-quota queries
    with pytest.raises(QuotaExceeded):
        await q.consume(db, queries=10)   # costs $0.10 > $0.05 approved


# ---------------------------------------------------------------------------
# warn_at threshold — check() free_remaining exposes it; callers emit warning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_free_remaining_reflects_usage(db):
    q = GoogleCSEQuota(free_per_day=100, warn_at=10)
    await q.consume(db, queries=91)
    status = await q.check(db)
    assert status.free_remaining == 9
    # Caller (google_cse adapter) checks: free_remaining <= quota.warn_at → emit warning
    assert status.free_remaining <= q.warn_at


@pytest.mark.asyncio
async def test_free_remaining_never_negative(db):
    q = GoogleCSEQuota(free_per_day=5, cost_per_query=0.005)
    await q.consume(db, queries=5)
    await q.approve_over_quota(db, dollars=1.0)
    await q.consume(db, queries=5)
    status = await q.check(db)
    assert status.free_remaining == 0


# ---------------------------------------------------------------------------
# QuotaExceeded exception attributes
# ---------------------------------------------------------------------------

def test_quota_exceeded_attributes():
    exc = QuotaExceeded(used=50, cap=50, approved_usd=0.0)
    assert exc.used == 50
    assert exc.cap == 50
    assert exc.approved_usd == 0.0
    assert "50/50" in str(exc)


# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------

def test_module_defaults():
    assert FREE_QUERIES_PER_DAY == 100
    assert COST_PER_QUERY_USD == pytest.approx(0.005)
    assert WARN_AT_REMAINING == 10


def test_quota_instance_uses_defaults():
    q = GoogleCSEQuota()
    assert q.free_per_day == 100
    assert q.cost_per_query == pytest.approx(0.005)
    assert q.warn_at == 10
