#!/usr/bin/env python3
"""Mock test: Ensure System Queues endpoint against running dev server."""
import sys
import signal
import httpx
import time

BASE_URL = "http://localhost:8079"
TIMEOUT = 60

# System queue definitions (5 system queues)
SYSTEM_QUEUES = {
    "system_fifo_queue": {"type": "fifo", "concurrency": 1},
    "system_parallel_queue": {"type": "parallel", "concurrency": 5},
    "system_kb_fifo_queue": {"type": "fifo", "concurrency": 1},
    "system_defer_queue": {"type": "defer", "concurrency": 1},
    "system_background_queue": {"type": "background", "concurrency": 1},
}


# Timeout enforcement
def _timeout_handler(signum, frame):
    print("RESULT: TIMEOUT")
    sys.exit(124)


signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(TIMEOUT)


def test_scenario(name: str, passed: int, failed: int, result: bool, detail: str = ""):
    """Record test result and print."""
    if result:
        passed += 1
        status = "PASS"
    else:
        failed += 1
        status = "FAIL"

    detail_str = f" - {detail}" if detail else ""
    print(f"  [{status}] {name}{detail_str}")
    return passed, failed


def main():
    passed = 0
    failed = 0
    project_id = None  # Project with existing queues (for idempotency tests)
    new_project_id = None  # Fresh project for create tests

    print("\n=== Mock Test: ensure_system_queues ===\n")

    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
        # ==========================================
        # 1. Discover a valid project_id (with existing queues)
        # ==========================================
        print("1. Discover valid project_id (with existing system queues)...")

        try:
            resp = client.get("/api/projects")
            if resp.status_code == 200:
                projects = resp.json().get("projects", [])
                if projects:
                    project_id = projects[0]["project_id"]
                    passed, failed = test_scenario(
                        "Found existing project",
                        passed, failed, True,
                        f"project_id={project_id}"
                    )
                else:
                    passed, failed = test_scenario(
                        "Find project",
                        passed, failed, False,
                        "no projects found"
                    )
                    print(f"\n=== Summary ===")
                    print(f"Passed: {passed}, Failed: {failed}")
                    print("RESULT: FAIL")
                    sys.exit(1)
            else:
                passed, failed = test_scenario(
                    "List projects",
                    passed, failed, False,
                    f"status={resp.status_code}"
                )
                print(f"\n=== Summary ===")
                print(f"Passed: {passed}, Failed: {failed}")
                print("RESULT: FAIL")
                sys.exit(1)
        except Exception as e:
            passed, failed = test_scenario(
                "Discover project",
                passed, failed, False,
                f"error={e}"
            )
            print(f"\n=== Summary ===")
            print(f"Passed: {passed}, Failed: {failed}")
            print("RESULT: FAIL")
            sys.exit(1)

        # ==========================================
        # 2. Create a NEW project with no queues (for testing creation)
        # ==========================================
        print("\n2. Create a fresh project for testing queue creation...")
        try:
            timestamp = int(time.time())
            resp = client.post("/api/projects", json={
                "name": f"test-ensure-{timestamp}",
                "project_type": "software"
            })
            if resp.status_code == 201:
                new_project_id = resp.json()["project_id"]
                # Wait for background auto-provision to complete
                time.sleep(1)
                passed, failed = test_scenario(
                    "Created fresh project",
                    passed, failed, True,
                    f"project_id={new_project_id}"
                )
            else:
                passed, failed = test_scenario(
                    "Create project",
                    passed, failed, False,
                    f"status={resp.status_code}, body={resp.text}"
                )
        except Exception as e:
            passed, failed = test_scenario(
                "Create fresh project",
                passed, failed, False,
                f"error={e}"
            )

        # ==========================================
        # 3. Fresh project - Call ensure (queues may exist from background task)
        # ==========================================
        print("\n3. Fresh project - Call ensure-system...")
        try:
            resp = client.post(f"/api/projects/{new_project_id}/queues/ensure-system")
            if resp.status_code == 200:
                data = resp.json()
                created_count = len(data.get("created_queues", []))
                existing_count = len(data.get("existing_queues", []))
                total_ok = data.get("total_system_queues", 0) == 5
                # Either all created, all existing, or split between them
                # The important thing is total = 5 and created + existing = 5
                total_match = (created_count + existing_count) == 5

                passed, failed = test_scenario(
                    "Ensure returns 200",
                    passed, failed, True
                )
                passed, failed = test_scenario(
                    "total_system_queues == 5",
                    passed, failed, total_ok,
                    f"got={data.get('total_system_queues')}"
                )
                passed, failed = test_scenario(
                    "created + existing == 5",
                    passed, failed, total_match,
                    f"created={created_count}, existing={existing_count}"
                )
            else:
                passed, failed = test_scenario(
                    "Ensure returns 200",
                    passed, failed, False,
                    f"status={resp.status_code}"
                )
        except Exception as e:
            passed, failed = test_scenario(
                "Call ensure-system (fresh project)",
                passed, failed, False,
                f"error={e}"
            )

        # ==========================================
        # 4. Fresh project - Call ensure again (idempotency)
        # ==========================================
        print("\n4. Fresh project - Call ensure again (idempotency)...")
        try:
            resp = client.post(f"/api/projects/{new_project_id}/queues/ensure-system")
            if resp.status_code == 200:
                data = resp.json()
                created_ok = len(data.get("created_queues", [])) == 0
                existing_ok = len(data.get("existing_queues", [])) == 5
                total_ok = data.get("total_system_queues", 0) == 5

                passed, failed = test_scenario(
                    "Ensure returns 200 (idempotent)",
                    passed, failed, True
                )
                passed, failed = test_scenario(
                    "created_queues is empty",
                    passed, failed, created_ok,
                    f"got={len(data.get('created_queues', []))}"
                )
                passed, failed = test_scenario(
                    "existing_queues has 5 items",
                    passed, failed, existing_ok,
                    f"got={len(data.get('existing_queues', []))}"
                )
                passed, failed = test_scenario(
                    "total_system_queues == 5",
                    passed, failed, total_ok,
                    f"got={data.get('total_system_queues')}"
                )
            else:
                passed, failed = test_scenario(
                    "Ensure returns 200",
                    passed, failed, False,
                    f"status={resp.status_code}"
                )
        except Exception as e:
            passed, failed = test_scenario(
                "Call ensure-system (idempotency)",
                passed, failed, False,
                f"error={e}"
            )

        # ==========================================
        # 5. Queue correctness verification (on fresh project)
        # ==========================================
        print("\n5. Queue correctness verification...")
        try:
            resp = client.get(f"/api/projects/{new_project_id}/queues")
            if resp.status_code == 200:
                queues = resp.json().get("queues", [])
                system_queues_found = {}

                for q in queues:
                    if q["queue_name"] in SYSTEM_QUEUES:
                        system_queues_found[q["queue_name"]] = q

                all_found = len(system_queues_found) == 5
                passed, failed = test_scenario(
                    "All 5 system queues exist",
                    passed, failed, all_found,
                    f"found={len(system_queues_found)}"
                )

                # Verify each queue has correct properties
                for queue_name, expected in SYSTEM_QUEUES.items():
                    if queue_name in system_queues_found:
                        q = system_queues_found[queue_name]
                        type_ok = q.get("queue_type") == expected["type"]
                        conc_ok = q.get("concurrency_limit") == expected["concurrency"]

                        passed, failed = test_scenario(
                            f"{queue_name} type={expected['type']}",
                            passed, failed, type_ok,
                            f"got={q.get('queue_type')}"
                        )
                        passed, failed = test_scenario(
                            f"{queue_name} concurrency={expected['concurrency']}",
                            passed, failed, conc_ok,
                            f"got={q.get('concurrency_limit')}"
                        )
                    else:
                        passed, failed = test_scenario(
                            f"{queue_name} exists",
                            passed, failed, False,
                            "not found"
                        )
            else:
                passed, failed = test_scenario(
                    "List queues",
                    passed, failed, False,
                    f"status={resp.status_code}"
                )
        except Exception as e:
            passed, failed = test_scenario(
                "Verify queue correctness",
                passed, failed, False,
                f"error={e}"
            )

        # ==========================================
        # 6. 404 for non-existent project
        # ==========================================
        print("\n6. 404 for non-existent project...")
        try:
            fake_id = "00000000-0000-0000-0000-000000000000"
            resp = client.post(f"/api/projects/{fake_id}/queues/ensure-system")
            is_404 = resp.status_code == 404
            passed, failed = test_scenario(
                "Returns 404 for non-existent project",
                passed, failed, is_404,
                f"status={resp.status_code}"
            )
        except Exception as e:
            passed, failed = test_scenario(
                "404 test",
                passed, failed, False,
                f"error={e}"
            )

        # ==========================================
        # 7. Cleanup - Delete test project
        # ==========================================
        print("\n7. Cleanup - Delete test project...")
        try:
            # Delete the fresh test project (this will cascade delete its queues)
            if new_project_id:
                del_resp = client.delete(f"/api/projects/{new_project_id}")
                if del_resp.status_code == 200:
                    passed, failed = test_scenario(
                        "Cleanup - delete test project",
                        passed, failed, True,
                        f"deleted project {new_project_id}"
                    )
                else:
                    passed, failed = test_scenario(
                        "Cleanup - delete test project",
                        passed, failed, False,
                        f"status={del_resp.status_code}"
                    )
            else:
                passed, failed = test_scenario(
                    "Cleanup - nothing to delete",
                    passed, failed, True
                )
        except Exception as e:
            passed, failed = test_scenario(
                "Cleanup",
                passed, failed, False,
                f"error={e}"
            )

    # ==========================================
    # Summary
    # ==========================================
    print(f"\n=== Summary ===")
    print(f"Passed: {passed}, Failed: {failed}")

    if failed > 0:
        print("RESULT: FAIL")
        sys.exit(1)
    print("RESULT: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
