import { signal } from '@angular/core';
import { of, throwError } from 'rxjs';

// Storage key matching the component
const STORAGE_KEY = 'settings-language-preference';
const CUSTOM_OPTION_VALUE = 'Other (custom)';
const PREDEFINED_LANGUAGES = [
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
}

// Testable SettingsComponent (mirrors actual component for testing)
class TestableSettingsComponent {
  readonly languages = PREDEFINED_LANGUAGES;
  readonly customOptionValue = CUSTOM_OPTION_VALUE;
  readonly selectedLanguage = signal<string>('English');
  readonly customLanguage = signal<string>('');
  readonly isCustom = signal<boolean>(false);
  readonly saving = signal<boolean>(false);
  readonly loaded = signal<boolean>(false);

  constructor(
    private settingsService: MockSettingsService,
    private snackBar: MockMatSnackBar,
  ) {}

  ngOnInit(): void {
    this.loadFromStorage();
    this.loadFromApi();
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
    this.settingsService.getLanguagePreference().subscribe({
      next: (pref: { language: string }) => {
        const lang = pref?.language;
        if (lang) {
          this.applyPreference(lang);
          this.persistToStorage(lang);
        }
        this.loaded.set(true);
      },
      error: () => {
        if (!this.loaded() && !this.selectedLanguage()) {
          this.selectedLanguage.set('English');
        }
        this.loaded.set(true);
        this.snackBar.open('Failed to load language preference', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
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
    this.saving.set(true);
    this.settingsService.setLanguagePreference(language).subscribe({
      next: () => {
        this.persistToStorage(language);
        this.saving.set(false);
        this.snackBar.open(`Language preference set to ${language}`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
      },
      error: () => {
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
  });

  describe('initialization', () => {
    it('should create with default values', () => {
      component = new TestableSettingsComponent(service, snackBar);
      expect(component.selectedLanguage()).toBe('English');
      expect(component.isCustom()).toBe(false);
      expect(component.customLanguage()).toBe('');
      expect(component.saving()).toBe(false);
      expect(component.loaded()).toBe(false);
    });

    it('should call getLanguagePreference on ngOnInit', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'English' }));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(service.getLanguagePreference).toHaveBeenCalledTimes(1);
    });

    it('should default to English with no localStorage and no API', () => {
      service.getLanguagePreference.mockReturnValue(throwError(() => new Error('Network error')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(component.selectedLanguage()).toBe('English');
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

    it('should mark as loaded after API success', () => {
      service.getLanguagePreference.mockReturnValue(of({ language: 'German' }));
      component.ngOnInit();
      expect(component.loaded()).toBe(true);
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
      // Use a deferred observable that doesn't emit synchronously.
      let apiResolved = false;
      service.getLanguagePreference.mockImplementation(() => of({ language: 'English' }).pipe());
      service.getLanguagePreference.mockReturnValue({
        subscribe: (observer: any) => {
          // Don't emit — simulate pending API call
          if (apiResolved) {
            observer.next({ language: 'English' });
            observer.complete();
          }
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

    it('should fall back to English when both localStorage and API fail', () => {
      service.getLanguagePreference.mockReturnValue(throwError(() => new Error('Network')));
      component = new TestableSettingsComponent(service, snackBar);
      component.ngOnInit();
      expect(component.selectedLanguage()).toBe('English');
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
});
