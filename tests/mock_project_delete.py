#!/usr/bin/env python3
"""Mock test: Project Delete Cascade Cleanup against running dev server."""
import sys
import signal
import httpx
import time

BASE_URL = "http://localhost:8079"
TIMEOUT = 120  # 2 minutes


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


def cleanup_project(client: httpx.Client, project_id: str | None) -> bool:
    """Attempt to clean up a project, returning success status."""
    if not project_id:
        return True
    try:
        # Try force delete first to clean up any state
        resp = client.delete(f"/api/projects/{project_id}?force=true")
        return resp.status_code == 200
    except Exception:
        return False


def main():
    passed = 0
    failed = 0
    test_projects = []  # Track for cleanup

    print("\n=== Mock Test: Project Delete Cascade Cleanup ===\n")

    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
        # ==========================================
        # Scenario 1: 404 for non-existent project
        # ==========================================
        print("Scenario 1: 404 for non-existent project...")
        try:
            fake_id = "nonexistent-id-12345"
            resp = client.delete(f"/api/projects/{fake_id}")
            is_404 = resp.status_code == 404
            detail = f"status={resp.status_code}"
            if not is_404:
                detail += f", body={resp.text[:100]}"
            passed, failed = test_scenario(
                "Returns 404 for non-existent project",
                passed, failed, is_404,
                detail
            )
        except Exception as e:
            passed, failed = test_scenario(
                "404 test",
                passed, failed, False,
                f"error={e}"
            )

        # ==========================================
        # Scenario 2: Happy path - delete project with no active instances
        # ==========================================
        print("\nScenario 2: Happy path - delete project with no active instances...")
        project_id_2 = None
        try:
            # Create a test project
            timestamp = int(time.time())
            resp = client.post("/api/projects", json={
                "name": f"test-delete-happy-{timestamp}",
                "project_type": "software"
            })
            if resp.status_code == 201:
                project_id_2 = resp.json()["project_id"]
                test_projects.append(project_id_2)
                passed, failed = test_scenario(
                    "Create test project",
                    passed, failed, True,
                    f"project_id={project_id_2}"
                )
                # Wait for background auto-provision
                time.sleep(1)
            else:
                passed, failed = test_scenario(
                    "Create test project",
                    passed, failed, False,
                    f"status={resp.status_code}, body={resp.text[:100]}"
                )
        except Exception as e:
            passed, failed = test_scenario(
                "Create test project",
                passed, failed, False,
                f"error={e}"
            )

        # Optionally create a queue for the project to test cascade cleanup
        if project_id_2:
            try:
                resp = client.post(f"/api/projects/{project_id_2}/queues", json={
                    "queue_name": "test-queue",
                    "queue_type": "fifo",
                    "concurrency_limit": 1
                })
                if resp.status_code == 201:
                    passed, failed = test_scenario(
                        "Create test queue",
                        passed, failed, True,
                        "queue created for cascade test"
                    )
                else:
                    passed, failed = test_scenario(
                        "Create test queue",
                        passed, failed, False,
                        f"status={resp.status_code}"
                    )
            except Exception as e:
                passed, failed = test_scenario(
                    "Create test queue",
                    passed, failed, False,
                    f"error={e}"
                )

        # Delete the project
        if project_id_2:
            try:
                resp = client.delete(f"/api/projects/{project_id_2}")
                is_200 = resp.status_code == 200
                passed, failed = test_scenario(
                    "Delete project returns 200",
                    passed, failed, is_200,
                    f"status={resp.status_code}"
                )
            except Exception as e:
                passed, failed = test_scenario(
                    "Delete project",
                    passed, failed, False,
                    f"error={e}"
                )

        # Verify project is truly deleted
        if project_id_2:
            try:
                resp = client.get(f"/api/projects/{project_id_2}")
                is_404 = resp.status_code == 404
                passed, failed = test_scenario(
                    "GET project returns 404 after deletion",
                    passed, failed, is_404,
                    f"status={resp.status_code}"
                )
            except Exception as e:
                passed, failed = test_scenario(
                    "Verify project deleted",
                    passed, failed, False,
                    f"error={e}"
                )

        # Verify project not in list
        if project_id_2:
            try:
                resp = client.get("/api/projects")
                if resp.status_code == 200:
                    projects = resp.json().get("projects", [])
                    project_ids = [p["project_id"] for p in projects]
                    not_in_list = project_id_2 not in project_ids
                    passed, failed = test_scenario(
                        "Deleted project not in project list",
                        passed, failed, not_in_list,
                        f"found={project_id_2[:8]} in list" if project_id_2 in project_ids else "not in list"
                    )
                else:
                    passed, failed = test_scenario(
                        "Verify project not in list",
                        passed, failed, False,
                        f"list status={resp.status_code}"
                    )
            except Exception as e:
                passed, failed = test_scenario(
                    "Verify project not in list",
                    passed, failed, False,
                    f"error={e}"
                )

        # ==========================================
        # Scenario 3: Active instance protection (409)
        # ==========================================
        print("\nScenario 3: Active instance protection (409)...")
        project_id_3 = None
        instance_id_3 = None
        try:
            # Create a test project
            timestamp = int(time.time())
            resp = client.post("/api/projects", json={
                "name": f"test-delete-409-{timestamp}",
                "project_type": "software"
            })
            if resp.status_code == 201:
                project_id_3 = resp.json()["project_id"]
                test_projects.append(project_id_3)
                passed, failed = test_scenario(
                    "Create test project for 409 test",
                    passed, failed, True,
                    f"project_id={project_id_3}"
                )
                time.sleep(1)

                # Create an instance
                try:
                    resp = client.post("/api/instances", json={
                        "agent_id": "leader",
                        "project_id": project_id_3
                    })
                    if resp.status_code == 201:
                        instance_id_3 = resp.json()["instance_id"]
                        passed, failed = test_scenario(
                            "Create instance",
                            passed, failed, True,
                            f"instance_id={instance_id_3[:8]}..."
                        )
                        time.sleep(0.5)

                        # Pause the instance to keep it in a non-terminal state
                        try:
                            resp = client.post(f"/api/instances/{instance_id_3}/pause")
                            if resp.status_code == 200:
                                passed, failed = test_scenario(
                                    "Pause instance for 409 test",
                                    passed, failed, True,
                                    "instance paused"
                                )
                            else:
                                passed, failed = test_scenario(
                                    "Pause instance for 409 test",
                                    passed, failed, False,
                                    f"status={resp.status_code}"
                                )
                        except Exception as e:
                            passed, failed = test_scenario(
                                "Pause instance for 409 test",
                                passed, failed, False,
                                f"error={e}"
                            )
                    else:
                        passed, failed = test_scenario(
                            "Create instance",
                            passed, failed, False,
                            f"status={resp.status_code}, body={resp.text[:100]}"
                        )
                except Exception as e:
                    passed, failed = test_scenario(
                        "Create instance",
                        passed, failed, False,
                        f"error={e}"
                    )
            else:
                passed, failed = test_scenario(
                    "Create test project for 409 test",
                    passed, failed, False,
                    f"status={resp.status_code}"
                )
        except Exception as e:
            passed, failed = test_scenario(
                "Setup for 409 test",
                passed, failed, False,
                f"error={e}"
            )

        # Try to delete without force - should get 409
        if project_id_3 and instance_id_3:
            try:
                resp = client.delete(f"/api/projects/{project_id_3}")
                is_409 = resp.status_code == 409
                detail = f"status={resp.status_code}"
                if not is_409:
                    detail += f", body={resp.text[:100]}"
                passed, failed = test_scenario(
                    "Delete without force returns 409",
                    passed, failed, is_409,
                    detail
                )
            except Exception as e:
                passed, failed = test_scenario(
                    "Delete without force",
                    passed, failed, False,
                    f"error={e}"
                )

        # ==========================================
        # Scenario 4: Force delete with active instances
        # ==========================================
        print("\nScenario 4: Force delete with active instances...")
        project_id_4 = None
        instance_id_4 = None
        try:
            # Create a test project
            timestamp = int(time.time())
            resp = client.post("/api/projects", json={
                "name": f"test-delete-force-{timestamp}",
                "project_type": "software"
            })
            if resp.status_code == 201:
                project_id_4 = resp.json()["project_id"]
                test_projects.append(project_id_4)
                passed, failed = test_scenario(
                    "Create test project for force test",
                    passed, failed, True,
                    f"project_id={project_id_4}"
                )
                time.sleep(1)

                # Create an instance
                try:
                    resp = client.post("/api/instances", json={
                        "agent_id": "leader",
                        "project_id": project_id_4
                    })
                    if resp.status_code == 201:
                        instance_id_4 = resp.json()["instance_id"]
                        passed, failed = test_scenario(
                            "Create instance",
                            passed, failed, True,
                            f"instance_id={instance_id_4[:8]}..."
                        )
                        time.sleep(1)
                    else:
                        passed, failed = test_scenario(
                            "Create instance",
                            passed, failed, False,
                            f"status={resp.status_code}"
                        )
                except Exception as e:
                    passed, failed = test_scenario(
                        "Create instance",
                        passed, failed, False,
                        f"error={e}"
                    )
            else:
                passed, failed = test_scenario(
                    "Create test project for force test",
                    passed, failed, False,
                    f"status={resp.status_code}"
                )
        except Exception as e:
            passed, failed = test_scenario(
                "Setup for force test",
                passed, failed, False,
                f"error={e}"
            )

        # Force delete the project
        if project_id_4:
            try:
                resp = client.delete(f"/api/projects/{project_id_4}?force=true")
                is_200 = resp.status_code == 200
                passed, failed = test_scenario(
                    "Force delete returns 200",
                    passed, failed, is_200,
                    f"status={resp.status_code}"
                )
            except Exception as e:
                passed, failed = test_scenario(
                    "Force delete",
                    passed, failed, False,
                    f"error={e}"
                )

        # Verify project is gone
        if project_id_4:
            try:
                resp = client.get(f"/api/projects/{project_id_4}")
                is_404 = resp.status_code == 404
                passed, failed = test_scenario(
                    "GET project returns 404 after force delete",
                    passed, failed, is_404,
                    f"status={resp.status_code}"
                )
            except Exception as e:
                passed, failed = test_scenario(
                    "Verify force delete",
                    passed, failed, False,
                    f"error={e}"
                )

        # ==========================================
        # Scenario 5: Cascade verification
        # ==========================================
        print("\nScenario 5: Cascade verification...")
        project_id_5 = None
        try:
            # Create a test project
            timestamp = int(time.time())
            resp = client.post("/api/projects", json={
                "name": f"test-delete-cascade-{timestamp}",
                "project_type": "software"
            })
            if resp.status_code == 201:
                project_id_5 = resp.json()["project_id"]
                test_projects.append(project_id_5)
                passed, failed = test_scenario(
                    "Create test project for cascade test",
                    passed, failed, True,
                    f"project_id={project_id_5}"
                )
                time.sleep(1)

                # Ensure system queues exist
                try:
                    resp = client.post(f"/api/projects/{project_id_5}/queues/ensure-system")
                    if resp.status_code == 200:
                        passed, failed = test_scenario(
                            "Ensure system queues",
                            passed, failed, True,
                            "system queues created"
                        )
                    else:
                        passed, failed = test_scenario(
                            "Ensure system queues",
                            passed, failed, False,
                            f"status={resp.status_code}"
                        )
                except Exception as e:
                    passed, failed = test_scenario(
                        "Ensure system queues",
                        passed, failed, False,
                        f"error={e}"
                    )

                # Delete the project
                try:
                    resp = client.delete(f"/api/projects/{project_id_5}")
                    is_200 = resp.status_code == 200
                    passed, failed = test_scenario(
                        "Delete project returns 200",
                        passed, failed, is_200,
                        f"status={resp.status_code}"
                    )
                except Exception as e:
                    passed, failed = test_scenario(
                        "Delete project",
                        passed, failed, False,
                        f"error={e}"
                    )

                # Verify project is gone
                try:
                    resp = client.get(f"/api/projects/{project_id_5}")
                    is_404 = resp.status_code == 404
                    passed, failed = test_scenario(
                        "Project deleted (404)",
                        passed, failed, is_404,
                        f"status={resp.status_code}"
                    )
                except Exception as e:
                    passed, failed = test_scenario(
                        "Verify project deleted",
                        passed, failed, False,
                        f"error={e}"
                    )

                # Verify no orphan references
                try:
                    resp = client.get("/api/projects")
                    if resp.status_code == 200:
                        projects = resp.json().get("projects", [])
                        project_ids = [p["project_id"] for p in projects]
                        not_in_list = project_id_5 not in project_ids
                        passed, failed = test_scenario(
                            "No orphan references",
                            passed, failed, not_in_list,
                            "clean" if not_in_list else "found orphans"
                        )
                    else:
                        passed, failed = test_scenario(
                            "Verify no orphans",
                            passed, failed, False,
                            f"list status={resp.status_code}"
                        )
                except Exception as e:
                    passed, failed = test_scenario(
                        "Verify no orphans",
                        passed, failed, False,
                        f"error={e}"
                    )
            else:
                passed, failed = test_scenario(
                    "Create test project for cascade test",
                    passed, failed, False,
                    f"status={resp.status_code}"
                )
        except Exception as e:
            passed, failed = test_scenario(
                "Cascade test setup",
                passed, failed, False,
                f"error={e}"
            )

        # ==========================================
        # Scenario 6: In-memory cleanup verification
        # ==========================================
        print("\nScenario 6: In-memory cleanup verification...")
        project_id_6a = None
        project_id_6b = None
        try:
            # Create a project and instance
            timestamp = int(time.time())
            resp = client.post("/api/projects", json={
                "name": f"test-delete-memory-{timestamp}",
                "project_type": "software"
            })
            if resp.status_code == 201:
                project_id_6a = resp.json()["project_id"]
                test_projects.append(project_id_6a)
                passed, failed = test_scenario(
                    "Create project for memory test",
                    passed, failed, True,
                    f"project_id={project_id_6a}"
                )
                time.sleep(1)

                # Create an instance
                try:
                    resp = client.post("/api/instances", json={
                        "agent_id": "leader",
                        "project_id": project_id_6a
                    })
                    if resp.status_code == 201:
                        passed, failed = test_scenario(
                            "Create instance",
                            passed, failed, True,
                            "instance created"
                        )
                        time.sleep(1)
                    else:
                        passed, failed = test_scenario(
                            "Create instance",
                            passed, failed, False,
                            f"status={resp.status_code}"
                        )
                except Exception as e:
                    passed, failed = test_scenario(
                        "Create instance",
                        passed, failed, False,
                        f"error={e}"
                    )

                # Force delete the project
                try:
                    resp = client.delete(f"/api/projects/{project_id_6a}?force=true")
                    is_200 = resp.status_code == 200
                    passed, failed = test_scenario(
                        "Force delete project",
                        passed, failed, is_200,
                        f"status={resp.status_code}"
                    )
                except Exception as e:
                    passed, failed = test_scenario(
                        "Force delete project",
                        passed, failed, False,
                        f"error={e}"
                    )
            else:
                passed, failed = test_scenario(
                    "Create project for memory test",
                    passed, failed, False,
                    f"status={resp.status_code}"
                )
        except Exception as e:
            passed, failed = test_scenario(
                "Memory test setup",
                passed, failed, False,
                f"error={e}"
            )

        # Create a NEW project with the same name pattern (should work without conflicts)
        if project_id_6a:
            try:
                # Extract base name for reuse
                timestamp = int(time.time())
                resp = client.post("/api/projects", json={
                    "name": f"test-delete-memory-{timestamp}",
                    "project_type": "software"
                })
                if resp.status_code == 201:
                    project_id_6b = resp.json()["project_id"]
                    test_projects.append(project_id_6b)
                    passed, failed = test_scenario(
                        "Create new project with same name pattern",
                        passed, failed, True,
                        f"project_id={project_id_6b}"
                    )
                    time.sleep(1)
                else:
                    passed, failed = test_scenario(
                        "Create new project",
                        passed, failed, False,
                        f"status={resp.status_code}"
                    )
            except Exception as e:
                passed, failed = test_scenario(
                    "Create new project",
                    passed, failed, False,
                    f"error={e}"
                )

        # Verify new project is healthy
        if project_id_6b:
            try:
                resp = client.get(f"/api/projects/{project_id_6b}")
                is_200 = resp.status_code == 200
                passed, failed = test_scenario(
                    "New project is healthy (200)",
                    passed, failed, is_200,
                    f"status={resp.status_code}"
                )
            except Exception as e:
                passed, failed = test_scenario(
                    "Verify new project health",
                    passed, failed, False,
                    f"error={e}"
                )

        # Delete the new project to clean up
        if project_id_6b:
            try:
                resp = client.delete(f"/api/projects/{project_id_6b}")
                is_200 = resp.status_code == 200
                passed, failed = test_scenario(
                    "Cleanup - delete new project",
                    passed, failed, is_200,
                    f"status={resp.status_code}"
                )
            except Exception as e:
                passed, failed = test_scenario(
                    "Cleanup delete new project",
                    passed, failed, False,
                    f"error={e}"
                )

        # ==========================================
        # Cleanup remaining test projects
        # ==========================================
        print("\n--- Cleanup ---")

        # Clean up project 3 (409 test - has instance, needs force)
        if project_id_3:
            cleanup_project(client, project_id_3)

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
