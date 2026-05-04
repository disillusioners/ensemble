"""LightRAG API endpoint paths."""

# Text insertion
INSERT_TEXT = "/documents/text"
INSERT_TEXTS = "/documents/texts"

# Querying
QUERY = "/query"
QUERY_DATA = "/query/data"

# Graph operations
SEARCH_LABELS = "/graph/label/search"
GET_GRAPH = "/graphs"

# Entity operations
CREATE_ENTITY = "/graph/entity/create"
GET_ENTITY = "/graph/entity"
UPDATE_ENTITY = "/graph/entity/edit"
MERGE_ENTITIES = "/graph/entities/merge"
DELETE_ENTITY = "/graph/entity/delete"

# Relation operations
CREATE_RELATION = "/graph/relation/create"
DELETE_RELATION = "/documents/delete_relation"

# Document operations
DELETE_DOCS = "/documents/delete_document"
LIST_DOCS = "/documents/paginated"

# Status
TRACK_STATUS = "/documents/track_status/{track_id}"
PIPELINE_STATUS = "/documents/pipeline_status"
