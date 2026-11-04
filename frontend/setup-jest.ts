// The jest setup — the house's FE test environment.
//
// `jest-canvas-mock` is wired HERE (T1 phase 4, task 4.5): the
// Chart.js panel wrapper draws on a canvas, and jsdom has no 2D
// context. WITHOUT this import the canvas specs fail with "not
// implemented: HTMLCanvasElement.prototype.getContext" — the planted
// "no canvas-mock" variant proves the mock is load-bearing (the
// variant's failure output is in the phase-4 evidence).
import 'jest-canvas-mock';

import { setupZoneTestEnv } from 'jest-preset-angular/setup-env/zone';

/**
 * The minimal `ResizeObserver` stub.
 *
 * Chart.js's responsive mode requires a ResizeObserver, and jsdom ships
 * none — without this the panel's mount fails with "ResizeObserver is
 * not defined" and the canvas specs can never run. The stub is a
 * NO-OP observer: the specs assert the CONFIGURATION and the DOM
 * surface, never a layout.
 */
class ResizeObserverStub {
  observe(): void {
    /* no-op — the specs assert configuration, not layout */
  }

  unobserve(): void {
    /* no-op */
  }

  disconnect(): void {
    /* no-op */
  }
}

if (typeof (globalThis as { ResizeObserver?: unknown }).ResizeObserver === 'undefined') {
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
}

setupZoneTestEnv();
