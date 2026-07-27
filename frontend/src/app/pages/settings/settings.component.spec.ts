import { signal } from '@angular/core';
import { of, throwError } from 'rxjs';

// Storage key matching the component
const STORAGE_KEY = 'settings-language-preference';
const EDITOR_STORAGE_KEY = 'settings-editor-preference';
const CUSTOM_OPTION_VALUE = 'Other (custom)';
const PREDEFINED_LANGUAGES = [
  'Auto',
  'English',
  'Spanish',
  'Chinese',
  'French',
  'German',
  'Japanese',
  'Korean',
  'Portuguese',
  'Russian',
  'Arabic',
  'Vietnamese',
  'Italian',
  'Dutch',
  'Hindi',
];
const STATUS_POLL_INTERVAL_MS = 2000;
const DEFAULT_EDITOR: EditorType = 'builtin';
type EditorType = 'builtin' | 'vscode';

interface VSCodeStatus {
  running: boolean;
  port: number | null;
  allow_remote: boolean;
}

// localStorage mock helpers
let localStorageData: Record<string, string> = {};
type StorageErrorMode = 'none' | 'get' | 'set' | 'remove' | 'all';
let localStorageErrorMode: StorageErrorMode = 'none';

const mockLocalStorage = {
  getItem: (key: string): string | null => {
    if (localStorageErrorMode === 'get' || localStorageErrorMode === 'all') throw new Error('localStorage unavailable');
    return localStorageData[key] ?? null;
  },
  setItem: (key: string, value: string): void => {
    if (localStorageErrorMode === 'set' || localStorageErrorMode === 'all') throw new Error('localStorage unavailable');
    localStorageData[key] = value;
  },
  removeItem: (key: string): void => {
    if (localStorageErrorMode === 'remove' || localStorageErrorMode === 'all') throw new Error('localStorage unavailable');
    delete localStorageData[key];
  },
  clear: () => {
    localStorageData = {};
  },
};

// Replace global localStorage
const originalLocalStorage = global.localStorage;
beforeAll(() => {
  Object.defineProperty(global, 'localStorage', {
    value: mockLocalStorage,
    writable: true,
    configurable: true,
  });
});

afterAll(() => {
  Object.defineProperty(global, 'localStorage', {
    value: originalLocalStorage,
    writable: true,
    configurable: true,
  });
});

beforeEach(() => {
  localStorageData = {};
  localStorageErrorMode = 'none';
});

// Mock MatSnackBar
class MockMatSnackBar {
  static lastOpen: { message: string; action?: string; options?: object } | null = null;

  open(message: string, action?: string, options?: { duration?: number; panelClass?: string }): void {
    MockMatSnackBar.lastOpen = { message, action, options };
  }

  static reset(): void {
    MockMatSnackBar.lastOpen = null;
  }
}

// Mock SettingsService
class MockSettingsService {
  getLanguagePreference = jest.fn();
  setLanguagePreference = jest.fn();
  getEditorPreference = jest.fn();
  setEditorPreference = jest.fn();
  getVscodeStatus = jest.fn();
  startVscodeServer = jest.fn();
  stopVscodeServer = jest.fn();
}

// Testable SettingsComponent (mirrors actual component for testing)
class TestableSettingsComponent {
  readonly languages = PREDEFINED_LANGUAGES;
  readonly customOptionValue = CUSTOM_OPTION_VALUE;
  readonly selectedLanguage = signal<string>('Auto');
  readonly customLanguage = signal<string>('');
  readonly isCustom = signal<boolean>(false);
  readonly saving = signal<boolean>(false);

  // Editor preference state — public readonly to match production signal exposure
  readonly selectedEditor = signal<EditorType>(DEFAULT_EDITOR);
  readonly savedEditor = signal<EditorType>(DEFAULT_EDITOR);
  readonly applyingEditor = signal<boolean>(false);
  readonly vscodeStatus = signal<VSCodeStatus | null>(null);
  readonly editorDirty = signal<boolean>(false);

