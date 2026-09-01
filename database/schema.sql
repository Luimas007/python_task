-- =====================================================================
--  Samsung Phone Knowledge Base  --  PostgreSQL 18
--  This database is the ONLY knowledge source the system may use at
--  query time. Missing facts are stored as SQL NULL. Nothing is guessed.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- runs
CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id          BIGSERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    source          TEXT        NOT NULL DEFAULT 'gsmarena.com',
    pages_fetched   INTEGER     NOT NULL DEFAULT 0,
    pages_failed    INTEGER     NOT NULL DEFAULT 0,
    notes           TEXT
);

-- -------------------------------------------------------------- phones
CREATE TABLE IF NOT EXISTS phones (
    phone_id         SERIAL PRIMARY KEY,
    slug             TEXT NOT NULL UNIQUE,
    gsmarena_id      INTEGER,
    brand            TEXT NOT NULL DEFAULT 'Samsung',
    model_name       TEXT NOT NULL,
    short_name       TEXT,
    series           TEXT,
    generation       INTEGER,
    tier             TEXT,
    form_factor      TEXT,
    is_flagship      BOOLEAN NOT NULL DEFAULT FALSE,
    popularity_rank  INTEGER,
    popularity_hits  BIGINT,
    popularity_pct   NUMERIC(6,2),
    fan_count        INTEGER,
    source_url       TEXT NOT NULL,
    local_page_path  TEXT,
    page_sha256      TEXT,
    page_bytes       INTEGER,
    scraped_at       TIMESTAMPTZ,
    scrape_run_id    BIGINT REFERENCES scrape_runs(run_id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_phones_model_trgm ON phones USING gin (model_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_phones_short_trgm ON phones USING gin (short_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_phones_series      ON phones (series);
CREATE INDEX IF NOT EXISTS idx_phones_rank        ON phones (popularity_rank);

-- ------------------------------------------------------ specifications
-- Verbatim key/value capture of every spec row on the source page.
-- A row whose spec_value IS NULL records that the page did not publish it.
CREATE TABLE IF NOT EXISTS specifications (
    spec_id     BIGSERIAL PRIMARY KEY,
    phone_id    INTEGER NOT NULL REFERENCES phones(phone_id) ON DELETE CASCADE,
    category    TEXT    NOT NULL,
    spec_key    TEXT    NOT NULL,
    spec_code   TEXT,
    spec_value  TEXT,
    position    INTEGER NOT NULL DEFAULT 0,
    is_absent   BOOLEAN GENERATED ALWAYS AS (spec_value IS NULL) STORED,
    UNIQUE (phone_id, category, spec_key, position)
);

CREATE INDEX IF NOT EXISTS idx_specs_phone    ON specifications (phone_id);
CREATE INDEX IF NOT EXISTS idx_specs_code     ON specifications (spec_code);
CREATE INDEX IF NOT EXISTS idx_specs_category ON specifications (phone_id, category);

-- ---------------------------------------------------- phone_attributes
-- Typed projection of the verbatim specs, used for ranking and comparison.
-- Every column is nullable on purpose.
CREATE TABLE IF NOT EXISTS phone_attributes (
    phone_id                 INTEGER PRIMARY KEY REFERENCES phones(phone_id) ON DELETE CASCADE,

    announced_text           TEXT,
    announced_date           DATE,
    release_status           TEXT,
    release_date             DATE,

    display_size_in          NUMERIC(4,2),
    display_type             TEXT,
    display_resolution       TEXT,
    display_width_px         INTEGER,
    display_height_px        INTEGER,
    display_refresh_hz       INTEGER,
    display_ppi              INTEGER,
    display_protection       TEXT,
    peak_brightness_nits     INTEGER,

    dimensions_text          TEXT,
    height_mm                NUMERIC(6,2),
    width_mm                 NUMERIC(6,2),
    thickness_mm             NUMERIC(6,2),
    weight_g                 NUMERIC(6,1),
    build_text               TEXT,
    ip_rating                TEXT,
    sim_text                 TEXT,

    os_launch                TEXT,
    android_version_launch   NUMERIC(4,1),
    os_updates_promised      INTEGER,
    chipset                  TEXT,
    chipset_vendor           TEXT,
    fabrication_nm           NUMERIC(4,1),
    cpu                      TEXT,
    gpu                      TEXT,

    card_slot                BOOLEAN,
    internal_memory_text     TEXT,
    ram_options_gb           INTEGER[],
    max_ram_gb               INTEGER,
    storage_options_gb       INTEGER[],
    max_storage_gb           INTEGER,

    main_camera_setup        TEXT,
    main_camera_mp           NUMERIC(6,1),
    main_camera_modules      TEXT,
    main_camera_features     TEXT,
    main_camera_video        TEXT,
    max_video_resolution     TEXT,

    selfie_camera_setup      TEXT,
    selfie_camera_mp         NUMERIC(6,1),
    selfie_camera_modules    TEXT,
    selfie_camera_video      TEXT,

    battery_type             TEXT,
    battery_capacity_mah     INTEGER,
    charging_text            TEXT,
    charging_wired_w         NUMERIC(6,1),
    charging_wireless_w      NUMERIC(6,1),
    reverse_wireless_w       NUMERIC(6,1),
    battery_endurance_hours  NUMERIC(6,1),

    network_technology       TEXT,
    has_5g                   BOOLEAN,
    wlan                     TEXT,
    bluetooth_version        NUMERIC(4,1),
    has_nfc                  BOOLEAN,
    has_fm_radio             BOOLEAN,
    has_headphone_jack       BOOLEAN,
    usb_text                 TEXT,
    sensors_text             TEXT,
    has_stereo_speakers      BOOLEAN,

    colors_text              TEXT,
    model_codes              TEXT,
    price_text               TEXT,
    price_eur                NUMERIC(10,2),
    price_usd                NUMERIC(10,2),
    price_inr                NUMERIC(12,2),
    antutu_score             INTEGER,
    geekbench_score          NUMERIC(10,1),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------- knowledge_chunks
-- RAG corpus. Text is generated FROM the rows above, so retrieval can never
-- surface a fact that is not already in this database. Embeddings live here
-- too: there is no external vector store.
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id        BIGSERIAL PRIMARY KEY,
    phone_id        INTEGER REFERENCES phones(phone_id) ON DELETE CASCADE,
    section         TEXT NOT NULL,
    heading         TEXT,
    content         TEXT NOT NULL,
    char_len        INTEGER NOT NULL,
    embedding       REAL[],
    embedding_model TEXT,
    embedded_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunks_phone   ON knowledge_chunks (phone_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section ON knowledge_chunks (section);
CREATE INDEX IF NOT EXISTS idx_chunks_content_trgm
    ON knowledge_chunks USING gin (content gin_trgm_ops);

-- ------------------------------------------------------------ audit log
CREATE TABLE IF NOT EXISTS query_log (
    log_id      BIGSERIAL PRIMARY KEY,
    run_id      TEXT,
    agent       TEXT,
    protocol    TEXT,
    operation   TEXT,
    statement   TEXT,
    params      JSONB,
    row_count   INTEGER,
    duration_ms NUMERIC(10,2),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_query_log_run ON query_log (run_id);

-- --------------------------------------------------------- conversation
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id BIGSERIAL PRIMARY KEY,
    session_key     TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_session ON conversations (session_key);

CREATE TABLE IF NOT EXISTS messages (
    message_id      BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    run_id          TEXT,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content         TEXT NOT NULL,
    intent          TEXT,
    agents_used     TEXT[],
    grounding       JSONB,
    latency_ms      NUMERIC(10,2),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages (conversation_id, message_id);

-- ============================ in-database vector search ================
-- pgvector is not installed on this server, so similarity is implemented as
-- a set-based SQL function. Retrieval therefore executes inside PostgreSQL
-- and the knowledge base stays entirely self-contained.
CREATE OR REPLACE FUNCTION cosine_similarity(a REAL[], b REAL[])
RETURNS DOUBLE PRECISION
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $fn$
    SELECT CASE
             WHEN s.na = 0 OR s.nb = 0 THEN 0::double precision
             ELSE s.dot / (sqrt(s.na) * sqrt(s.nb))
           END
    FROM (
        SELECT sum(x.v::double precision * y.v::double precision) AS dot,
               sum(x.v::double precision * x.v::double precision) AS na,
               sum(y.v::double precision * y.v::double precision) AS nb
        FROM unnest(a) WITH ORDINALITY AS x(v, i)
        JOIN unnest(b) WITH ORDINALITY AS y(v, i) USING (i)
    ) s;
$fn$;

-- ------------------------------------------------------------ views ---
CREATE OR REPLACE VIEW v_phone_overview AS
SELECT p.phone_id,
       p.slug,
       p.model_name,
       p.short_name,
       p.series,
       p.tier,
       p.is_flagship,
       p.popularity_rank,
       a.release_date,
       a.display_size_in,
       a.display_type,
       a.display_refresh_hz,
       a.chipset,
       a.max_ram_gb,
       a.max_storage_gb,
       a.main_camera_mp,
       a.selfie_camera_mp,
       a.battery_capacity_mah,
       a.charging_wired_w,
       a.battery_endurance_hours,
       a.weight_g,
       a.price_eur,
       a.price_usd
FROM phones p
LEFT JOIN phone_attributes a USING (phone_id);

CREATE OR REPLACE VIEW v_coverage AS
SELECT p.phone_id,
       p.model_name,
       count(s.spec_id)                                         AS spec_rows,
       count(s.spec_id) FILTER (WHERE s.spec_value IS NOT NULL) AS spec_present,
       count(s.spec_id) FILTER (WHERE s.spec_value IS NULL)     AS spec_null,
       (SELECT count(*) FROM knowledge_chunks k WHERE k.phone_id = p.phone_id) AS chunks
FROM phones p
LEFT JOIN specifications s USING (phone_id)
GROUP BY p.phone_id, p.model_name;
