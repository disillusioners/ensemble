# EVIDENCE MANIFEST — 2026-10-19 job-scoped answer resolution functional gate

Gate: `fix/job-scoped-answer-resolution` @ `829feb94d` (functional gate, PASS-WITH-NOTES).
Provenance: every file below is a byte-identical copy of a raw artifact produced in `/tmp/jaaf-p*/`.
Copy policy: contents unedited; sha256 of the copy == sha256 of the source (25/25 verified).

## Layout note

Files are grouped by leg under `p<N>/`, each keeping its ORIGINAL basename.
A single flat directory was NOT used because `junit.xml`, `output.txt` and
`failures*.txt` repeat across legs (p1-p6) and a flat copy would silently overwrite
all but the last leg's copy of each — losing evidence. Per-leg subdirectories keep
all 25 artifacts with original names and no data loss.

## File table (source path | sha256 | bytes)

| evidence path | source path | sha256 | bytes |
|---|---|---|---|
| `p1/junit.xml` | `/tmp/jaaf-p1/junit.xml` | `2d8d8d2652f8b251c62223acceaed4a60ccb419a10898221b79639b9c48dd720` | 37543 |
| `p1/junit_rerun.xml` | `/tmp/jaaf-p1/junit_rerun.xml` | `93a9b51bb2b321803fde1854a0075fc9b2807a93792408d5c5fba1254b947ed1` | 37544 |
| `p1/output.txt` | `/tmp/jaaf-p1/output.txt` | `76366644544a92cf189f7b4e6af3b9594b4196be5ff816f1c00df5d19008f547` | 26642 |
| `p1/output_rerun.txt` | `/tmp/jaaf-p1/output_rerun.txt` | `4f32341f167503bfc7e840740a2b4aca1a5095ed9fb17b74fd680d063419e3df` | 26643 |
| `p2/failures_failed_only.txt` | `/tmp/jaaf-p2/failures_failed_only.txt` | `e3a3c74a04e34fd4d1c2ea5476721106d4565a55ccc47c5cc5be722a08cd58cf` | 2286 |
| `p2/failures_failed_only_corrected.txt` | `/tmp/jaaf-p2/failures_failed_only_corrected.txt` | `fe9978b8586b2b870192d2f775dae2afc9233a1221aef21bd46098b11f068dbe` | 1876 |
| `p2/junit.xml` | `/tmp/jaaf-p2/junit.xml` | `907d33906e8eb688a3d602f9c88a6a77a21e5d1674cc68a9c76c68692cd7af1c` | 358996 |
| `p2/location_attributed_only.txt` | `/tmp/jaaf-p2/location_attributed_only.txt` | `feea05f2e75ae5d91ba4a9465b5a2344b739a1de7de343a8d57671a025694025` | 410 |
| `p2/output.txt` | `/tmp/jaaf-p2/output.txt` | `ce185e1f13b0e16ec3bed0a5798cd7454555b0fdca49756d09a771d8e677c809` | 73238 |
| `p3/failures.txt` | `/tmp/jaaf-p3/failures.txt` | `fd1eab8253241425f235627a229b8c93f397e3e2194dd6a3e679300a3cc8f10b` | 116 |
| `p3/junit.xml` | `/tmp/jaaf-p3/junit.xml` | `4748587f2e81536816c10c17e4cd6db34e57fbb928e779894491b3f2017b388d` | 34658 |
| `p3/output.txt` | `/tmp/jaaf-p3/output.txt` | `d4b8c69d35ec3bd313dad97a59d78f14798329d4c00237d1999230e7da42a2f0` | 4285 |
| `p4/junit.xml` | `/tmp/jaaf-p4/junit.xml` | `09bd7428edc2135cde3b0dc0bc22f663f2986680762a1429b5d425555805b771` | 7541 |
| `p4/nodes.txt` | `/tmp/jaaf-p4/nodes.txt` | `9cfebbec618f9eea7c1856575ecc9de4fcfeb924796adbb096331004ab384326` | 6222 |
| `p5/failures.txt` | `/tmp/jaaf-p5/failures.txt` | `fd1eab8253241425f235627a229b8c93f397e3e2194dd6a3e679300a3cc8f10b` | 116 |
| `p5/junit.xml` | `/tmp/jaaf-p5/junit.xml` | `00af3762461ba1e739757b29cb0cbbc32d5bbfc85c1fbe0d1ed4d101c028bd7e` | 34658 |
| `p5/output.txt` | `/tmp/jaaf-p5/output.txt` | `024fccc50f9d0aa9bf5be1a11c6a82d73f10fe247fee80cb988191260f68dc4f` | 3911 |
| `p6/failures_failed_only.txt` | `/tmp/jaaf-p6/failures_failed_only.txt` | `fe9978b8586b2b870192d2f775dae2afc9233a1221aef21bd46098b11f068dbe` | 1876 |
| `p6/junit.xml` | `/tmp/jaaf-p6/junit.xml` | `912cfa5a56921e9122207f68b2e31c532d4734c4e011d77ec20c7bf3844d693b` | 363558 |
| `p6/output.txt` | `/tmp/jaaf-p6/output.txt` | `e301fc5ea149f6c5fc79ab89e9e72d57da5af88c6f3e0133b6a846d3509ccc11` | 70043 |
| `p8/pg_branch_check.py` | `/tmp/jaaf-p8/pg_branch_check.py` | `79ac9f50e4905532646b23737589dd1dbac03d507c21d35b5ee5306e7496aa33` | 30792 |
| `p9/evidence.md` | `/tmp/jaaf-p9/evidence.md` | `44c54c56057e9931ca5b83c5eae6cb300ec76500f198718e0d70020d59d41304` | 12319 |
| `p9/run-smoke.sh` | `/tmp/jaaf-p9/run-smoke.sh` | `a4a159f76c631eafc7fc615a4d0771d38b3858a2948c83fda7f1171e7a6e469e` | 2432 |
| `p9/run2-full.log` | `/tmp/jaaf-p9/run2-full.log` | `3e55f7df18bc5e30ae69e7710ef6bb03f116a43f6fcd68523de172d1d4db99be` | 57718 |
| `p9/smoke.py` | `/tmp/jaaf-p9/smoke.py` | `5f1db49c77054db229905d08ac1305c1925fc85c6ad532570458186bedcb4488` | 18957 |

## Not copied (present in /tmp, absent from the copy list — left in place, not deleted)

| source path | bytes | note |
|---|---|---|
| `/tmp/jaaf-p1/overlay_stash.py` | 69947 | not in the copy list; retained in /tmp |
| `/tmp/jaaf-p2/probe_symlink.txt` | 1598 | not in the copy list; retained in /tmp |
| `/tmp/jaaf-p4/pack.log` | 611 | not in the copy list; retained in /tmp |
| `/tmp/jaaf-p6/tools_base.txt` | 677 | not in the copy list; retained in /tmp |
| `/tmp/jaaf-p6/tools_head.txt` | 677 | not in the copy list; retained in /tmp |
| `/tmp/jaaf-p9/smoke-stdout.log` | 57539 | not in the copy list; retained in /tmp |

## Notes

- `p9/run2-full.log` included: 57,718 bytes (<= 2 MB cap). No file in this directory exceeds 5 MB, so no `.truncated` variants were produced.
- Raw leg semantics (which leg proves what) are documented in `.agents/tester/RESULTS/2026-10-19-job-scoped-answer-resolution-functional-gate.md`.
