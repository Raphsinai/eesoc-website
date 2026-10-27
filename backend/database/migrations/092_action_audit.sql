-- Immutable attribution for committee changes. Reusable for other entities.
CREATE TABLE IF NOT EXISTS public.action_audit (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 actor_user_id BIGINT REFERENCES public.users(id) ON DELETE SET NULL,
 action VARCHAR NOT NULL,
 entity_type VARCHAR NOT NULL,
 entity_id BIGINT NOT NULL,
 before_data JSONB,
 after_data JSONB
);
CREATE INDEX IF NOT EXISTS action_audit_entity_idx ON public.action_audit(entity_type, entity_id, at DESC);
CREATE INDEX IF NOT EXISTS action_audit_actor_idx ON public.action_audit(actor_user_id, at DESC);
-- Recorded in the same transaction as a change, never asynchronously.
