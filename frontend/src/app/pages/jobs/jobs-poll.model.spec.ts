// jobs-poll.model spec — jobs-page-improvement arc, Phase 2.
//
// Pins:
// * shouldTick truth table — every pause condition independently.
// * POLL_INTERVAL_MS = 30_000 (cadence pin, F-5 anchor).
// * REFOCUS_DEBOUNCE_MS = 2_000 (debounce-window pin).
// * Poll constants pinned against the production source file.

import { readFileSync } from 'fs';
import { join } from 'path';
import { POLL_INTERVAL_MS, REFOCUS_DEBOUNCE_MS, shouldTick } from './jobs-poll.model';

describe('jobs-poll — shouldTick truth table', () => {
  it('the common case: visible tab, no overlays, no in-flight fetch → tick', () => {
    expect(
      shouldTick({
        tabVisible: true,
        drawerOpen: false,
        modalOpen: false,
        fetchInFlight: false,
      }),
    ).toBe(true);
  });

  it('hidden tab pauses regardless of any other input (4-case sweep)', () => {
    const hiddenCases = [
      { tabVisible: false, drawerOpen: false, modalOpen: false, fetchInFlight: false },
      { tabVisible: false, drawerOpen: true, modalOpen: false, fetchInFlight: false },
      { tabVisible: false, drawerOpen: false, modalOpen: true, fetchInFlight: false },
      { tabVisible: false, drawerOpen: false, modalOpen: false, fetchInFlight: true },
    ];
    for (const inputs of hiddenCases) {
      expect({ inputs, result: shouldTick(inputs) }).toEqual({
        inputs,
        result: false,
      });
    }
  });

  it('drawer open pauses even when the tab is visible', () => {
    expect(
      shouldTick({
        tabVisible: true,
        drawerOpen: true,
        modalOpen: false,
        fetchInFlight: false,
      }),
    ).toBe(false);
  });

  it('modal open pauses even when the tab is visible and drawer closed', () => {
    expect(
      shouldTick({
        tabVisible: true,
        drawerOpen: false,
        modalOpen: true,
        fetchInFlight: false,
      }),
    ).toBe(false);
  });

  it('in-flight fetch pauses even when the tab is visible and overlays closed', () => {
    expect(
      shouldTick({
        tabVisible: true,
        drawerOpen: false,
        modalOpen: false,
        fetchInFlight: true,
      }),
    ).toBe(false);
  });

  it('all three overlays + in-flight — hidden tab still wins (first-gate)', () => {
    expect(
      shouldTick({
        tabVisible: false,
        drawerOpen: true,
        modalOpen: true,
        fetchInFlight: true,
      }),
    ).toBe(false);
  });
});

describe('jobs-poll — cadence + debounce pins', () => {
  it('POLL_INTERVAL_MS = 30_000 ms (the 30s cadence pin)', () => {
    expect(POLL_INTERVAL_MS).toBe(30_000);
  });

  it('REFOCUS_DEBOUNCE_MS = 2_000 ms (the refocus-storm mitigation pin)', () => {
    expect(REFOCUS_DEBOUNCE_MS).toBe(2_000);
  });
});

describe('jobs-poll — production-source anchors (F-5 pins)', () => {
  const realSource = readFileSync(join(__dirname, 'jobs-poll.model.ts'), 'utf-8');

  it('the poll-gate helper exists on the REAL source file', () => {
    expect(realSource).toMatch(/export function shouldTick\(inputs: PollGateInputs\): boolean/);
  });

  it('the cadence constant exists on the REAL source file', () => {
    expect(realSource).toMatch(/export const POLL_INTERVAL_MS = 30_000;/);
  });

  it('the debounce constant exists on the REAL source file', () => {
    expect(realSource).toMatch(/export const REFOCUS_DEBOUNCE_MS = 2_000;/);
  });

  it('the gate reads tabVisible FIRST (early-return on hidden tab)', () => {
    // Spec guarantees the gate short-circuits on tabVisible so the
    // hidden-tab 4-case sweep is correct.
    expect(realSource).toMatch(/if \(!inputs\.tabVisible\) \{\s*\n\s*return false;/);
  });
});