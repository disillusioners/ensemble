# The 8 GB reference profile — running Ensemble with a local PostgreSQL

**Audience:** anyone running Agents Ensemble on a small machine — a mini PC, an old
laptop, a single small cloud box — with PostgreSQL on the same host.

**The short version:** an 8 GB machine is enough, and here is exactly how the memory is
split, what Postgres should be told, and what the Ensemble does *not* need. If you only
read one section, read [§2](#2-the-split-the-numbers) and
[§3](#3-what-the-ensemble-does-not-need).

Every number below was measured on a live install (a busy, multi-agent one) or is
arithmetic over those measurements. Nothing here is a guess about your workload.

---

## 1. Why this page exists

8 GB is the size of the machine that most people actually have. If Ensemble cannot run
there, it cannot run where the people who need it are.

The awkward part is that "it runs" is not the same as "it is configured well". A default
PostgreSQL install plus a default container limit can look fine on a 16 GB box and quietly
consume half of an 8 GB box — mostly in ways that do not make anything faster.

This page is the profile we intend to support: one 8 GB machine, one local PostgreSQL, one
Ensemble daemon, no redundancy.

---

## 2. The split — the numbers

Total: **8192 MiB** of RAM.

| Who | RAM | What it is |
|---|---|---|
| **PostgreSQL** | **~940 MiB** that cannot be reclaimed, inside a **2 GiB** cap | 512 MiB of database cache + ~250 MiB of live connections + 128 MiB for vacuum/index work + ~50 MiB of WAL buffers and locks |
| **The Ensemble daemon** | **~1.5 GiB** (measured: 1.0 GiB anonymous + 0.5 GiB kernel slabs on a busy install) | the daemon, its workers, the LangGraph runtime |
| **The operating system** | **~1 GiB** | kernel, services, the disk driver |
| **Left over — the page cache** | **~3–4 GiB** | the OS's copy of recently-read files. This is *not* waste: it is the free speed. It can be dropped at any moment, so the system never stalls waiting for it. |

Two things follow from that table:

* **Postgres' cap is 2 GiB, not 4 GiB.** A 4 GiB cap sounds generous until you notice what
  fills it: on the measured install, 3.67 GiB of the 4 GiB was the operating system's
  file cache — memory the OS could reclaim instantly, and did. The database itself was
  using ~700 MiB.
* **The page cache is the point of a small box.** PostgreSQL leans on the OS cache to serve
  reads. On an 8 GB machine, giving Postgres a 4 GiB cap mostly moves the OS cache out of
  the way; giving it ~1 GiB inside a 2 GiB cap keeps the cache useful.

### The one rule behind the numbers

**Never raise a ceiling to make a symptom go away.** If Postgres is being throttled, the
answer is to give it the *right* amount of memory, not more of it. Every number in the
table above was set by measuring what the workload actually does.

---

## 3. What the Ensemble does NOT need

This is the honest list. Most of it is memory and disk that a default install asks for and
never uses.

* **It does not need 2 GB of database cache.** The whole working set of the measured
  database — every table's data plus every index — was 719 MiB + 478 MiB. The large
  attachments stored in the database (tens of gigabytes on the measured install) *stream*
  through the cache; they do not sit in it. **512 MiB is enough**, and 2 GB was pure
  waste.
* **It does not need a fat memory cap on Postgres.** Its real footprint is ~700 MiB. The
  rest of an old 4 GiB cap was the OS's file cache.
* **It does not need hundreds of database connections.** A busy install used **37**
  connections (28 of them idle). An idle connection costs 1–2 MB. The number of
  connections is *not* where the memory goes.
* **It does not need big per-query memory.** `work_mem` is the memory one sort or hash
  operation may use. This workload's queries are small; **4 MB** is plenty. Note this is
  the setting that actually bounds query memory (connections × sort nodes × `work_mem`) —
  not the connection count.
* **It does not need a large WAL.** Postgres needs ~2 GB of disk for WAL between
  checkpoints — that is *disk*, not RAM, and it is a good setting to keep.
* **It does not need the dead attachment data.** On the measured install, the big
  attachment table had **23 GB of dead space** (94 % of it free after a retention sweep).
  That is disk, not memory, and it can be reclaimed with a repack — your call, on your
  maintenance window.
* **It does not need redundancy.** That is the point of this profile. The cost of no
  redundancy is the restore time, which is [§6](#6-the-restore-time-the-number-this-profile-rests-on).

---

## 4. What to tell PostgreSQL

For a **local** PostgreSQL on the same box, put these in `postgresql.conf`
(`/etc/postgresql/<version>/main/postgresql.conf` on Debian/Ubuntu, or the file your
installation reads — check with `SHOW config_file`):

```conf
# ─── memory (the point of this page) ───
shared_buffers = '512MB'        # 6% of 8 GB — holds the working set
work_mem = '4MB'                # per sort/hash operation
maintenance_work_mem = '64MB'   # vacuum, CREATE INDEX
max_connections = 100           # ~37 in use on a busy install; an idle one costs ~1-2 MB
```

Then **restart** PostgreSQL (`shared_buffers` only changes at a restart) and check that
the setting really took:

```sql
SHOW shared_buffers;                        -- expect 512MB
SELECT name, setting, source, sourcefile     -- sourcefile tells you WHO set it
FROM pg_settings WHERE name = 'shared_buffers';
```

**Read `sourcefile`.** If it says `postgresql.auto.conf` instead of your
`postgresql.conf`, something set it behind your back and your file is being ignored —
see [§5](#5-when-postgres-is-not-a-pod-your-file-can-lie-to-you).

### A note on `effective_cache_size`

PostgreSQL also keeps a *hint* about how much RAM is available for caching, so its query
planner can make sensible choices. Its default (4 GB) is wrong on an 8 GB box; ~2 GB is
honest. But `effective_cache_size` **changes which queries the planner picks**, so set it
deliberately, as its own change, and watch the query timings afterwards. We deliberately do
not set it for you.

---

## 5. When Postgres is not a pod: your file can lie to you

If you run PostgreSQL in Docker, Kubernetes, or any setup that has a *data directory* and
a *configuration file*, read this. It is the single most common way an 8 GB box ends up
using 2 GB of cache "for no reason".

PostgreSQL reads **two** files, in this order, and **the second one wins**:

1. `postgresql.conf` — your configuration file (in Docker/Kubernetes, this is usually
   mounted or generated by the deployment definition);
2. `postgresql.auto.conf` — in the *data directory*, written by `ALTER SYSTEM`.

So if anybody ever ran `ALTER SYSTEM SET shared_buffers = '2GB'` — or your setup tool did
it for you — that value now overrides your configuration file. Your deployment says 512 MB;
Postgres runs 2 GB; `helm get values` shows nothing wrong. **The chart was lying.**

Check which one is in charge:

```sql
SELECT name, current_setting(name), source, sourcefile
FROM pg_settings
WHERE name IN ('shared_buffers','work_mem','maintenance_work_mem','max_connections');
```

* `sourcefile = .../postgresql.conf` → your file is in charge. Good.
* `sourcefile = .../postgresql.auto.conf` → the data directory is in charge. Your file is
  being ignored.

The fix is to clear the override, not to edit your file harder:

```sql
ALTER SYSTEM RESET shared_buffers;
-- restart Postgres; the value now comes from postgresql.conf
```

**Make it automatic.** `ALTER SYSTEM RESET` is a no-op when the setting was never
overridden, so it is safe to run every time the database starts. In Kubernetes the
Bitnami chart does this with a pre-init script
(`primary.preInitDb.scripts`) that runs before the server starts, on every pod start —
because the override lives in the data volume, a release-time hook alone would leave it in
place after a restart or a reschedule. A worked example, with the measurements and the
proof that the order is right, is in the operator's chart repository
(`infranstructure/k8s/postgres/README-pg-memory-profile.md`).

If you are not using Kubernetes: run `ALTER SYSTEM RESET …` in whatever script starts
PostgreSQL, then restart it.

---

## 6. The restore time — the number this profile rests on

**This number was not measured. It is an estimate, and we say so plainly.**

With no redundancy, the box *is* the backup. Its restore time is what decides whether a
bad afternoon is an annoyance or an outage. So it deserves a real number, and this page
does not have one yet.

**What we can measure, and what it implies.** On the measured install:

| Measurement | Value |
|---|---|
| A full dump of the live database | 3.60 GiB (before a retention sweep removed old attachments: 11.99 GiB) |
| Compressed | 0.52 GiB (zstd -3) |
| Time to take that dump (11.99 GiB case) | **237 s** → ~54 MB/s of plain output |
| The 11.99 GiB artifact itself | `pg_dumpall-2026-11-25-03-48.pgdump.zst`, 554,023,327 B — still on the backup volume (retention is off) |
| Live connections / tables / indexes | 37 / 115 / 394 |

And the machine's I/O, measured directly (this host — a server; a mini PC is slower):

| Measurement | Value |
|---|---|
| Sequential write + flush (`dd conv=fdatasync`) | **175 MB/s** |
| Cold read (page cache bypassed) | 183 MB/s |
| `zstd -3` compress / decompress | 610 MB/s / **458 MB/s** |

Restoring is roughly the mirror image of dumping: read the dump, write the data, rebuild
394 indexes, write the WAL. Put the numbers together for an 11.99 GiB dump:

* decompress 12.9 GB → ~28 s;
* the server writes ~12.9 GB of data, ~0.5 GB of indexes, and ~25 GB of WAL
  (`full_page_writes` roughly doubles the WAL) → **~38 GB of writes** → ~220 s of I/O floor
  at 175 MB/s;
* one `psql` stream parses and loads it, the server re-compresses the ~10 GB of
  attachments, and 394 indexes are rebuilt → ~3–5 minutes of CPU.

Overlapping, that is **5–9 minutes on this host**. A typical 8 GB mini PC has a slower
sustained write rate and the same class of CPU, which puts it at:

> **Estimate: 15–25 minutes for an 11.99 GiB dump. Plan for 20.**
> Uncertain by roughly 2× — this is arithmetic over measured dump and I/O rates, not a
> restore that was actually timed. The 5–9 minute figure above is the floor on
> server-class hardware, not what a mini PC will do.

**Disk, not just time.** A restore needs room for both the dump and the database it
builds: **~25–30 GiB free** during the restore for an 11.99 GiB dump (12 GiB of data +
~1.2 GiB of indexes + the dump file + WAL). On a small box that is often the binding
constraint, not the clock.

**How to measure it yourself, when you want a real number.** Do it on a scratch machine or
a scratch volume — never on the box that holds the live database:

```bash
# 1. create a scratch database on the scratch instance
psql -h <scratch-host> -p 5432 -U postgres -c 'CREATE DATABASE scratch_restore;'

# 2. restore into it, timed (the operator's repository ships this as
#    infranstructure/k8s/postgres/verify/measure-restore-time.sh -- it refuses
#    any target database not named scratch_*)
bash measure-restore-time.sh --dump pg_dumpall-2026-11-25-03-48.pgdump.zst \
     --host <scratch-host> --port 5432 --user postgres --db scratch_restore
```

If your host is slow, add `pv` around the pipe for a progress bar. Do it once, on the
same hardware you will actually restore onto — a restore timed on a different machine is
not the number you need.

**Until that number is measured, treat 20 minutes as the planning figure** and keep a
known-good dump somewhere you can reach it from.

---

## 7. Checklist for a new 8 GB box

- [ ] Postgres: `shared_buffers = 512MB`, `work_mem = 4MB`, `maintenance_work_mem = 64MB`, `max_connections = 100` in `postgresql.conf`.
- [ ] Postgres restarted, and `SHOW shared_buffers` says `512MB` with `sourcefile` = your `postgresql.conf` (not `postgresql.auto.conf`).
- [ ] The reset step (`ALTER SYSTEM RESET …`) runs every time Postgres starts, so nobody's stray `ALTER SYSTEM` can quietly take the memory back.
- [ ] Postgres's memory cap (if it is in a container) is ~2 GiB, with a request of ~1 GiB.
- [ ] ~30 GiB of free disk for the database *and* a restore to fit in.
- [ ] A backup that actually runs (the daemon's own retention sweep keeps the live data
      smaller; a nightly `pg_dumpall` gives you the restore path).
- [ ] The restore time measured on *your* hardware, once — §6.

---

## 8. Where the numbers came from

The measurements behind this page were taken on a live, busy install on 2026-11-26:
PostgreSQL 18.4 (chart 18.8.0), a 30 GB database with 115 tables and 394 indexes, 37 live
connections, container limit 4 GiB, `memory.events` showing `max 3197` and `oom_kill 0`
(throttled, never killed), and `shared_buffers` at 2 GB with `sourcefile =
postgresql.auto.conf` — invisible to Helm. The operator-side chart change, the pod-start
reset step, and the proof that the reset lands before the server reads its configuration
are in `infranstructure/k8s/postgres/README-pg-memory-profile.md` in the operator's
repository.
