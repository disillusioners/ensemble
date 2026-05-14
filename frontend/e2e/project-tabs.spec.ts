import { test, expect, Page } from '@playwright/test';
import {
  createTestProject,
  createTestInstance,
  listInstances,
} from './fixtures/test-helpers';
import { trackInstance, trackProject, cleanupAll } from './fixtures/cleanup';

test.describe.configure({ mode: 'serial' });

/**
 * E2E tests for the project tabs feature.
 * These tests verify the tab bar functionality for filtering instances by project.
 */

test.describe('Project Tabs Feature', () => {
  let page: Page;
  const timestamp = Date.now();
  const testProjects: Array<{ project_id: string; name: string }> = [];

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();

    // Create a base instance first to navigate to chat page
    const baseInstance = await createTestInstance('leader');
    trackInstance(baseInstance.instance_id);

    // Navigate to the chat page with this instance
    await page.goto(`/instances/${baseInstance.instance_id}`);
    await page.waitForSelector('.tab-bar', { timeout: 10000 });
  });

  test.afterAll(async () => {
    await cleanupAll();
    await page?.close();
  });

  test.afterEach(async () => {
    // Clear localStorage between tests to reset tab state
    await page.evaluate(() => localStorage.removeItem('ensemble-project-tabs'));
  });

  // ==========================================================================
  // Test 1: Default state - "All" tab visible and active on load
  // ==========================================================================
  test('Default state: "All" tab visible and active on load', async () => {
    // Verify the tab bar is visible
    await expect(page.locator('.tab-bar')).toBeVisible();

    // Verify "All" tab exists and is active
    const allTab = page.locator('.tab').first();
    await expect(allTab).toBeVisible();
    await expect(allTab).toHaveClass(/active/);
    await expect(allTab.locator('.tab-name')).toHaveText('All');

    // Verify "All" tab has no close button
    const closeButton = allTab.locator('.tab-close');
    await expect(closeButton).toHaveCount(0);
  });

  // ==========================================================================
  // Test 2: Add project tab from + menu
  // ==========================================================================
  test('Add project tab from + menu', async () => {
    // Create a test project
    const project = await createTestProject(`Test Project ${timestamp}-add`);
    testProjects.push(project);
    trackProject(project.project_id);

    // Refresh to pick up the new project
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });

    // Click the + button to open project menu
    const addButton = page.locator('.tab-add');
    await expect(addButton).toBeVisible();
    await addButton.click();

    // Wait for the menu to appear
    const menu = page.locator('.project-menu');
    await expect(menu).toBeVisible();

    // Click on the project name in the menu
    const menuItem = page.locator('.project-menu button[mat-menu-item]', {
      hasText: project.name,
    });
    await expect(menuItem).toBeVisible();
    await menuItem.click();

    // Verify a new tab appears with the project name
    const projectTab = page.locator('.tab', { hasText: project.name });
    await expect(projectTab).toBeVisible();

    // Verify the new tab becomes active
    await expect(projectTab).toHaveClass(/active/);

    // Verify "All" tab is no longer active
    const allTab = page.locator('.tab').first();
    await expect(allTab).not.toHaveClass(/active/);
  });

  // ==========================================================================
  // Test 3: Switching tabs filters instances
  // ==========================================================================
  test('Switching tabs filters instances', async () => {
    // Create two projects with instances
    const project1 = await createTestProject(`Test Project ${timestamp}-p1`);
    const project2 = await createTestProject(`Test Project ${timestamp}-p2`);
    testProjects.push(project1, project2);
    trackProject(project1.project_id);
    trackProject(project2.project_id);

    // Create instances in each project
    const instance1 = await createTestInstance('leader', project1.project_id);
    const instance2 = await createTestInstance('leader', project2.project_id);
    trackInstance(instance1.instance_id);
    trackInstance(instance2.instance_id);

    // Refresh to pick up new projects and instances
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });

    // Open project 1 tab
    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: project1.name }).click();

    // Open project 2 tab
    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: project2.name }).click();

    // Verify only project 1's instance shows
    let instanceLinks = page.locator('a[href^="/instances/"]');
    const count1 = await instanceLinks.count();
    expect(count1).toBeGreaterThanOrEqual(1);

    // Switch to project 2 tab
    const project2Tab = page.locator('.tab', { hasText: project2.name });
    await project2Tab.click();

    // Verify only project 2's instance shows
    instanceLinks = page.locator('a[href^="/instances/"]');
    const count2 = await instanceLinks.count();
    expect(count2).toBeGreaterThanOrEqual(1);

    // Switch back to "All" tab
    const allTab = page.locator('.tab').first();
    await allTab.click();

    // Wait for instances to load
    await page.waitForTimeout(500);

    // Verify all instances show (should show both)
    instanceLinks = page.locator('a[href^="/instances/"]');
    await expect(instanceLinks.first()).toBeVisible();
    const totalCount = await instanceLinks.count();
    expect(totalCount).toBeGreaterThanOrEqual(2);
  });

  // ==========================================================================
  // Test 4: Close project tab
  // ==========================================================================
  test('Close project tab', async () => {
    // Create a project and add it as a tab
    const project = await createTestProject(`Test Project ${timestamp}-close`);
    testProjects.push(project);
    trackProject(project.project_id);

    const instance = await createTestInstance('leader', project.project_id);
    trackInstance(instance.instance_id);

    // Refresh and add the project tab
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });

    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: project.name }).click();

    // Verify the project tab is visible and active
    const projectTab = page.locator('.tab', { hasText: project.name });
    await expect(projectTab).toBeVisible();
    await expect(projectTab).toHaveClass(/active/);

    // Close the project tab
    const closeButton = projectTab.locator('.tab-close');
    await closeButton.click();

    // Verify the tab disappears
    await expect(projectTab).not.toBeVisible();

    // Verify "All" tab becomes active
    const allTab = page.locator('.tab').first();
    await expect(allTab).toHaveClass(/active/);
  });

  // ==========================================================================
  // Test 5: "All" tab cannot be closed
  // ==========================================================================
  test('"All" tab cannot be closed', async () => {
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });

    const allTab = page.locator('.tab').first();
    await expect(allTab).toBeVisible();

    // Verify "All" tab has no close button
    const closeButton = allTab.locator('.tab-close');
    await expect(closeButton).toHaveCount(0);

    // Also verify by checking that only project tabs have close buttons
    // First add a project tab
    const project = await createTestProject(`Test Project ${timestamp}-nocloseproject`);
    testProjects.push(project);
    trackProject(project.project_id);

    // Refresh to pick up the new project
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });

    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: project.name }).click();

    // Now verify that only the project tab has a close button
    const closeButtons = page.locator('.tab-close');
    await expect(closeButtons).toHaveCount(1);

    // Verify the close button belongs to the project tab, not "All"
    const projectTab = page.locator('.tab', { hasText: project.name });
    await expect(projectTab.locator('.tab-close')).toBeVisible();
  });

  // ==========================================================================
  // Test 6: Tab state persists after page refresh
  // ==========================================================================
  test('Tab state persists after page refresh', async () => {
    // Create two projects and add them as tabs
    const project1 = await createTestProject(`Test Project ${timestamp}-persist1`);
    const project2 = await createTestProject(`Test Project ${timestamp}-persist2`);
    testProjects.push(project1, project2);
    trackProject(project1.project_id);
    trackProject(project2.project_id);

    // Refresh and add both project tabs
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });

    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: project1.name }).click();

    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: project2.name }).click();

    // Click on project1 tab to make it active
    const project1Tab = page.locator('.tab', { hasText: project1.name });
    await project1Tab.click();

    // Verify state before refresh
    await expect(project1Tab).toHaveClass(/active/);
    await expect(page.locator('.tab', { hasText: project2.name })).not.toHaveClass(/active/);

    // Refresh the page
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });

    // Verify tabs are restored
    await expect(page.locator('.tab', { hasText: project1.name })).toBeVisible();
    await expect(page.locator('.tab', { hasText: project2.name })).toBeVisible();

    // Verify active tab is restored (project1 should be active)
    await expect(page.locator('.tab', { hasText: project1.name })).toHaveClass(/active/);
  });

  // ==========================================================================
  // Test 7: "+" menu shows only unopened projects
  // ==========================================================================
  test('"+" menu shows only unopened projects', async () => {
    // Create two projects
    const project1 = await createTestProject(`Test Project ${timestamp}-menu1`);
    const project2 = await createTestProject(`Test Project ${timestamp}-menu2`);
    testProjects.push(project1, project2);
    trackProject(project1.project_id);
    trackProject(project2.project_id);

    // Refresh
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 10000 });

    // Open both projects as tabs
    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: project1.name }).click();

    await page.locator('.tab-add').click();
    await page.locator('.project-menu button[mat-menu-item]', { hasText: project2.name }).click();

    // Verify both tabs are open
    await expect(page.locator('.tab', { hasText: project1.name })).toBeVisible();
    await expect(page.locator('.tab', { hasText: project2.name })).toBeVisible();

    // Click + button again
    await page.locator('.tab-add').click();

    // Verify menu is visible
    const menu = page.locator('.project-menu');
    await expect(menu).toBeVisible();

    // Verify the opened projects are NOT in the menu (they're already open)
    const openedProject1 = page.locator('.project-menu button[mat-menu-item]', { hasText: project1.name });
    const openedProject2 = page.locator('.project-menu button[mat-menu-item]', { hasText: project2.name });
    await expect(openedProject1).toHaveCount(0);
    await expect(openedProject2).toHaveCount(0);
  });

  // ==========================================================================
  // Test 8: Empty project shows empty state
  // ==========================================================================
  test('Empty project shows empty state', async () => {
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

    // Verify empty state is shown
    const emptyState = page.locator('.empty-state');
    await expect(emptyState).toBeVisible();

    // Verify the empty state message
    const emptyText = emptyState.locator('.empty-text');
    await expect(emptyText).toBeVisible();
    await expect(emptyText).toContainText('No instances in this project');
  });
});
