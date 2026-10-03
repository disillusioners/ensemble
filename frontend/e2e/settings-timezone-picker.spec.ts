/**
 * Ad-hoc E2E smoke — settings-page timezone picker (web automation).
 *
 * Feature under test: user-timezone-setting @ 4ac8fd4a
 *   settings page timezone picker (app-searchable-select fed by the
 *   API list from GET /api/settings/timezones, falling back to
 *   Intl.supportedValuesOf('timeZone'), then to a plain text-input
 *   row; labels carry UTC offsets, 'Auto / not set' sentinel → API
 *   null) wired to GET/PUT /api/settings/timezone.
 *
 * Scenarios:
 *   S1  picker renders with zone list showing offsets
 *       (assert Asia/Bangkok + a UTC+7 / +07:00 representation)
 *   S2  select Asia/Bangkok → auto-save (PUT) → RELOAD → persisted
 *       (picker shows Asia/Bangkok; GET corroborates)
 *   S3  select Auto / not set → auto-save (PUT null) → RELOAD → cleared
 *       (GET returns nulls; picker back to Auto)
 *
 * Run (single spec only, against an already-booted dev lane):
 *   cd frontend && timeout 300 npx playwright test e2e/settings-timezone-picker.spec.ts --reporter=line
 *
 * NOTE: this spec auto-navigates like every other spec in this dir —
 * the dev lane presents no auth wall.
 */
import { test, expect, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

/** Evidence dir (Playwright runs from frontend/). */
const RESULTS_DIR = path.resolve(process.cwd(), 'e2e-results', 'timezone-smoke');

const SETTINGS_URL = '/settings';
/** The timezone searchable-select field (aria-label = its `label` input). */
const TZ_FIELD = 'mat-form-field[aria-label="Timezone"] input';

const TZ_API = '/api/settings/timezone';
const onTzPut = (r: { url(): string; request(): { method(): string } }) =>
  r.url().includes(TZ_API) && r.request().method() === 'PUT';
const onTzGet = (r: { url(): string; request(): { method(): string } }) =>
  r.url().includes(TZ_API) && r.request().method() === 'GET';

interface TzPref {
  timezone: string | null;
  utc_offset: string | null;
}

/** Reset the server-side preference through the real API. */
async function putTimezone(request: Page['request'], value: string | null): Promise<TzPref> {
  const resp = await request.put(TZ_API, { data: { timezone: value } });
  expect(resp.status(), 'PUT /api/settings/timezone should succeed').toBe(200);
  return (await resp.json()) as TzPref;
}

async function getTimezone(request: Page['request']): Promise<TzPref> {
  const resp = await request.get(TZ_API);
  expect(resp.status(), 'GET /api/settings/timezone should succeed').toBe(200);
  return (await resp.json()) as TzPref;
}

/** Open the settings page and wait until the picker reflects the stored pref. */
async function openSettings(page: Page): Promise<void> {
  const getResp = page.waitForResponse(onTzGet);
  await page.goto(SETTINGS_URL);
  await getResp;
  await expect(page.locator(TZ_FIELD)).toBeVisible();
}

async function screenshot(page: Page, name: string): Promise<string> {
  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  const file = path.join(RESULTS_DIR, name);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

test.describe('settings timezone picker smoke', () => {
  test('S1: picker renders zone list with offset labels (Asia/Bangkok, UTC+07:00)', async ({
    page,
    request,
  }) => {
    // Fresh Auto state so the run is deterministic.
    const reset = await putTimezone(request, null);
    expect(reset).toEqual({ timezone: null, utc_offset: null });

    await openSettings(page);

    // Unset state displays the Auto sentinel label.
    await expect(page.locator(TZ_FIELD)).toHaveValue('Auto / not set');

    // Open the panel (focus clears the display text → full list).
    await page.locator(TZ_FIELD).click();
    const bangkok = page.getByRole('option', { name: 'Asia/Bangkok (UTC+07:00)' });
    await expect(bangkok).toBeVisible();

    // The list carries offset representations broadly, not just Bangkok.
    const offsetLabels = page.getByRole('option').filter({
      hasText: /\(UTC[+-]\d{2}:\d{2}\)/,
    });
    const offsetCount = await offsetLabels.count();
    expect(
      offsetCount,
      'zone list should show many offset-bearing labels',
    ).toBeGreaterThan(10);

    const shot = await screenshot(page, '01-picker-rendered-with-offsets.png');
    console.log(`[S1] screenshot: ${shot}`);
    console.log(`[S1] offset-bearing options rendered: ${offsetCount}`);
  });

  test('S2: select Asia/Bangkok → auto-save → reload → persisted', async ({
    page,
    request,
  }) => {
    await putTimezone(request, null); // deterministic start

    await openSettings(page);
    await page.locator(TZ_FIELD).click();

    const putResp = page.waitForResponse(onTzPut);
    await page.getByRole('option', { name: 'Asia/Bangkok (UTC+07:00)' }).click();
    const resp = await putResp;
    expect(resp.status()).toBe(200);
    const putBody = (await resp.json()) as TzPref;
    console.log(`[S2] PUT response body: ${JSON.stringify(putBody)}`);
    expect(putBody).toEqual({ timezone: 'Asia/Bangkok', utc_offset: '+07:00' });

    // Server-side corroboration through the same API the page uses.
    const stored = await getTimezone(request);
    console.log(`[S2] GET after save: ${JSON.stringify(stored)}`);
    expect(stored).toEqual({ timezone: 'Asia/Bangkok', utc_offset: '+07:00' });

    // RELOAD — the picker must come back showing the persisted zone.
    const reloadedGet = page.waitForResponse(onTzGet);
    await page.reload();
    await reloadedGet;
    await expect(page.locator(TZ_FIELD)).toHaveValue('Asia/Bangkok (UTC+07:00)');

    const shot = await screenshot(page, '02-after-save-reloaded-bangkok.png');
    console.log(`[S2] screenshot: ${shot}`);
  });

  test('S3: select Auto / not set → clear (PUT null) → reload → cleared', async ({
    page,
    request,
  }) => {
    // Start from a SET zone so this scenario exercises a real clear.
    const seeded = await putTimezone(request, 'Asia/Bangkok');
    expect(seeded.timezone).toBe('Asia/Bangkok');

    await openSettings(page);
    await expect(page.locator(TZ_FIELD)).toHaveValue('Asia/Bangkok (UTC+07:00)');

    await page.locator(TZ_FIELD).click();

    const putResp = page.waitForResponse(onTzPut);
    await page.getByRole('option', { name: 'Auto / not set' }).click();
    const resp = await putResp;
    expect(resp.status()).toBe(200);
    const putBody = (await resp.json()) as TzPref;
    console.log(`[S3] PUT response body: ${JSON.stringify(putBody)}`);
    expect(putBody).toEqual({ timezone: null, utc_offset: null });

    const stored = await getTimezone(request);
    console.log(`[S3] GET after clear: ${JSON.stringify(stored)}`);
    expect(stored).toEqual({ timezone: null, utc_offset: null });

    // RELOAD — picker must return to the Auto state.
    const reloadedGet = page.waitForResponse(onTzGet);
    await page.reload();
    await reloadedGet;
    await expect(page.locator(TZ_FIELD)).toHaveValue('Auto / not set');

    const shot = await screenshot(page, '03-after-clear-reloaded-auto.png');
    console.log(`[S3] screenshot: ${shot}`);
  });
});
