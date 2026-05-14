import { deleteTestInstance } from './test-helpers';

/**
 * Track created resources for cleanup.
 * Note: Projects cannot be deleted via API, so we use unique timestamped names
 * and track only instance IDs for cleanup.
 */

const trackedInstanceIds = new Set<string>();

/**
 * Track a created instance for later cleanup.
 * @param instanceId - The instance ID to track
 */
export function trackInstance(instanceId: string): void {
  trackedInstanceIds.add(instanceId);
}

/**
 * Track a created project (for documentation purposes).
 * Projects cannot be deleted via API, so we just note them.
 * @param projectId - The project ID (not actually used for cleanup)
 */
export function trackProject(projectId: string): void {
  // Projects cannot be deleted via API - we use unique names instead
  // This function is kept for API consistency but doesn't track anything
  console.log(`[Cleanup] Project ${projectId} cannot be deleted via API (using unique names)`);
}

/**
 * Delete all tracked instances.
 * Should be called in afterAll or afterEach hooks.
 */
export async function cleanupAll(): Promise<void> {
  const deletePromises: Promise<void>[] = [];

  for (const instanceId of trackedInstanceIds) {
    deletePromises.push(
      deleteTestInstance(instanceId).catch((err) => {
        console.error(`[Cleanup] Failed to delete instance ${instanceId}:`, err.message);
      })
    );
  }

  await Promise.all(deletePromises);
  trackedInstanceIds.clear();
}
