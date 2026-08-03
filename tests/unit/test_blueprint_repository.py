import pytest
import sqlalchemy.exc
from sqlalchemy import create_engine
from sqlmodel import SQLModel
from daemon.repositories.blueprint.repository import BlueprintRepository

@pytest.fixture
def repo():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return BlueprintRepository(engine)

def bp(repo, project="p", slug="s", kind="area", content="content"):
    return repo.create(project_id=project, slug=slug, name=slug, kind=kind, content=content)

def test_create_and_get_by_id(repo):
    b=bp(repo); got=repo.get_by_id(b.id); assert got.content=="content" and got.project_id=="p"
def test_get_by_slug(repo):
    b=bp(repo, slug="hello"); assert repo.get_by_slug("p","hello").id==b.id
def test_get_core(repo):
    bp(repo,slug="a",kind="area"); c=bp(repo,slug="c",kind="core"); assert repo.get_core("p").id==c.id
def test_list_by_project(repo):
    bp(repo,slug="a"); bp(repo,slug="b",kind="core"); bp(repo,project="q",slug="q"); repo.soft_delete(repo.get_by_slug("p","a").id)
    assert len(repo.list_by_project("p"))==1 and len(repo.list_by_project("p",active_only=False))==2
    assert len(repo.list_by_project("p",kind="core"))==1
def test_update_bumps_version(repo):
    b=bp(repo); repo.update(b.id,content="new"); assert repo.get_by_id(b.id).version==2; repo.update(b.id,name="x"); assert repo.get_by_id(b.id).version==2
def test_soft_delete(repo):
    b=bp(repo); assert repo.soft_delete(b.id); assert not repo.get_by_id(b.id).is_active
def test_trigger_methods(repo):
    b=bp(repo); assert repo.replace_triggers(b.id,[("a",[1.]),("b",[2.])])==2; assert len(repo.get_triggers_by_blueprint(b.id))==2; assert repo.replace_triggers(b.id,[("c",[3.])])==1
def test_revision_methods(repo):
    b=bp(repo); repo.add_revision(blueprint_id=b.id,version=1,content_snapshot="a"); repo.add_revision(blueprint_id=b.id,version=2,content_snapshot="b"); assert [r.version for r in repo.list_revisions(b.id)]==[2,1]
def test_search_candidates(repo):
    c=bp(repo,slug="c",kind="core"); a=bp(repo,slug="a"); repo.replace_triggers(a.id,[("x",[]) ]); assert [(x.id,len(t)) for x,t in repo.search_candidates("p")] == [(a.id,1)]
def test_project_isolation(repo):
    bp(repo,project="a",slug="x"); bp(repo,project="b",slug="x"); assert len(repo.list_by_project("a"))==1 and repo.get_core("a") is None


def test_trigger_queries_field(repo):
    b = repo.create(
        project_id="p", slug="s", name="s", kind="area", content="c",
        trigger_queries=["how to auth", "login flow"],
    )
    got = repo.get_by_id(b.id)
    assert got.trigger_queries == ["how to auth", "login flow"]


def test_duplicate_slug_rejected(repo):
    repo.create(project_id="p1", slug="my-bp", name="first", kind="area", content="c")
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        repo.create(project_id="p1", slug="my-bp", name="second", kind="area", content="c")


def test_duplicate_name_rejected(repo):
    repo.create(project_id="p1", slug="s1", name="same-name", kind="area", content="c")
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        repo.create(project_id="p1", slug="s2", name="same-name", kind="area", content="c")


def test_revision_with_new_fields(repo):
    b = bp(repo)
    repo.add_revision(
        blueprint_id=b.id,
        version=1,
        content_snapshot="snap",
        file_refs=["a.py", "b.py"],
        tags=[{"category": "x", "value": "y"}],
        trigger_queries=["q1"],
        reason="updated content",
    )
    revs = repo.list_revisions(b.id)
    assert len(revs) == 1
    r = revs[0]
    assert r.file_refs == ["a.py", "b.py"]
    assert r.tags == [{"category": "x", "value": "y"}]
    assert r.trigger_queries == ["q1"]
    assert r.reason == "updated content"
    # field renamed from revision_summary -> reason
    assert not hasattr(r, "revision_summary")


def test_revision_failure_does_not_block_update(repo, monkeypatch):
    """Mock add_revision to raise → update still returns updated blueprint."""
    b = bp(repo)
    # Make add_revision raise on the next call (revision capture runs
    # post-commit inside update()).
    monkeypatch.setattr(repo, "add_revision", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    result = repo.update(b.id, content="still works")
    assert result is not None
    assert result.content == "still works"
    # The update committed; the revision table is empty because
    # add_revision raised (and was swallowed — C8).
    assert repo.list_revisions(b.id) == []


def test_update_creates_revision_on_content_change(repo):
    b = bp(repo)
    repo.update(b.id, content="new")
    revs = repo.list_revisions(b.id)
    assert len(revs) == 1
    assert revs[0].content_snapshot == "new"
    assert revs[0].version == 2


def test_update_creates_revision_on_trigger_change(repo):
    b = bp(repo)
    repo.update(b.id, trigger_queries=["q"])
    revs = repo.list_revisions(b.id)
    assert len(revs) == 1
    assert revs[0].trigger_queries == ["q"]


def test_update_no_revision_on_metadata_change(repo):
    b = bp(repo)
    repo.update(b.id, name="x")
    assert repo.list_revisions(b.id) == []


def test_update_with_reason_does_not_raise(repo):
    b = bp(repo)
    # Must NOT raise ValueError: Unknown Blueprint field: reason
    result = repo.update(b.id, content="x", reason="y")
    assert result is not None
    revs = repo.list_revisions(b.id)
    assert len(revs) == 1
    assert revs[0].reason == "y"


def test_update_with_empty_triggers_clears(repo):
    b = bp(repo)
    assert b.version == 1
    repo.update(b.id, trigger_queries=[])
    got = repo.get_by_id(b.id)
    assert got.version == 2
    assert got.trigger_queries == []
    revs = repo.list_revisions(b.id)
    assert len(revs) == 1
    assert revs[0].trigger_queries == []


def test_multiple_updates_multiple_revisions(repo):
    b = bp(repo)
    repo.update(b.id, content="v2")
    repo.update(b.id, content="v3")
    repo.update(b.id, content="v4")
    revs = repo.list_revisions(b.id)
    assert len(revs) == 3
    # Ordered by version DESC
    assert [r.version for r in revs] == [4, 3, 2]


def test_revision_captures_new_state(repo):
    b = bp(repo, content="old")
    repo.update(b.id, content="new")
    revs = repo.list_revisions(b.id)
    assert len(revs) == 1
    # Revision captures the NEW state, not the old.
    assert revs[0].content_snapshot == "new"


def test_update_trigger_queries_bumps_version(repo):
    b = bp(repo)
    assert b.version == 1
    repo.update(b.id, trigger_queries=["new query"])
    got = repo.get_by_id(b.id)
    assert got.version == 2
    assert got.trigger_queries == ["new query"]