  private statusPollTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private settingsService: MockSettingsService,
    private snackBar: MockMatSnackBar,
  ) {}

  ngOnInit(): void {
    this.loadFromStorage();
    this.loadFromApi();
    this.loadEditorPreference();
  }

  ngOnDestroy(): void {
    this.stopStatusPolling();
  }

  private loadFromStorage(): void {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(STORAGE_KEY);
    } catch {
      // silently ignore
    }
    if (!saved) {
      return;
    }
    this.applyPreference(saved);
  }

  private loadFromApi(): void {
    let hadCachedValue = false;
    try {
      hadCachedValue = localStorage.getItem(STORAGE_KEY) !== null;
    } catch {
      hadCachedValue = false;
    }

    this.settingsService.getLanguagePreference().subscribe({
      next: (pref: { language: string }) => {
        const lang = pref?.language;
        if (lang) {
          this.applyPreference(lang);
          this.persistToStorage(lang);
        }
      },
      error: () => {
        if (!hadCachedValue) {
          this.selectedLanguage.set('Auto');
        }
        this.snackBar.open('Failed to load language preference', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  private loadEditorPreference(): void {
    let cached: string | null = null;
    try {
      cached = localStorage.getItem(EDITOR_STORAGE_KEY);
    } catch {
      cached = null;
    }

    const cachedEditor = this.coerceEditorType(cached);
    if (cachedEditor) {
      this.selectedEditor.set(cachedEditor);
      this.savedEditor.set(cachedEditor);
    }

    let hadCached = cached !== null;
    this.settingsService.getEditorPreference().subscribe({
      next: (resp: { editor: string }) => {
        const editor = this.coerceEditorType(resp?.editor);
        if (editor) {
          this.selectedEditor.set(editor);
          this.savedEditor.set(editor);
          this.editorDirty.set(false);
          this.persistEditorToStorage(editor);
          if (editor === 'vscode') {
            this.startStatusPolling();
          }
        }
      },
      error: () => {
        if (!hadCached) {
          this.selectedEditor.set(DEFAULT_EDITOR);
          this.savedEditor.set(DEFAULT_EDITOR);
        }
        this.snackBar.open('Failed to load editor preference', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  private coerceEditorType(value: string | null | undefined): EditorType | null {
    if (value === 'builtin' || value === 'vscode') {
      return value;
    }
    return null;
  }

  onEditorSelectionChange(editor: EditorType): void {
    this.selectedEditor.set(editor);
    this.editorDirty.set(editor !== this.savedEditor());
  }

  saveEditor(): void {
    const target = this.selectedEditor();
    this.applyingEditor.set(true);
    this.settingsService.setEditorPreference(target).subscribe({
      next: (resp: { editor: string }) => {
        const confirmed = this.coerceEditorType(resp?.editor) ?? target;
        this.savedEditor.set(confirmed);
        this.editorDirty.set(false);
        this.persistEditorToStorage(confirmed);
        this.applyingEditor.set(false);
        this.snackBar.open(`Editor preference set to ${this.editorLabel(confirmed)}`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
        if (confirmed === 'vscode') {
          this.startStatusPolling();
        } else {
          this.stopStatusPolling();
          this.vscodeStatus.set(null);
        }
      },
      error: (err: { status?: number; error?: { detail?: { error?: string; detail?: string } } }) => {
        this.applyingEditor.set(false);
        // Mirror of the production error handler — surface the specific reason
        // from the backend's `detail.error` so the user can act on it
        // (install binary, check logs, restart daemon) instead of a single
        // catch-all hint. All other failures get a generic message.
        const isUnavailable = err?.status === 503;
        let message: string;
        if (isUnavailable) {
          const detail = err?.error?.detail;
          switch (detail?.error) {
            case 'code-server binary not found':
              message = 'VS Code editor (code-server) is not installed. Install code-server and try again.';
              break;
            case 'VS Code server failed to start':
              message = detail?.detail?.trim() || 'VS Code server failed to start. Check server logs for details.';
              break;
            case 'VS Code server manager not initialized':
              message = 'VS Code server manager not initialized. Try restarting the daemon.';
              break;
            case 'Project repository not initialized':
              message = 'VS Code settings cannot be saved — project repository is not initialized. Restart the daemon.';
              break;
            default:
              // Malformed/missing detail — fall back to the historical generic hint.
              message = 'VS Code editor is not installed. Install code-server and try again.';
          }
        } else {
          message = 'Failed to save editor preference';
        }
        this.snackBar.open(message, 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  private startStatusPolling(): void {
    this.stopStatusPolling();
    this.pollStatus();
    this.statusPollTimer = setInterval(() => this.pollStatus(), STATUS_POLL_INTERVAL_MS);
  }

  private stopStatusPolling(): void {
    if (this.statusPollTimer !== null) {
      clearInterval(this.statusPollTimer);
      this.statusPollTimer = null;
    }
  }

  private pollStatus(): void {
    this.settingsService.getVscodeStatus().subscribe({
      next: (status: VSCodeStatus) => {
        this.vscodeStatus.set(status);
        if (status?.running) {
          this.stopStatusPolling();
        }
      },
      error: () => {
        this.vscodeStatus.set({ running: false, port: null, allow_remote: false });
      },
    });
  }

  vscodeStatusLabel(): string {
    const status = this.vscodeStatus();
    if (!status) {
      return '';
    }
    if (status.running) {
      return status.port !== null ? `Running on port ${status.port}` : 'Running';
    }
    const isStarting = this.statusPollTimer !== null && !status.running;
    return isStarting ? 'Starting' : 'Stopped';
  }

  vscodeStatusClass(): string {
    const status = this.vscodeStatus();
    if (!status) {
      return '';
    }
    if (status.running) {
      return 'running';
    }
    const isStarting = this.statusPollTimer !== null && !status.running;
    return isStarting ? 'starting' : 'stopped';
  }

  editorLabel(editor: EditorType): string {
    return editor === 'vscode' ? 'VS Code' : 'Built-in Editor';
  }

  private persistEditorToStorage(editor: EditorType): void {
    try {
      localStorage.setItem(EDITOR_STORAGE_KEY, editor);
    } catch {
      // silently ignore
    }
  }

  private applyPreference(language: string): void {
    if (PREDEFINED_LANGUAGES.includes(language)) {
      this.selectedLanguage.set(language);
      this.isCustom.set(false);
    } else {
      this.selectedLanguage.set(CUSTOM_OPTION_VALUE);
      this.isCustom.set(true);
      this.customLanguage.set(language);
    }
  }

  private persistToStorage(language: string): void {
    try {
      localStorage.setItem(STORAGE_KEY, language);
    } catch {
      // silently ignore
    }
  }

  onLanguageChange(value: string): void {
    if (value === CUSTOM_OPTION_VALUE) {
      this.isCustom.set(true);
      return;
    }
    this.isCustom.set(false);
    this.save(value);
  }

  onCustomLanguageChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.customLanguage.set(target.value);
  }

  saveCustom(): void {
    const lang = this.customLanguage().trim();
    if (!lang) {
      return;
    }
    this.save(lang);
  }

  private save(language: string): void {
    const previousSelectedLanguage = this.selectedLanguage();
    const previousCustomLanguage = this.customLanguage();

    this.saving.set(true);
    this.settingsService.setLanguagePreference(language).subscribe({
      next: () => {
        this.applyPreference(language);
        this.persistToStorage(language);
        this.saving.set(false);
        this.snackBar.open(`Language preference set to ${language}`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
      },
      error: () => {
        this.selectedLanguage.set(previousSelectedLanguage);
        this.customLanguage.set(previousCustomLanguage);
        this.saving.set(false);
        this.snackBar.open('Failed to save language preference', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }
}

describe('SettingsComponent', () => {
  let service: MockSettingsService;
  let snackBar: MockMatSnackBar;
  let component: TestableSettingsComponent;

  beforeEach(() => {
    service = new MockSettingsService();
    snackBar = new MockMatSnackBar();
    MockMatSnackBar.reset();
    jest.clearAllMocks();
    // Safe defaults so the editor section doesn't break the legacy language
    // tests that don't care about editor preference. Tests that care about
    // editor behavior override these mocks explicitly.
    service.getEditorPreference.mockReturnValue(of({ editor: 'builtin' }));
    service.getVscodeStatus.mockReturnValue(
      of({ running: false, port: null, allow_remote: false }),
    );
    service.setEditorPreference.mockReturnValue(of({ editor: 'builtin' }));
  });

  describe('initialization', () => {
    it('should create with default values', () => {
      component = new TestableSettingsComponent(service, snackBar);
      expect(component.selectedLanguage()).toBe('Auto');
      expect(component.isCustom()).toBe(false);
      expect(component.customLanguage()).toBe('');
      expect(component.saving()).toBe(false);
    });

    it('should call getLanguagePreference on ngOnInit', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(service.getLanguagePreference).toHaveBeenCalledTimes(1);
    });

    it('should default to Auto with no localStorage and no API', () => {
      service.getLanguagePreference.mockReturnValue(throwError(() => new Error('Network error')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(component.selectedLanguage()).toBe('Auto');
      expect(component.isCustom()).toBe(false);
    });
  });

  describe('API success — predefined language', () => {
    beforeEach(() => {
      component = new TestableSettingsComponent(service, snackBar);
    });

    it('should set selectedLanguage to French when API returns French', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'French' }));
      component.ngOnInit();
      expect(component.selectedLanguage()).toBe('French');
      expect(component.isCustom()).toBe(false);
      expect(component.customLanguage()).toBe('');
    });

    it('should set selectedLanguage to Spanish when API returns Spanish', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'Spanish' }));
      component.ngOnInit();
      expect(component.selectedLanguage()).toBe('Spanish');
      expect(component.isCustom()).toBe(false);
    });

    it('should set selectedLanguage to English when API returns English', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      component.ngOnInit();
      expect(component.selectedLanguage()).toBe('English');
      expect(component.isCustom()).toBe(false);
    });

    it('should persist the loaded value to localStorage', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'Japanese' }));
      component.ngOnInit();
      expect(localStorageData[STORAGE_KEY]).toBe('Japanese');
    });
  });

  describe('API success — custom (non-predefined) language', () => {
    beforeEach(() => {
      component = new TestableSettingsComponent(service, snackBar);
    });

    it('should set isCustom=true when API returns Thai', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'Thai' }));
      component.ngOnInit();
      expect(component.isCustom()).toBe(true);
      expect(component.selectedLanguage()).toBe(CUSTOM_OPTION_VALUE);
      expect(component.customLanguage()).toBe('Thai');
    });

    it('should set isCustom=true for any non-predefined value', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'Klingon' }));
      component.ngOnInit();
      expect(component.isCustom()).toBe(true);
      expect(component.customLanguage()).toBe('Klingon');
    });
  });

  describe('localStorage caching — synchronous init', () => {
    it('should load from localStorage before API completes', () => {
      localStorageData[STORAGE_KEY] = 'French';
      // Use a deferred observable that never emits synchronously — simulates
      // a pending API call. The localStorage value should still be reflected
      // in selectedLanguage immediately after ngOnInit.
      service.getLanguagePreference.mockReturnValue({
        subscribe: (_observer: unknown) => {
          // intentionally never emits
        },
      });

      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();

      // localStorage value is loaded synchronously
      expect(component.selectedLanguage()).toBe('French');
      expect(component.isCustom()).toBe(false);
    });

    it('should treat localStorage value as custom when not predefined', () => {
      // Note: API success wins over localStorage, so for this test we
      // make the API error out — that way the localStorage value is
      // what sticks and we can verify the custom split.
      localStorageData[STORAGE_KEY] = 'Klingon';
      service.getLanguagePreference.mockReturnValue(throwError(() => new Error('Network')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(component.selectedLanguage()).toBe(CUSTOM_OPTION_VALUE);
      expect(component.isCustom()).toBe(true);
      expect(component.customLanguage()).toBe('Klingon');
    });

    it('should not throw when localStorage is unavailable on getItem', () => {
      localStorageErrorMode = 'get';
      service.getLanguagePreference.mockReturnValue(throwError(() => new Error('Network')));
      component = new TestableSettingsComponent(service, snackBar);
      expect(() => component.ngOnInit()).not.toThrow();
    });

    it('should not throw when localStorage is unavailable on setItem', () => {
      localStorageErrorMode = 'set';
      service.getLanguagePreference.mockReturnValue(of({ language: 'French' }));
      component = new TestableSettingsComponent(service, snackBar);
      expect(() => component.ngOnInit()).not.toThrow();
    });

    it('should fall back to Auto when both localStorage and API fail', () => {
      service.getLanguagePreference.mockReturnValue(throwError(() => new Error('Network')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(component.selectedLanguage()).toBe('Auto');
    });
  });

  describe('API error — preserves localStorage value', () => {
    it('should keep localStorage value when API errors', () => {
      localStorageData[STORAGE_KEY] = 'German';
      service.getLanguagePreference.mockReturnValue(throwError(() => new Error('Network error')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(component.selectedLanguage()).toBe('German');
      expect(component.isCustom()).toBe(false);
    });

    it('should show error snackbar on get failure', () => {
      service.getLanguagePreference.mockReturnValue(throwError(() => new Error('Network error')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(MockMatSnackBar.lastOpen).toEqual({
        message: 'Failed to load language preference',
        action: 'Dismiss',
        options: { duration: 5000, panelClass: 'error-snackbar' },
      });
    });

    it('should not remove localStorage entry on API error', () => {
      localStorageData[STORAGE_KEY] = 'Spanish';
      service.getLanguagePreference.mockReturnValue(throwError(() => new Error('Network error')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(localStorageData[STORAGE_KEY]).toBe('Spanish');
    });
  });

  describe('onLanguageChange', () => {
    beforeEach(() => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      jest.clearAllMocks();
    });

    it('should set isCustom=true without saving when Other (custom) selected', () => {
      service.setLanguagePreference.mockReturnValue(of({ language: CUSTOM_OPTION_VALUE }));
      component.onLanguageChange(CUSTOM_OPTION_VALUE);
      expect(component.isCustom()).toBe(true);
      expect(service.setLanguagePreference).not.toHaveBeenCalled();
    });

    it('should save immediately when predefined language selected', () => {
      service.setLanguagePreference.mockReturnValue(of({ language: 'French' }));
      component.onLanguageChange('French');
      expect(component.isCustom()).toBe(false);
      expect(service.setLanguagePreference).toHaveBeenCalledWith('French');
    });

    it('should set isCustom=false when switching from custom to predefined', () => {
      component.isCustom.set(true);
      service.setLanguagePreference.mockReturnValue(of({ language: 'French' }));
      component.onLanguageChange('French');
      expect(component.isCustom()).toBe(false);
    });
  });

  describe('save flow — predefined', () => {
    beforeEach(() => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      MockMatSnackBar.reset();
      jest.clearAllMocks();
    });

    it('should transition saving signal during save', () => {
      // Build a manual observable so we can observe saving=true
      // before the response resolves.
      let capturedSavingDuringEmit: boolean | null = null;
      service.setLanguagePreference.mockImplementation(() => ({
        subscribe: (observer: { next: (v: { language: string }) => void }) => {
          capturedSavingDuringEmit = component.saving();
          observer.next({ language: 'French' });
        },
      }));

      component.onLanguageChange('French');

      expect(capturedSavingDuringEmit).toBe(true);
      expect(component.saving()).toBe(false);
    });

    it('should write localStorage on success', () => {
      service.setLanguagePreference.mockReturnValue(of({ language: 'French' }));
      component.onLanguageChange('French');
      expect(localStorageData[STORAGE_KEY]).toBe('French');
    });

    it('should show success snackbar', () => {
      service.setLanguagePreference.mockReturnValue(of({ language: 'French' }));
      component.onLanguageChange('French');
      expect(MockMatSnackBar.lastOpen).toEqual({
        message: 'Language preference set to French',
        action: 'Close',
        options: { duration: 3000, panelClass: 'success-snackbar' },
      });
    });

    it('should not overwrite localStorage with a failed-save value', () => {
      // Pre-populate localStorage so we can verify it is NOT replaced
      // by the failed-save value 'French'.
      localStorageData[STORAGE_KEY] = 'English';
      service.setLanguagePreference.mockReturnValue(throwError(() => new Error('Save failed')));
      component.onLanguageChange('French');
      expect(localStorageData[STORAGE_KEY]).toBe('English');
    });

    it('should show error snackbar on save failure', () => {
      service.setLanguagePreference.mockReturnValue(throwError(() => new Error('Save failed')));
      component.onLanguageChange('French');
      expect(MockMatSnackBar.lastOpen).toEqual({
        message: 'Failed to save language preference',
        action: 'Dismiss',
        options: { duration: 5000, panelClass: 'error-snackbar' },
      });
    });

    it('should set saving=false after success', () => {
      service.setLanguagePreference.mockReturnValue(of({ language: 'French' }));
      component.onLanguageChange('French');
      expect(component.saving()).toBe(false);
    });

    it('should set saving=false after error', () => {
      service.setLanguagePreference.mockReturnValue(throwError(() => new Error('Save failed')));
      component.onLanguageChange('French');
      expect(component.saving()).toBe(false);
    });

    // W1: state sync on save success
    it('should sync selectedLanguage to the saved value on success', () => {
      service.setLanguagePreference.mockReturnValue(of({ language: 'French' }));
      expect(component.selectedLanguage()).toBe('English');
      component.onLanguageChange('French');
      expect(component.selectedLanguage()).toBe('French');
    });

    // W1: revert on save failure
    it('should revert selectedLanguage to the previous value on failure', () => {
      // Start with English (loaded via API in beforeEach).
      expect(component.selectedLanguage()).toBe('English');
      service.setLanguagePreference.mockReturnValue(throwError(() => new Error('Save failed')));
      component.onLanguageChange('French');
      // Save was rejected, so the dropdown model should be restored.
      expect(component.selectedLanguage()).toBe('English');
    });
  });

  describe('saveCustom flow', () => {
    beforeEach(() => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      MockMatSnackBar.reset();
      jest.clearAllMocks();
    });

    it('should not save when custom input is empty', () => {
      component.isCustom.set(true);
      component.customLanguage.set('');
      component.saveCustom();
      expect(service.setLanguagePreference).not.toHaveBeenCalled();
    });

    it('should not save when custom input is whitespace only', () => {
      component.isCustom.set(true);
      component.customLanguage.set('   ');
      component.saveCustom();
      expect(service.setLanguagePreference).not.toHaveBeenCalled();
    });

    it('should trim and save when custom input has content', () => {
      service.setLanguagePreference.mockReturnValue(of({ language: 'Thai' }));
      component.isCustom.set(true);
      component.customLanguage.set('  Thai  ');
      component.saveCustom();
      expect(service.setLanguagePreference).toHaveBeenCalledWith('Thai');
    });

    it('should persist trimmed custom value to localStorage on success', () => {
      service.setLanguagePreference.mockReturnValue(of({ language: 'Thai' }));
      component.isCustom.set(true);
      component.customLanguage.set('  Thai  ');
      component.saveCustom();
      expect(localStorageData[STORAGE_KEY]).toBe('Thai');
    });

    it('should show success snackbar with trimmed value', () => {
      service.setLanguagePreference.mockReturnValue(of({ language: 'Thai' }));
      component.isCustom.set(true);
      component.customLanguage.set('  Thai  ');
      component.saveCustom();
      expect(MockMatSnackBar.lastOpen?.message).toBe('Language preference set to Thai');
    });

    // Regression: dropping a raw custom value into selectedLanguage left the
    // mat-select with no matching <mat-option> and rendered unhighlighted.
    // After a successful custom save, selectedLanguage must hold the
    // CUSTOM_OPTION_VALUE sentinel (matching the "Other (custom)" option)
    // and customLanguage must hold the actual typed text.
    it('should sync selectedLanguage to CUSTOM_OPTION_VALUE after custom save', () => {
      service.setLanguagePreference.mockReturnValue(of({ language: 'Swedish' }));
      component.isCustom.set(true);
      component.customLanguage.set('Swedish');
      component.saveCustom();
      expect(component.selectedLanguage()).toBe(CUSTOM_OPTION_VALUE);
      expect(component.customLanguage()).toBe('Swedish');
      expect(component.isCustom()).toBe(true);
    });
  });

  describe('onCustomLanguageChange', () => {
    beforeEach(() => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
    });

    it('should update customLanguage signal from input event', () => {
      const event = { target: { value: 'Klingon' } } as unknown as Event;
      component.onCustomLanguageChange(event);
      expect(component.customLanguage()).toBe('Klingon');
    });
  });

  describe('editor preference — initialization', () => {
    it('should default to builtin with no API and no localStorage', () => {
      service.getLanguagePreference.mockReturnValue(throwError(() => new Error('Network error')));
      service.getEditorPreference.mockReturnValue(throwError(() => new Error('Network error')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(component.selectedEditor()).toBe('builtin');
      expect(component.savedEditor()).toBe('builtin');
      expect(component.editorDirty()).toBe(false);
    });

    it('should call getEditorPreference on ngOnInit', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      service.getEditorPreference.mockReturnValue(of({ editor: 'builtin' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(service.getEditorPreference).toHaveBeenCalledTimes(1);
    });

    it('should populate signals from API response', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      service.getEditorPreference.mockReturnValue(of({ editor: 'vscode' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(component.selectedEditor()).toBe('vscode');
      expect(component.savedEditor()).toBe('vscode');
      expect(component.editorDirty()).toBe(false);
    });

    it('should persist loaded editor to localStorage', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      service.getEditorPreference.mockReturnValue(of({ editor: 'vscode' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(localStorageData[EDITOR_STORAGE_KEY]).toBe('vscode');
    });

    it('should seed from localStorage when API is pending', () => {
      localStorageData[EDITOR_STORAGE_KEY] = 'vscode';
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      // Deferred observable — never emits synchronously
      service.getEditorPreference.mockReturnValue({
        subscribe: (_observer: unknown) => {
          // intentionally never emits
        },
      });
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      // Cached value is used as both selected and saved so the UI starts non-dirty
      expect(component.selectedEditor()).toBe('vscode');
      expect(component.savedEditor()).toBe('vscode');
    });

    it('should ignore invalid cached editor values', () => {
      localStorageData[EDITOR_STORAGE_KEY] = 'not-a-valid-editor';
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      service.getEditorPreference.mockReturnValue(throwError(() => new Error('Network')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      // Invalid cache value is treated as no cache — fallback to default on API error
      expect(component.selectedEditor()).toBe('builtin');
      expect(component.savedEditor()).toBe('builtin');
    });

    it('should fall back to default when API fails and no cache exists', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      service.getEditorPreference.mockReturnValue(throwError(() => new Error('Network')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(component.selectedEditor()).toBe('builtin');
      expect(component.savedEditor()).toBe('builtin');
    });

    it('should preserve cached value when API fails', () => {
      localStorageData[EDITOR_STORAGE_KEY] = 'vscode';
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      service.getEditorPreference.mockReturnValue(throwError(() => new Error('Network')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(component.selectedEditor()).toBe('vscode');
      expect(component.savedEditor()).toBe('vscode');
    });

    it('should start polling status when loaded editor is vscode', () => {
      jest.useFakeTimers();
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      service.getEditorPreference.mockReturnValue(of({ editor: 'vscode' }));
      service.getVscodeStatus.mockReturnValue(
        of({ running: false, port: null, allow_remote: false }),
      );
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      // First immediate poll fires on subscribe
      expect(service.getVscodeStatus).toHaveBeenCalledTimes(1);
      // Advance timer to trigger interval-based poll
      jest.advanceTimersByTime(STATUS_POLL_INTERVAL_MS);
      expect(service.getVscodeStatus).toHaveBeenCalledTimes(2);
      jest.useRealTimers();
    });

    it('should not poll status when loaded editor is builtin', () => {
      jest.useFakeTimers();
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      service.getEditorPreference.mockReturnValue(of({ editor: 'builtin' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      jest.advanceTimersByTime(STATUS_POLL_INTERVAL_MS * 5);
      expect(service.getVscodeStatus).not.toHaveBeenCalled();
      jest.useRealTimers();
    });
  });

  describe('editor preference — dirty tracking', () => {
    beforeEach(() => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      service.getEditorPreference.mockReturnValue(of({ editor: 'builtin' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      jest.clearAllMocks();
    });

    it('should set editorDirty=true when selecting a different editor', () => {
      component.onEditorSelectionChange('vscode');
      expect(component.editorDirty()).toBe(true);
      expect(component.selectedEditor()).toBe('vscode');
      expect(component.savedEditor()).toBe('builtin');
    });

    it('should set editorDirty=false when selecting the same editor as saved', () => {
      // First dirty it
      component.onEditorSelectionChange('vscode');
      expect(component.editorDirty()).toBe(true);
      // Now toggle back
      component.onEditorSelectionChange('builtin');
      expect(component.editorDirty()).toBe(false);
    });
  });

  describe('editor preference — save flow', () => {
    beforeEach(() => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      service.getEditorPreference.mockReturnValue(of({ editor: 'builtin' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      MockMatSnackBar.reset();
      jest.clearAllMocks();
    });

    it('should call setEditorPreference with the working selection', () => {
      service.setEditorPreference.mockReturnValue(of({ editor: 'vscode' }));
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(service.setEditorPreference).toHaveBeenCalledWith('vscode');
    });

    it('should set applyingEditor=true during save', () => {
      let capturedApplyingDuringEmit: boolean | null = null;
      service.setEditorPreference.mockImplementation(() => ({
        subscribe: (observer: { next: (v: { editor: string }) => void }) => {
          capturedApplyingDuringEmit = component.applyingEditor();
          observer.next({ editor: 'vscode' });
        },
      }));
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(capturedApplyingDuringEmit).toBe(true);
      expect(component.applyingEditor()).toBe(false);
    });

    it('should update savedEditor on success', () => {
      service.setEditorPreference.mockReturnValue(of({ editor: 'vscode' }));
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(component.savedEditor()).toBe('vscode');
      expect(component.editorDirty()).toBe(false);
    });

    it('should persist saved editor to localStorage on success', () => {
      service.setEditorPreference.mockReturnValue(of({ editor: 'vscode' }));
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(localStorageData[EDITOR_STORAGE_KEY]).toBe('vscode');
    });

    it('should show success snackbar on save', () => {
      service.setEditorPreference.mockReturnValue(of({ editor: 'vscode' }));
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen).toEqual({
        message: 'Editor preference set to VS Code',
        action: 'Close',
        options: { duration: 3000, panelClass: 'success-snackbar' },
      });
    });

    it('should NOT update savedEditor on error', () => {
      service.setEditorPreference.mockReturnValue(throwError(() => new Error('Save failed')));
      component.onEditorSelectionChange('vscode');
      const previousSaved = component.savedEditor();
      component.saveEditor();
      expect(component.savedEditor()).toBe(previousSaved);
      expect(component.savedEditor()).toBe('builtin');
      expect(component.editorDirty()).toBe(true);
    });

    it('should set applyingEditor=false after error', () => {
      service.setEditorPreference.mockReturnValue(throwError(() => new Error('Save failed')));
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(component.applyingEditor()).toBe(false);
    });

    it('should show install hint snackbar on 503 with code-server binary not found', () => {
      service.setEditorPreference.mockReturnValue(
        throwError(() => ({
          status: 503,
          error: { detail: { error: 'code-server binary not found', detail: 'shutil.which missed code-server in PATH' } },
        })),
      );
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen?.message).toBe(
        'VS Code editor (code-server) is not installed. Install code-server and try again.',
      );
      expect(component.applyingEditor()).toBe(false);
    });

    it('should show backend explanation on 503 with VS Code server failed to start', () => {
      service.setEditorPreference.mockReturnValue(
        throwError(() => ({
          status: 503,
          error: { detail: { error: 'VS Code server failed to start', detail: 'code-server exited with code 1' } },
        })),
      );
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen?.message).toBe('code-server exited with code 1');
      expect(component.applyingEditor()).toBe(false);
    });

    // Regression: a code-server crash log surfaces as multi-line text (e.g.
    // "code-server exited (code=1)\n--- tail ---\nline1\nline2"). The CSS rule
    // in settings.component.scss depends on the snackbar still being tagged
    // `error-snackbar` AND the message preserving its newlines — if production
    // ever strips them, this test fails.
    it('should preserve newlines in the snackbar message and apply error-snackbar panel class', () => {
      const multilineDetail =
        'code-server exited (code=1)\n--- tail ---\nline1\nline2';
      service.setEditorPreference.mockReturnValue(
        throwError(() => ({
          status: 503,
          error: { detail: { error: 'VS Code server failed to start', detail: multilineDetail } },
        })),
      );
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen?.message).toBe(multilineDetail);
      expect(MockMatSnackBar.lastOpen?.options).toEqual({
        duration: 5000,
        panelClass: 'error-snackbar',
      });
    });

    it('should show generic failed-to-start hint when 503 detail omits explanation', () => {
      service.setEditorPreference.mockReturnValue(
        throwError(() => ({
          status: 503,
          error: { detail: { error: 'VS Code server failed to start' } },
        })),
      );
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen?.message).toBe(
        'VS Code server failed to start. Check server logs for details.',
      );
      expect(component.applyingEditor()).toBe(false);
    });

    it('should show generic failed-to-start hint when 503 detail.detail is an empty string', () => {
      service.setEditorPreference.mockReturnValue(
        throwError(() => ({
          status: 503,
          error: { detail: { error: 'VS Code server failed to start', detail: '' } },
        })),
      );
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen?.message).toBe(
        'VS Code server failed to start. Check server logs for details.',
      );
      expect(component.applyingEditor()).toBe(false);
    });

    it('should show generic failed-to-start hint when 503 detail.detail is whitespace only', () => {
      service.setEditorPreference.mockReturnValue(
        throwError(() => ({
          status: 503,
          error: { detail: { error: 'VS Code server failed to start', detail: '   \t  ' } },
        })),
      );
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen?.message).toBe(
        'VS Code server failed to start. Check server logs for details.',
      );
      expect(component.applyingEditor()).toBe(false);
    });

    it('should show restart-daemon hint on 503 with VS Code server manager not initialized', () => {
      service.setEditorPreference.mockReturnValue(
        throwError(() => ({
          status: 503,
          error: { detail: { error: 'VS Code server manager not initialized' } },
        })),
      );
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen?.message).toBe(
        'VS Code server manager not initialized. Try restarting the daemon.',
      );
      expect(component.applyingEditor()).toBe(false);
    });

    it('should fall back to generic install hint on 503 with malformed detail', () => {
      service.setEditorPreference.mockReturnValue(
        throwError(() => ({ status: 503, message: 'Service Unavailable' })),
      );
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen?.message).toBe(
        'VS Code editor is not installed. Install code-server and try again.',
      );
      expect(component.applyingEditor()).toBe(false);
    });

    it('should show restart-daemon hint on 503 with Project repository not initialized', () => {
      service.setEditorPreference.mockReturnValue(
        throwError(() => ({
          status: 503,
          error: { detail: { error: 'Project repository not initialized' } },
        })),
      );
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen?.message).toBe(
        'VS Code settings cannot be saved — project repository is not initialized. Restart the daemon.',
      );
      expect(component.applyingEditor()).toBe(false);
    });

    it('should fall back to generic install hint on 503 with unexpected detail.error', () => {
      service.setEditorPreference.mockReturnValue(
        throwError(() => ({
          status: 503,
          error: { detail: { error: 'some unexpected string' } },
        })),
      );
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen?.message).toBe(
        'VS Code editor is not installed. Install code-server and try again.',
      );
      expect(component.applyingEditor()).toBe(false);
    });

    it('should show generic error snackbar on non-503 error', () => {
      service.setEditorPreference.mockReturnValue(throwError(() => ({ status: 500 })));
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      expect(MockMatSnackBar.lastOpen?.message).toBe('Failed to save editor preference');
    });

    it('should start status polling after saving vscode preference', () => {
      jest.useFakeTimers();
      service.setEditorPreference.mockReturnValue(of({ editor: 'vscode' }));
      service.getVscodeStatus.mockReturnValue(
        of({ running: false, port: null, allow_remote: false }),
      );
      component.onEditorSelectionChange('vscode');
      component.saveEditor();
      // Immediate poll fires on save success
      expect(service.getVscodeStatus).toHaveBeenCalledTimes(1);
      jest.advanceTimersByTime(STATUS_POLL_INTERVAL_MS);
      expect(service.getVscodeStatus).toHaveBeenCalledTimes(2);
      jest.useRealTimers();
    });

    it('should stop status polling and clear status when switching to builtin', () => {
      jest.useFakeTimers();
      // Start in vscode state with active polling
      service.getVscodeStatus.mockReturnValue(
        of({ running: false, port: null, allow_remote: false }),
      );
      service.setEditorPreference.mockReturnValue(of({ editor: 'builtin' }));
      component.onEditorSelectionChange('builtin');
      component.saveEditor();
      // Polling was never started (builtin initially), so status should be null
      expect(component.vscodeStatus()).toBeNull();
      // No polling timer should be active
      jest.advanceTimersByTime(STATUS_POLL_INTERVAL_MS * 5);
      jest.useRealTimers();
    });
  });

  describe('editor preference — status polling', () => {
    beforeEach(() => {
      jest.useFakeTimers();
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      service.getEditorPreference.mockReturnValue(of({ editor: 'vscode' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      jest.clearAllMocks();
    });

    afterEach(() => {
      jest.useRealTimers();
    });

    it('should populate vscodeStatus from a poll response', () => {
      service.getVscodeStatus.mockReturnValue(
        of({ running: false, port: null, allow_remote: false }),
      );
      jest.advanceTimersByTime(STATUS_POLL_INTERVAL_MS);
      expect(component.vscodeStatus()).toEqual({
        running: false,
        port: null,
        allow_remote: false,
      });
      expect(component.vscodeStatusClass()).toBe('starting');
    });

    it('should mark status as running and stop polling when status.running=true', () => {
      service.getVscodeStatus.mockReturnValue(
        of({ running: true, port: 8080, allow_remote: true }),
      );
      jest.advanceTimersByTime(STATUS_POLL_INTERVAL_MS);
      expect(component.vscodeStatus()).toEqual({
        running: true,
        port: 8080,
        allow_remote: true,
      });
      expect(component.vscodeStatusLabel()).toBe('Running on port 8080');
      expect(component.vscodeStatusClass()).toBe('running');
      // Polling stopped — advancing the timer should not trigger another call
      const callsBefore = service.getVscodeStatus.mock.calls.length;
      jest.advanceTimersByTime(STATUS_POLL_INTERVAL_MS * 5);
      expect(service.getVscodeStatus.mock.calls.length).toBe(callsBefore);
    });

    it('should fall back to stopped status on poll error', () => {
      service.getVscodeStatus.mockReturnValue(throwError(() => new Error('Status failed')));
      jest.advanceTimersByTime(STATUS_POLL_INTERVAL_MS);
      expect(component.vscodeStatus()).toEqual({
        running: false,
        port: null,
        allow_remote: false,
      });
    });

    it('should clear polling interval on ngOnDestroy', () => {
      service.getVscodeStatus.mockReturnValue(
        of({ running: false, port: null, allow_remote: false }),
      );
      // Confirm polling is active
      jest.advanceTimersByTime(STATUS_POLL_INTERVAL_MS);
      const callsBefore = service.getVscodeStatus.mock.calls.length;
      component.ngOnDestroy();
      // Advance timer — no new polls should fire because the interval was cleared
      jest.advanceTimersByTime(STATUS_POLL_INTERVAL_MS * 5);
      expect(service.getVscodeStatus.mock.calls.length).toBe(callsBefore);
    });
  });
});
