-- Run this in the Supabase SQL editor (Project → SQL Editor → New query)
-- before starting the bot. Requires the pgvector extension for embeddings.
-- 
-- This schema includes Row-Level Security (RLS) policies to enforce
-- permissions at the database level.

create extension if not exists vector;

-- Enable RLS for all tables (required before defining policies)
ALTER TABLE IF EXISTS group_chat_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS group_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS active_polls ENABLE ROW LEVEL SECURITY;

-- ═══════════════════════════════════════════════════════════════════
-- TABLE 1: group_chat_logs
-- Raw group chat history, used for /summarize and personality lookups.
-- ═══════════════════════════════════════════════════════════════════

create table if not exists group_chat_logs (
    id          bigint generated always as identity primary key,
    chat_id     bigint not null,
    sender      text not null,
    message     text not null,
    created_at  timestamptz not null default now()
);

CREATE INDEX IF NOT EXISTS idx_group_chat_logs_chat_id ON group_chat_logs(chat_id);
CREATE INDEX IF NOT EXISTS idx_group_chat_logs_created_at ON group_chat_logs(created_at DESC);

-- RLS: Service role (bot) can insert/read. Anonymous cannot.
-- This ensures only the authenticated bot server writes to this table.
CREATE POLICY "Service role can insert group chat logs" ON group_chat_logs
    FOR INSERT WITH CHECK (auth.role() = 'service_role');

CREATE POLICY "Service role can read group chat logs" ON group_chat_logs
    FOR SELECT USING (auth.role() = 'service_role');

-- ═══════════════════════════════════════════════════════════════════
-- TABLE 2: group_embeddings
-- Vector embeddings of group messages, used for semantic memory search.
-- Requires pgvector extension.
-- ═══════════════════════════════════════════════════════════════════

create table if not exists group_embeddings (
    id          bigint generated always as identity primary key,
    chat_id     bigint not null,
    sender      text not null,
    message     text not null,
    embedding   vector(768) not null,
    created_at  timestamptz not null default now()
);

CREATE INDEX IF NOT EXISTS idx_group_embeddings_chat_id ON group_embeddings(chat_id);
CREATE INDEX IF NOT EXISTS idx_group_embeddings_vector ON group_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- RLS: Service role only (no anonymous access to embeddings).
CREATE POLICY "Service role can insert embeddings" ON group_embeddings
    FOR INSERT WITH CHECK (auth.role() = 'service_role');

CREATE POLICY "Service role can read embeddings" ON group_embeddings
    FOR SELECT USING (auth.role() = 'service_role');

-- ═══════════════════════════════════════════════════════════════════
-- TABLE 3: active_polls
-- Live inline-keyboard polls with a 5-minute expiry window.
-- Supports voting by both service role (create/cleanup) and anon (vote).
-- ═══════════════════════════════════════════════════════════════════

create table if not exists active_polls (
    poll_id     text primary key,
    chat_id     bigint not null,
    question    text not null,
    options     jsonb not null,
    votes       jsonb not null default '{}'::jsonb,
    expires_at  timestamptz not null,
    created_at  timestamptz not null default now()
);

CREATE INDEX IF NOT EXISTS idx_active_polls_chat_id ON active_polls(chat_id);
CREATE INDEX IF NOT EXISTS idx_active_polls_expires_at ON active_polls(expires_at);

-- RLS: Service role can insert/delete (create polls, cleanup expired).
CREATE POLICY "Service role can create polls" ON active_polls
    FOR INSERT WITH CHECK (auth.role() = 'service_role');

CREATE POLICY "Service role can delete polls" ON active_polls
    FOR DELETE USING (auth.role() = 'service_role');

-- Anonymous (client) can only READ and UPDATE poll votes.
-- This is safe because they can only see/modify vote counts, not internal state.
CREATE POLICY "Anonymous can read active polls" ON active_polls
    FOR SELECT USING (auth.role() = 'anon' AND expires_at > now());

CREATE POLICY "Anonymous can vote on polls" ON active_polls
    FOR UPDATE USING (auth.role() = 'anon' AND expires_at > now())
    WITH CHECK (auth.role() = 'anon' AND expires_at > now');

-- ═══════════════════════════════════════════════════════════════════
-- RPC FUNCTIONS
-- ═══════════════════════════════════════════════════════════════════

-- match_chat_embeddings: RPC for semantic memory search.
-- Callable only by service role (server-side only).
create or replace function match_chat_embeddings(
    query_embedding vector(768),
    match_threshold float,
    match_count int,
    filter_chat_id bigint
)
returns table (
    sender text,
    message text,
    similarity float
)
language sql stable
security definer  -- Executes as the owner (postgres), bypassing RLS
as $$
    select
        sender,
        message,
        1 - (embedding <=> query_embedding) as similarity
    from group_embeddings
    where chat_id = filter_chat_id
      and 1 - (embedding <=> query_embedding) > match_threshold
    order by embedding <=> query_embedding
    limit match_count;
$$;

-- Grant execution to service role only.
REVOKE ALL ON FUNCTION match_chat_embeddings FROM public, anon;
GRANT EXECUTE ON FUNCTION match_chat_embeddings TO service_role;
