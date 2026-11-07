-- Template RLS policy for an event-scoped table. Replace :table with the table name.
-- The app sets the event per transaction: SET LOCAL app.event_id = '<uuid>';
-- If app.event_id is unset, current_setting(..., true) returns NULL and no rows match.

ALTER TABLE :table ENABLE ROW LEVEL SECURITY;
ALTER TABLE :table FORCE ROW LEVEL SECURITY;

CREATE POLICY :table_event_isolation ON :table
    USING (event_id = current_setting('app.event_id', true)::uuid)
    WITH CHECK (event_id = current_setting('app.event_id', true)::uuid);
