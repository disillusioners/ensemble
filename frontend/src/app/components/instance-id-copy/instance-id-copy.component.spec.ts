import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Clipboard } from '@angular/cdk/clipboard';
import { MatSnackBar } from '@angular/material/snack-bar';
import { InstanceIdCopyComponent } from './instance-id-copy.component';

@Component({
  standalone: true,
  imports: [InstanceIdCopyComponent],
  template: `<app-instance-id-copy [instanceId]="id()">{{ short() }}</app-instance-id-copy>`,
})
class TestHostComponent {
  readonly id = signal('dfb5863e-1a2b-3c4d-5e6f-7a8b9c0d1e2f');
  readonly short = signal('dfb5863e...');
}

describe('InstanceIdCopyComponent', () => {
  let fixture: ComponentFixture<TestHostComponent>;
  let host: TestHostComponent;
  let clipboard: { copy: jest.Mock };
  let snackBar: { open: jest.Mock };
  let writeText: jest.Mock;

  const FULL_ID = 'dfb5863e-1a2b-3c4d-5e6f-7a8b9c0d1e2f';

  /** Flush the async copy chain (writeText await + boolean resolution). */
  const flush = async (): Promise<void> => {
    for (let i = 0; i < 4; i++) {
      await Promise.resolve();
    }
  };

  beforeEach(async () => {
    clipboard = { copy: jest.fn().mockReturnValue(true) };
    snackBar = { open: jest.fn() };
    writeText = jest.fn().mockResolvedValue(undefined);

    await TestBed.configureTestingModule({
      imports: [TestHostComponent],
      providers: [
        { provide: Clipboard, useValue: clipboard },
        { provide: MatSnackBar, useValue: snackBar },
      ],
    }).compileComponents();

    // jsdom has no navigator.clipboard — stub it per test via the shared mock.
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: writeText },
      configurable: true,
      writable: true,
    });

    fixture = TestBed.createComponent(TestHostComponent);
    host = fixture.componentInstance;
  });

  afterEach(() => {
    jest.clearAllMocks();
    // Remove the stub so other specs see the default (undefined) surface.
    Reflect.deleteProperty(navigator, 'clipboard');
  });

  function copyButton(): HTMLButtonElement {
    return (fixture.nativeElement as HTMLElement).querySelector('button')!;
  }

  it('renders a real button with tooltip + full-ID aria-label over projected text', () => {
    fixture.detectChanges();
    const button = copyButton();
    expect(button).toBeTruthy();
    expect(button.getAttribute('type')).toBe('button');
    expect(button.getAttribute('title')).toBe('Copy full ID');
    expect(button.getAttribute('aria-label')).toBe(`Copy full instance ID ${FULL_ID}`);
    // Projected (truncated) display text stays the call site's business.
    expect(button.textContent!.trim()).toBe('dfb5863e...');
  });

  it('copies the FULL id on click and confirms via success toast', async () => {
    fixture.detectChanges();
    copyButton().click();

    await flush();
    expect(writeText).toHaveBeenCalledWith(FULL_ID);
    expect(clipboard.copy).not.toHaveBeenCalled(); // API path wins when available
    expect(snackBar.open).toHaveBeenCalledWith(
      `Instance ${FULL_ID} copied`,
      'Close',
      expect.objectContaining({ duration: 2000, panelClass: 'success-snackbar' }),
    );
  });

  it('falls back to CDK execCommand copy when the async API rejects', async () => {
    writeText.mockRejectedValue(new DOMException('denied', 'NotAllowedError'));
    fixture.detectChanges();
    copyButton().click();

    await flush();
    expect(clipboard.copy).toHaveBeenCalledWith(FULL_ID);
    expect(snackBar.open).toHaveBeenCalledWith(
      `Instance ${FULL_ID} copied`,
      'Close',
      expect.objectContaining({ duration: 2000, panelClass: 'success-snackbar' }),
    );
  });

  it('uses the CDK fallback when navigator.clipboard is absent (insecure context)', async () => {
    Reflect.deleteProperty(navigator, 'clipboard');
    fixture.detectChanges();
    copyButton().click();

    await flush();
    expect(clipboard.copy).toHaveBeenCalledWith(FULL_ID);
    expect(snackBar.open).toHaveBeenCalledWith(
      `Instance ${FULL_ID} copied`,
      'Close',
      expect.objectContaining({ duration: 2000, panelClass: 'success-snackbar' }),
    );
  });

  it('does not let the click bubble to interactive ancestors', () => {
    fixture.detectChanges();
    let bubbled = 0;
    const listener = (): void => {
      bubbled++;
    };
    document.addEventListener('click', listener);
    try {
      copyButton().click();
      expect(bubbled).toBe(0); // stopPropagation keeps the list row <a> from navigating
    } finally {
      document.removeEventListener('click', listener);
    }
  });

  it('shows an error toast when every copy path fails', async () => {
    Reflect.deleteProperty(navigator, 'clipboard');
    clipboard.copy.mockReturnValue(false);
    fixture.detectChanges();
    copyButton().click();

    await flush();
    expect(snackBar.open).toHaveBeenCalledWith(
      'Failed to copy instance ID',
      'Dismiss',
      expect.objectContaining({ duration: 4000, panelClass: 'error-snackbar' }),
    );
  });

  it('aria-label reflects the current instance id input', () => {
    fixture.detectChanges();
    host.id.set('ffff8888-1111-2222-3333-444455556666');
    fixture.detectChanges();
    expect(copyButton().getAttribute('aria-label')).toBe(
      'Copy full instance ID ffff8888-1111-2222-3333-444455556666',
    );
  });
});
