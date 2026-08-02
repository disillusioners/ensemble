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
    a=setup.create(project_id="p",slug="a",name="a",content="nothing"); setup.add_triggers(a.id,[("other",[1.,1.])])
    out=await BlueprintMatcher(setup,Embed(),BlueprintConfig(bm25_weight=0,vector_weight=1,match_threshold=.3)).match("p","other"); assert out[1].id==a.id
