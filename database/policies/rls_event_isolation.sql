-- RLS policy pattern used for every event-scoped table (see migration 0001).
-- The app sets the event per transaction: SELECT set_config('app.event_id', '<uuid>', true);
-- If app.event_id is unset or empty, the expression is NULL and no rows match.
-- FORCE makes the policy bind the table owner (the facescan role) as well.

ALTER TABLE :table ENABLE ROW LEVEL SECURITY;
ALTER TABLE :table FORCE ROW LEVEL SECURITY;

CREATE POLICY :table_event_isolation ON :table
    USING (event_id = nullif(current_setting('app.event_id', true), '')::uuid)
    WITH CHECK (event_id = nullif(current_setting('app.event_id', true), '')::uuid);
