# jaaf-p9 live HTTP route smoke evidence

```
[    3.9s] booting real app via create_app() + TestClient lifespan ...
[   20.4s] FENCE: InstanceManager.start_sources() patched to no-op (smoke process never connects chat sources)
[   20.5s] app booted with lifespan in 16.6s
[   20.5s] manager wired; is_write_paused=False; daemon.__version__=0.25.4
[   20.5s] REAL route path resolved: POST /api/jobs/{work_id}/answer
[   20.5s] seeding mission M1 (root R1, child C1 with pending pack P1, work W1 on R1)
[   20.6s] M1 ids: R1=inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2 C1=inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3 W1=fa6faf38-e50c-4e73-a71a-11b1448dafab C1-handle=1e2f2477-f099-4cb4-8665-f945339b0b4c P1=d3ad769e-09ec-4b96-864d-a6ee7eb7da2f
[   20.6s] BEFORE states (M1):
[   20.6s]   DB[M1-before-C1] instances.status = 'paused'  (instance_id=inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3)
[   20.6s]   DB[M1-before-C1] task[handle] work_id=1e2f2477-f099-4cb4-8665-f945339b0b4c status='paused' suspension_reason='awaiting_answer' resume_target_turn_id='1e2f2477-f099-4cb4-8665-f945339b0b4c'
[   20.6s]   DB[M1-before-C1] task[work] work_id=fa6faf38-e50c-4e73-a71a-11b1448dafab status='running' suspension_reason=None resume_target_turn_id=None
[   20.6s]   DB[M1-before-R1] instances.status = 'paused'  (instance_id=inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2)
[   20.6s]   DB[M1-before-R1] task[work] work_id=fa6faf38-e50c-4e73-a71a-11b1448dafab status='running' suspension_reason=None resume_target_turn_id=None
[   20.6s] seeding mission M2 (root R2, children CA/CB with distinct pending packs PA/PB, work W2 on R2)
[   20.7s] M2 ids: R2=inst-128b2ef9-22ad-4b58-928f-039d2add2a19 CA=inst-12ce625c-5494-473c-9ee9-c34c6308d677 CB=inst-a05c7cb7-aabe-4eb9-af43-8be4347c48ad W2=c9edd00a-e84f-4cdc-a54a-3267c8054425 PA=fdc9a7fc-600d-4511-9436-7d9e08bb25d9 PB=a8936ec1-a94f-4d68-848e-5219a9e404b9
[   20.7s] ═══ S1: POST job-scoped answer WITH pack id ═══
[   20.7s] S1 request: POST /api/jobs/fa6faf38-e50c-4e73-a71a-11b1448dafab/answer body={'answers': {'q1': 'Approach A (p9-smoke answer)'}, 'question_pack_id': 'd3ad769e-09ec-4b96-864d-a6ee7eb7da2f'}
[   21.2s] S1 response: HTTP 200
[   21.2s] S1 body verbatim: {"work_id":"fa6faf38-e50c-4e73-a71a-11b1448dafab","answer_target":{"instance_id":"inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3","work_instance_id":"inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2","mission_root_id":"inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2","route":"subtree_pack_id","reachable_count":2,"scan_uncapped":false},"status":"answered","instance_id":"inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3","question_pack":{"instance_id":"inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3","pack_id":"d3ad769e-09ec-4b96-864d-a6ee7eb7da2f","status":"answered","created_at":"2026-10-19T23:49:37.718279+00:00","questions":[{"id":"q1","text":"P1: Approach A or B?","options":[],"allow_custom":true,"required":true,"answer":null,"option_descriptions":{}}],"answers":{"q1":"Approach A (p9-smoke answer)"}},"resume_route":"answer_gate_existing_turn","resume_info":{"resumed":true,"resumed_ids":["inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2","inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3"],"skipped_ids":[],"target_id":"inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3","resume_results":{"inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3":{"instance_id":"inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3","job_id":"1e2f2477-f099-4cb4-8665-f945339b0b4c","message_id":"6b843917-15a6-4863-a788-5a56cf900750","status":"resuming"},"inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2":{"instance_id":"inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2","job_id":"fa6faf38-e50c-4e73-a71a-11b1448dafab","message_id":"a10b3a28-114f-478f-accc-0f96794a297e","status":"resuming"}}}}
[   21.2s] S1 parsed: status='answered' instance_id='inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3' answer_target={'instance_id': 'inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3', 'work_instance_id': 'inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2', 'mission_root_id': 'inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2', 'route': 'subtree_pack_id', 'reachable_count': 2, 'scan_uncapped': False} work_id='fa6faf38-e50c-4e73-a71a-11b1448dafab'
[   24.2s] AFTER states (M1):
[   24.2s]   DB[M1-after-C1] instances.status = 'running'  (instance_id=inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3)
[   24.2s]   DB[M1-after-C1] task[handle] work_id=1e2f2477-f099-4cb4-8665-f945339b0b4c status='failed' suspension_reason=None resume_target_turn_id=None
[   24.2s]   DB[M1-after-C1] task[work] work_id=fa6faf38-e50c-4e73-a71a-11b1448dafab status='running' suspension_reason=None resume_target_turn_id=None
[   24.2s]   DB[M1-after-R1] instances.status = 'running'  (instance_id=inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2)
[   24.2s]   DB[M1-after-R1] task[work] work_id=fa6faf38-e50c-4e73-a71a-11b1448dafab status='running' suspension_reason=None resume_target_turn_id=None
[   24.2s] S1 DB transition child C1: 'paused' -> 'running'
[   24.2s] S1 DB transition root  R1: 'paused' -> 'running' (noted, not gated)
[   24.2s] S1 DB transition C1 handle task 1e2f2477-f099-4cb4-8665-f945339b0b4c: 'failed' (suspension_reason=None)
[   24.2s] S1 DB transition work  task fa6faf38-e50c-4e73-a71a-11b1448dafab: 'running' -> 'running'
[   24.2s] S1 RAM pack on C1 after: id=d3ad769e-09ec-4b96-864d-a6ee7eb7da2f status=answered
[   24.2s] S1 durable shadow on C1 after consumption: None (expect None)
[   24.2s] ═══ S2: POST the SAME answer again (idempotency) ═══
[   24.3s] S2 response: HTTP 200
[   24.3s] S2 body verbatim: {"work_id":"fa6faf38-e50c-4e73-a71a-11b1448dafab","answer_target":{"instance_id":"inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3","work_instance_id":"inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2","mission_root_id":"inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2","route":"subtree_pack_id","reachable_count":2,"scan_uncapped":false},"status":"already_delivered","instance_id":"inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3","question_pack":{"instance_id":"inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3","pack_id":"d3ad769e-09ec-4b96-864d-a6ee7eb7da2f","status":"answered","created_at":"2026-10-19T23:49:37.718279+00:00","questions":[{"id":"q1","text":"P1: Approach A or B?","options":[],"allow_custom":true,"required":true,"answer":null,"option_descriptions":{}}],"answers":{"q1":"Approach A (p9-smoke answer)"}},"resume_route":"already_delivered"}
[   24.3s] S2 parsed: status='already_delivered' instance_id='inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3' resume_route='already_delivered'
[   24.3s] ═══ S3: POST WITHOUT pack id into a two-packer mission (ambiguity) ═══
[   24.3s] S3 request: POST /api/jobs/c9edd00a-e84f-4cdc-a54a-3267c8054425/answer body={'answers': {'q1': 'no pack id supplied (p9-smoke)'}}
[   24.4s] S3 response: HTTP 400
[   24.4s] S3 body verbatim: {"detail":{"code":"INVALID_REQUEST","message":"Ambiguous answer target: 2 instances in this mission hold a pending question (inst-12ce625c-5494-473c-9ee9-c34c6308d677, inst-a05c7cb7-aabe-4eb9-af43-8be4347c48ad) — supply question_pack_id to select the right one.","details":{"resolved_instance_id":"inst-128b2ef9-22ad-4b58-928f-039d2add2a19","work_instance_id":"inst-128b2ef9-22ad-4b58-928f-039d2add2a19","mission_root_id":"inst-128b2ef9-22ad-4b58-928f-039d2add2a19","question_pack_id":null,"subtree_scanned":true,"subtree_truncated":false,"reachable_count":3,"scan_uncapped":false,"resolution_route":null,"candidate_ids":["inst-12ce625c-5494-473c-9ee9-c34c6308d677","inst-a05c7cb7-aabe-4eb9-af43-8be4347c48ad"],"work_instance_status":null}}}
[   24.4s] S3 parsed: code='INVALID_REQUEST' candidate_ids=['inst-12ce625c-5494-473c-9ee9-c34c6308d677', 'inst-a05c7cb7-aabe-4eb9-af43-8be4347c48ad'] (both children named)
[   24.4s] ═══ S4: job_answer tool via the REAL production factory ═══
[   24.5s] S4 factory produced 24 tools; selected by name: job_answer
[   24.9s] S4 tool output verbatim: {'work_id': 'c9edd00a-e84f-4cdc-a54a-3267c8054425', 'answer_target': {'instance_id': 'inst-12ce625c-5494-473c-9ee9-c34c6308d677', 'work_instance_id': 'inst-128b2ef9-22ad-4b58-928f-039d2add2a19', 'mission_root_id': 'inst-128b2ef9-22ad-4b58-928f-039d2add2a19', 'route': 'subtree_pack_id', 'reachable_count': 3, 'scan_uncapped': False}, 'status': 'answered', 'instance_id': 'inst-12ce625c-5494-473c-9ee9-c34c6308d677', 'question_pack': {'instance_id': 'inst-12ce625c-5494-473c-9ee9-c34c6308d677', 'pack_id': 'fdc9a7fc-600d-4511-9436-7d9e08bb25d9', 'status': 'answered', 'created_at': '2026-10-19T23:49:37.805824+00:00', 'questions': [{'id': 'q1', 'text': 'PA: Choose X or Y?', 'options': [], 'allow_custom': True, 'required': True, 'answer': None, 'option_descriptions': {}}], 'answers': {'q1': 'Choose X (p9-smoke tool answer)'}}, 'resume_route': 'answer_gate_existing_turn', 'resume_info': {'resumed': True, 'resumed_ids': ['inst-128b2ef9-22ad-4b58-928f-039d2add2a19', 'inst-12ce625c-5494-473c-9ee9-c34c6308d677', 'inst-a05c7cb7-aabe-4eb9-af43-8be4347c48ad'], 'skipped_ids': [], 'target_id': 'inst-12ce625c-5494-473c-9ee9-c34c6308d677', 'resume_results': {'inst-12ce625c-5494-473c-9ee9-c34c6308d677': {'instance_id': 'inst-12ce625c-5494-473c-9ee9-c34c6308d677', 'job_id': 'd2901123-13a8-4e87-921c-967f4c339f34', 'message_id': '4e387c9b-703b-43d6-91f6-f67c6c286659', 'status': 'resuming'}, 'inst-128b2ef9-22ad-4b58-928f-039d2add2a19': {'instance_id': 'inst-128b2ef9-22ad-4b58-928f-039d2add2a19', 'job_id': 'c9edd00a-e84f-4cdc-a54a-3267c8054425', 'message_id': '51e6d1fe-531e-4dc2-b461-a8e9f2cc1cdc', 'status': 'resuming'}, 'inst-a05c7cb7-aabe-4eb9-af43-8be4347c48ad': {'instance_id': 'inst-a05c7cb7-aabe-4eb9-af43-8be4347c48ad', 'job_id': None, 'message_id': None, 'status': 'silent_resume'}}}}
[   24.9s] S4 DB after: CA status='running' CB status='running' R2 status='running'
[   24.9s] S4 RAM pack on CA after: status=answered
[   24.9s] ═══ all four scenarios behaved as specified ═══
[   24.9s] ── cleanup begin (exact-id predicates only) ──
[   25.0s] cleanup: DELETE FROM event WHERE instance_id = ANY(5 recorded ids) -> 3 rows
[   25.0s] cleanup: DELETE FROM message_queue WHERE instance_id = ANY(5 recorded ids) -> 0 rows
[   25.0s] cleanup: DELETE FROM checkpoints WHERE thread_id = ANY(5 recorded ids) -> 0 rows
[   25.0s] cleanup: DELETE FROM checkpoint_blobs WHERE thread_id = ANY(5 recorded ids) -> 0 rows
[   25.0s] cleanup: DELETE FROM checkpoint_writes WHERE thread_id = ANY(5 recorded ids) -> 0 rows
[   25.0s] cleanup: DELETE FROM instance_hierarchy WHERE parent_id = ANY(5 recorded ids) -> 0 rows
[   25.0s] cleanup: DELETE FROM instance_hierarchy WHERE child_id = ANY(5 recorded ids) -> 0 rows
[   25.0s] cleanup: table job_watcher absent — skipped
[   25.0s] cleanup: DELETE FROM task WHERE work_id = ANY(5 recorded ids) -> 5 rows
[   25.0s] cleanup: DELETE FROM instances WHERE instance_id = ANY(5 recorded ids) -> 5 rows
[   25.0s] residue verification (every count must be 0):
[   25.0s]   SELECT count(*) FROM instances WHERE instance_id = ANY(ids) -> 0
[   25.0s]   SELECT count(*) FROM task WHERE work_id = ANY(ids) -> 0
[   25.0s]   SELECT count(*) FROM event WHERE instance_id = ANY(ids) -> 0
[   25.0s]   SELECT count(*) FROM message_queue WHERE instance_id = ANY(ids) -> 0
[   25.0s]   SELECT count(*) FROM checkpoints WHERE thread_id = ANY(ids) -> 0
[   25.0s]   SELECT count(*) FROM checkpoint_blobs WHERE thread_id = ANY(ids) -> 0
[   25.0s]   SELECT count(*) FROM checkpoint_writes WHERE thread_id = ANY(ids) -> 0
[   25.0s]   residue job_watcher: table absent — n/a
[   25.0s] row ids created (instances): ['inst-8e4ba2f6-8d5e-4fe5-8d54-659c6c595de2', 'inst-73f8eeff-0566-4a9b-b9af-85a60c32a7c3', 'inst-128b2ef9-22ad-4b58-928f-039d2add2a19', 'inst-12ce625c-5494-473c-9ee9-c34c6308d677', 'inst-a05c7cb7-aabe-4eb9-af43-8be4347c48ad']
[   25.0s] row ids created (task work_ids): ['fa6faf38-e50c-4e73-a71a-11b1448dafab', '1e2f2477-f099-4cb4-8665-f945339b0b4c', 'c9edd00a-e84f-4cdc-a54a-3267c8054425', 'd2901123-13a8-4e87-921c-967f4c339f34', '69194d33-207f-4875-a100-bcaf0e9ae730']
[   25.0s] row ids created (pack ids): ['d3ad769e-09ec-4b96-864d-a6ee7eb7da2f', 'fdc9a7fc-600d-4511-9436-7d9e08bb25d9', 'a8936ec1-a94f-4d68-848e-5219a9e404b9']
[   25.0s] cleanup residue-zero: True
[   31.7s] app shutdown complete (lifespan exit)
[   31.7s] runtime: 31.7s
[   31.7s] RESULT: PASS
```
