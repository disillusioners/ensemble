import { test, expect, Page } from '@playwright/test';
import {
  createTestProject,
  createTestInstance,
} from './fixtures/test-helpers';
import { trackInstance, trackProject, cleanupAll } from './fixtures/cleanup';

test.describe.configure({ mode: 'serial' });

/**
 * E2E tests for the project tab bar feature on the /instances page.
 * These tests verify the tab bar functionality specifically on the instances page.
 */

test.describe('Instances Page - Project Tabs', () => {
  let page: Page;
  const timestamp = Date.now();
  const testProjects: Array<{ project_id: string; name: string }> = [];

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();

    // Navigate to the instances page
    await page.goto('/instances');
    await page.waitForSelector('app-project-tab-bar', { timeout: 10000 });
  });

  test.afterAll(async () => {
    await cleanupAll();
    await page?.close();
  });

  test.beforeEach(async () => {
    // Clear localStorage before each test to reset tab state
    await page.evaluate(() => localStorage.removeItem('ensemble-project-tabs'));
    // Refresh to ensure clean state
    await page.reload();
    await page.waitForSelector('app-project-tab-bar', { timeout: 10000 });
  });

  // ==========================================================================
  // Test 1: Tab bar is visible on /instances page
  // ==========================================================================
  test('Tab bar is visible on /instances page', async () => {
    // Verify the project tab bar element is present
    const tabBar = page.locator('app-project-tab-bar');
    await expect(tabBar).toBeVisible();

    // Verify the tab bar internal element is visible
    await expect(page.locator('.tab-bar')).toBeVisible();

    // Verify "All" tab is present and active by default
    const allTab = page.locator('.tab').first();
    await expect(allTab).toBeVisible();
    await expect(allTab).toHaveClass(/active/);
    await expect(allTab.locator('.tab-name')).toHaveText('All');
  });

  // ==========================================================================
  // Test 2: Tab switching works on /instances page
  // ==========================================================================
  test('Tab switching works on /instances page', async () => {
    // Create a test project with an instance
    const project = await createTestProject(`Test Project ${timestamp}-switch`);
    testProjects.push(project);
    trackProject(project.project_id);

    const instance = await createTestInstance('leader', project.project_id);
    trackInstance(instance.instance_id);

    // Refresh to pick up the new project
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });
    await page.waitForResponse(resp => resp.url().includes('/api/instances'));

    // Add the project tab via + menu
    const addButton = page.locator('.tab-add');
    await expect(addButton).toBeVisible();
    await addButton.click();

    const menu = page.locator('.project-menu');
    await expect(menu).toBeVisible();

    const menuItem = page.locator('.project-menu button[mat-menu-item]', {
      hasText: project.name,
    });
    await expect(menuItem).toBeVisible();
    await menuItem.click();

    // Wait for the instance list to update after selecting project tab
    await page.waitForResponse(resp => resp.url().includes('/api/instances'));

    // Verify the project tab appears and becomes active
    const projectTab = page.locator('.tab', { hasText: project.name });
    await expect(projectTab).toBeVisible();
    await expect(projectTab).toHaveClass(/active/);

    // Verify only the project's instance is shown (URL format: /projects/{project_id}/instances/{instance_id})
    const instanceLink = page.locator(`a[href^="/projects/${project.project_id}/instances/${instance.instance_id}"]`);
    await expect(instanceLink).toBeVisible();

    // Click "All" tab
    const allTab = page.locator('.tab').first();
    await allTab.click();

    // Wait for the API response
    await page.waitForResponse(resp => resp.url().includes('/api/instances'));

    // Verify "All" tab is now active
    await expect(allTab).toHaveClass(/active/);
  });

  // ==========================================================================
  // Test 3: Tab state persists on /instances page
  // ==========================================================================
  test('Tab state persists on /instances page', async () => {
    // Create a test project
    const project = await createTestProject(`Test Project ${timestamp}-persist`);
    testProjects.push(project);
    trackProject(project.project_id);

    const instance = await createTestInstance('leader', project.project_id);
    trackInstance(instance.instance_id);

    // Refresh and add the project tab
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });

    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: project.name }).click();

    // Verify the project tab is active
    const projectTab = page.locator('.tab', { hasText: project.name });
    await expect(projectTab).toHaveClass(/active/);

    // Navigate away to chat page
    // First create a base instance to navigate to
    const baseInstance = await createTestInstance('leader');
    trackInstance(baseInstance.instance_id);
    await page.goto(`/instances/${baseInstance.instance_id}`);
    await page.waitForTimeout(1000);

    // Navigate back to /instances
    await page.goto('/instances');
    await page.waitForSelector('.tab-bar', { timeout: 10000 });

    // Verify the previously selected tab is still active
    await expect(page.locator('.tab', { hasText: project.name })).toBeVisible();
    await expect(page.locator('.tab', { hasText: project.name })).toHaveClass(/active/);

    // Verify "All" tab is no longer active
    const allTab = page.locator('.tab').first();
    await expect(allTab).not.toHaveClass(/active/);
  });

  // ==========================================================================
  // Test 4: Instances page shows correct instances per project
  // ==========================================================================
  test('Instances page shows correct instances per project', async () => {
    // Create two projects
    const project1 = await createTestProject(`Test Project ${timestamp}-multi1`);
    const project2 = await createTestProject(`Test Project ${timestamp}-multi2`);
    testProjects.push(project1, project2);
    trackProject(project1.project_id);
    trackProject(project2.project_id);

    // Create instances in each project
    const instance1 = await createTestInstance('leader', project1.project_id);
    const instance2 = await createTestInstance('leader', project2.project_id);
    trackInstance(instance1.instance_id);
    trackInstance(instance2.instance_id);

    // Refresh to pick up the new projects and instances
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });
    await page.waitForResponse(resp => resp.url().includes('/api/instances'));

    // Add project 1 tab
    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: project1.name }).click();
    await page.waitForResponse(resp => resp.url().includes('/api/instances'));

    // Verify only project 1's instance is visible (URL format: /projects/{project_id}/instances/{instance_id})
    const instance1Link = page.locator(`a[href^="/projects/${project1.project_id}/instances/${instance1.instance_id}"]`);
    await expect(instance1Link).toBeVisible();
    const instance2Link = page.locator(`a[href^="/projects/${project2.project_id}/instances/${instance2.instance_id}"]`);
    await expect(instance2Link).not.toBeVisible();

    // Add project 2 tab via + menu
    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: project2.name }).click();
    await page.waitForResponse(resp => resp.url().includes('/api/instances'));

    // Verify project 2 tab is visible
    const project2Tab = page.locator('.tab', { hasText: project2.name });
    await expect(project2Tab).toBeVisible();

    // Verify only project 2's instance is visible
    await expect(instance2Link).toBeVisible();
    await expect(instance1Link).not.toBeVisible();

    // Switch back to "All" tab
    const allTab = page.locator('.tab').first();
    await allTab.click();
    await page.waitForResponse(resp => resp.url().includes('/api/instances'));

    // Verify all instances are visible - re-create locators since URLs change on "All" tab
    // On "All" tab, URLs are /projects/all/instances/{id}
    const instance1LinkOnAll = page.locator(`a[href="/projects/all/instances/${instance1.instance_id}"]`);
    const instance2LinkOnAll = page.locator(`a[href="/projects/all/instances/${instance2.instance_id}"]`);
    await expect(instance1LinkOnAll).toBeVisible();
    await expect(instance2LinkOnAll).toBeVisible();
  });

  // ==========================================================================
  // Test 5: Empty project shows empty state on instances page
  // ==========================================================================
  test('Empty project shows empty state on instances page', async () => {
    // Create a project with NO instances
    const emptyProject = await createTestProject(`Test Project ${timestamp}-empty`);
    testProjects.push(emptyProject);
    trackProject(emptyProject.project_id);

    // Refresh to pick up the new project
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });

    // Open the empty project tab
    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: emptyProject.name }).click();

    // Verify the project tab is active
    const emptyTab = page.locator('.tab', { hasText: emptyProject.name });
    await expect(emptyTab).toHaveClass(/active/);

    // Wait for the instance list to update
    await page.waitForResponse(resp => resp.url().includes('/api/instances'));

    // Verify empty state is shown
    const emptyState = page.locator('.empty-state');
    await expect(emptyState).toBeVisible();
  });
});
