# RAG Client & Tools Audit — LightRAG API Spec Alignment (2026-04-24)

## What was done
Audited and fixed all RAG code (schemas, endpoints, client, tools) to match the actual LightRAG OpenAPI spec. Found ~20 mismatches.

## Key Fixes
- InsertTextRequest: `description`/`file_paths` → `file_source` (single string)
- QueryRequest: mode default `"hybrid"` → `"mix"`, new fields for token control
- EntityCreateRequest/RelationCreateRequest: separate fields → `*_data` dict
- EntityMergeRequest: `entities_to_change`/`entity_to_change_into`
- DeleteDocsRequest: added `delete_file` + `delete_llm_cache`
- RelationUpdateRequest: `source_id`/`target_id` + `updated_data`
- Endpoints: entity/edit, entities/merge (plural!), documents/pipeline_status
- HTTP methods: DELETE for delete_entity/relation/docs
- LabelSearch: GET with query params, list_docs: POST for paginated
- file_source auto-generation in rag_insert_text tool

## Gotchas
- Merge endpoint: `/graph/entities/merge` (plural), create/edit: `/graph/entity/*` (singular)
- API spec fetched from: `http://lightrag.lightrag.svc.cluster.local:9621/openapi.json`
- Commit: 46a86a3 on feature/rag-knowledge-toolset
