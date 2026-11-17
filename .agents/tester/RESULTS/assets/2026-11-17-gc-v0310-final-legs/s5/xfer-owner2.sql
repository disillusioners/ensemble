-- functions / aggregates / procedures
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid, p.proname, pg_get_function_identity_arguments(p.oid) AS args
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname='public' AND pg_get_userbyid(p.proowner) <> 'ensemble_dev_app'
  LOOP
    EXECUTE format('ALTER FUNCTION public.%I(%s) OWNER TO ensemble_dev_app', r.proname, r.args);
  END LOOP;
END $$;
-- views / materialized views / foreign tables
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.relname, c.relkind
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname='public' AND pg_get_userbyid(c.relowner) <> 'ensemble_dev_app'
      AND c.relkind IN ('v','m','f')
  LOOP
    EXECUTE format('ALTER %s public.%I OWNER TO ensemble_dev_app',
      CASE r.relkind WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'VIEW' END, r.relname);
  END LOOP;
END $$;
-- types (enums / composites) owned by others
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT t.typname
    FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname='public' AND pg_get_userbyid(t.typowner) <> 'ensemble_dev_app'
      AND t.typtype IN ('e','c')
  LOOP
    EXECUTE format('ALTER TYPE public.%I OWNER TO ensemble_dev_app', r.typname);
  END LOOP;
END $$;
