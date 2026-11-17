DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.relname
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname='public' AND pg_get_userbyid(c.relowner) <> 'ensemble_dev_app'
      AND c.relkind IN ('r','p')
  LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO ensemble_dev_app', r.relname);
  END LOOP;
END $$;
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.relname
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname='public' AND pg_get_userbyid(c.relowner) <> 'ensemble_dev_app'
      AND c.relkind = 'S'
      AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=c.oid AND d.deptype='a')
  LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO ensemble_dev_app', r.relname);
  END LOOP;
END $$;
