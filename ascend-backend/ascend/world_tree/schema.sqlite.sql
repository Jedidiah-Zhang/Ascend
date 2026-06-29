-- 事件归档 Schema (SQLite)
-- DDL 使用标准 SQL 语法，幂等性由 Python 层保证

CREATE TABLE events (
    id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL,
    chunk_x INTEGER NOT NULL,
    chunk_y INTEGER NOT NULL,
    tile_x INTEGER,
    tile_y INTEGER,
    initiator_type TEXT NOT NULL,
    initiator_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    data_json TEXT DEFAULT '{}',
    caused_by_json TEXT DEFAULT '[]',
    observes TEXT,
    co_participants_json TEXT DEFAULT '[]',
    affected_json TEXT DEFAULT '[]'
);

CREATE TABLE event_entities (
    event_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (event_id, entity_id),
    FOREIGN KEY (event_id) REFERENCES events(id)
);

CREATE INDEX idx_events_time
    ON events(timestamp);
CREATE INDEX idx_events_initiator
    ON events(initiator_id);
CREATE INDEX idx_events_chunk
    ON events(chunk_x, chunk_y);
CREATE INDEX idx_events_type
    ON events(event_type);
CREATE INDEX idx_event_entities_entity
    ON event_entities(entity_id);
