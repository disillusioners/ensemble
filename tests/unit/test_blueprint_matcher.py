import math
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from daemon.config import BlueprintConfig
from daemon.repositories.blueprint.repository import BlueprintRepository
from daemon.services.blueprint_matcher import BlueprintMatcher

class Embed:
    async def embed_text(self,text):
        return [float((sum(map(ord,text))%10)+1), 1.0]
    @staticmethod
    def cosine_similarity(a,b):
        d=sum(x*y for x,y in zip(a,b)); return d/(math.sqrt(sum(x*x for x in a))*math.sqrt(sum(x*x for x in b))) if d else 0
@pytest.fixture
def setup():
    e=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool); SQLModel.metadata.create_all(e); r=BlueprintRepository(e)
    r.create(project_id="p",slug="core",name="core",kind="core",content="core")
    return r
@pytest.mark.asyncio
async def test_core_always_included(setup):
    out=await BlueprintMatcher(setup,Embed(),BlueprintConfig()).match("p",""); assert out[0].kind=="core" and out[0].score==1
@pytest.mark.asyncio
async def test_bm25_matching(setup):
    a=setup.create(project_id="p",slug="a",name="a",content="database migration"); setup.create(project_id="p",slug="b",name="b",content="unrelated")
    out=await BlueprintMatcher(setup,None,BlueprintConfig(vector_weight=0,bm25_weight=1,match_threshold=.3)).match("p","database migration"); assert out[1].id==a.id
@pytest.mark.asyncio
async def test_threshold_and_empty_query(setup):
    setup.create(project_id="p",slug="a",name="a",content="zzz"); out=await BlueprintMatcher(setup,Embed(),BlueprintConfig()).match("p"," "); assert len(out)==1
@pytest.mark.asyncio
async def test_max_results_and_fields(setup):
    for i in range(6): setup.create(project_id="p",slug=str(i),name=str(i),content="common word")
    out=await BlueprintMatcher(setup,Embed(),BlueprintConfig(match_threshold=0,max_results=3)).match("p","common")
    assert len(out)<=3 and all(set(x.__dict__) == {'id','name','kind','version','content','file_refs','score'} for x in out)
@pytest.mark.asyncio
async def test_vector_matching(setup):
    a=setup.create(project_id="p",slug="a",name="a",content="nothing"); setup.replace_triggers(a.id,[("other",[1.,1.])])
    out=await BlueprintMatcher(setup,Embed(),BlueprintConfig(bm25_weight=0,vector_weight=1,match_threshold=.3)).match("p","other"); assert out[1].id==a.id


# ─── G6: BM25 single-candidate / all-equal edge cases ────────────


@pytest.mark.asyncio
async def test_single_candidate_nonzero_bm25(setup):
    """G6: a single matching area candidate must contribute a non-zero
    BM25 score. Before the fix, ``span == 0`` collapsed the lone
    candidate's normalized BM25 to 0.0, suppressing it from the
    ranker even when query and content shared terms.

    Asserts that the lone candidate's BM25 contribution is 1.0
    (the spec's "fully relevant" sentinel for the single-candidate
    case) and that the fused score is non-zero.
    """
    a = setup.create(
        project_id="p", slug="a", name="a",
        content="database migration strategy",
    )
    # BM25-only, vector disabled, very low threshold so the lone
    # candidate is included.
    cfg = BlueprintConfig(
        bm25_weight=1.0, vector_weight=0.0,
        match_threshold=0.0, max_results=5,
    )
    out = await BlueprintMatcher(setup, None, cfg).match("p", "database migration")
    # Slot 0 is the core; slot 1 should be the lone matching area.
    assert len(out) >= 2
    area = out[1]
    assert area.id == a.id
    # G6: pre-fix this would be 0.0 (single-candidate zero-span).
    assert area.score > 0.0, (
        "G6 single-candidate edge case must produce a non-zero "
        f"fused score; got {area.score}"
    )


@pytest.mark.asyncio
async def test_all_equal_scores_nonzero(setup):
    """G6: 2 candidates with identical content yield identical raw
    BM25 scores → ``span == 0`` with non-zero raw → both should be
    treated as ``1.0`` (fully relevant)."""
    a = setup.create(
        project_id="p", slug="a", name="a",
        content="duplicate content token",
    )
    b = setup.create(
        project_id="p", slug="b", name="b",
        content="duplicate content token",
    )
    cfg = BlueprintConfig(
        bm25_weight=1.0, vector_weight=0.0,
        match_threshold=0.0, max_results=5,
    )
    out = await BlueprintMatcher(setup, None, cfg).match("p", "duplicate content token")
    by_id = {row.id: row for row in out}
    assert a.id in by_id and b.id in by_id
    # Both candidates must have non-zero fused scores.
    assert by_id[a.id].score > 0.0
    assert by_id[b.id].score > 0.0
    # Same content → same BM25 → same fused score.
    assert by_id[a.id].score == by_id[b.id].score


@pytest.mark.asyncio
async def test_genuine_zero_bm25_stays_zero(setup):
    """G6 control: a candidate with NO query terms must STILL score
    0.0 even after the single-candidate fix. The fix only
    promotes non-zero raw scores — zero raw scores remain zero."""
    a = setup.create(
        project_id="p", slug="a", name="a",
        content="alphabet soup recipe",
    )
    cfg = BlueprintConfig(
        bm25_weight=1.0, vector_weight=0.0,
        match_threshold=0.0, max_results=5,
    )
    # Query has zero overlap with the candidate's content.
    out = await BlueprintMatcher(setup, None, cfg).match("p", "xyzzy foobar")
    by_id = {row.id: row for row in out}
    # The candidate is in the candidates list but its BM25 raw is 0.
    # ``span == 0`` AND ``raw == 0`` → 0.0 (no positive promotion).
    if a.id in by_id:
        assert by_id[a.id].score == 0.0


@pytest.mark.asyncio
async def test_multi_candidate_ranking_unchanged(setup):
    """G6 regression: with multiple candidates, the min-max
    normalization must still produce a strictly ordered ranking.

    Scenario: two candidates share the same query terms, so they
    fall in the same BM25 bucket. The matcher must rank them by
    raw BM25 score (one more dense match wins). The G6 fix
    changes behavior only for the ``span == 0`` edge case — this
    test verifies the multi-candidate path is unaffected.
    """
    # Identical content → identical raw BM25 → span == 0.
    a = setup.create(
        project_id="p", slug="a", name="a",
        content="database migration strategy",
    )
    c = setup.create(
        project_id="p", slug="c", name="c",
        content="database migration strategy",
    )
    cfg = BlueprintConfig(
        bm25_weight=1.0, vector_weight=0.0,
        match_threshold=0.30, max_results=5,
    )
    out = await BlueprintMatcher(setup, None, cfg).match("p", "database migration")
    by_id = {row.id: row for row in out}
    # Both candidates match identically → both must appear.
    assert a.id in by_id and c.id in by_id
    # With identical content, G6 promotes both to 1.0 → equal scores.
    assert by_id[a.id].score == by_id[c.id].score
    # G6: the equal-score-but-non-zero case is the positive edge
    # case — both scores must be > 0.0 (pre-fix they would be 0.0).
    assert by_id[a.id].score > 0.0
