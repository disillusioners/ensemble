import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { SettingsService, LanguagePreference, TimezonePreference } from './settings.service';

describe('SettingsService', () => {
  let service: SettingsService;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(SettingsService);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    // Verify that no unmatched requests are outstanding after each test.
    httpTesting.verify();
  });

  describe('getLanguagePreference', () => {
    it('should send GET to /api/settings/language and return the response', (done) => {
      const mockResponse: LanguagePreference = { language: 'English' };

      service.getLanguagePreference().subscribe({
        next: (result) => {
          expect(result).toEqual(mockResponse);
          expect(result.language).toBe('English');
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne('/api/settings/language');
      expect(req.request.method).toBe('GET');
      expect(req.request.body).toBeNull();
      req.flush(mockResponse);
    });

    it('should propagate backend errors', (done) => {
      service.getLanguagePreference().subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(err.status).toBe(500);
          done();
        },
      });

      const req = httpTesting.expectOne('/api/settings/language');
      expect(req.request.method).toBe('GET');
      req.flush('Server error', { status: 500, statusText: 'Server Error' });
    });
  });

  describe('setLanguagePreference', () => {
    it("should send PUT to /api/settings/language with body { language: 'Spanish' }", (done) => {
      const mockResponse: LanguagePreference = { language: 'Spanish' };

      service.setLanguagePreference('Spanish').subscribe({
        next: (result) => {
          expect(result).toEqual(mockResponse);
          expect(result.language).toBe('Spanish');
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne('/api/settings/language');
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toEqual({ language: 'Spanish' });
      req.flush(mockResponse);
    });

    it('should send the exact language string in the PUT body', (done) => {
      const customLanguage = 'Thai';

      service.setLanguagePreference(customLanguage).subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne('/api/settings/language');
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toEqual({ language: customLanguage });
      req.flush({ language: customLanguage });
    });

    it('should propagate backend errors on PUT', (done) => {
      service.setLanguagePreference('Spanish').subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(err.status).toBe(400);
          done();
        },
      });

      const req = httpTesting.expectOne('/api/settings/language');
      expect(req.request.method).toBe('PUT');
      req.flush('Bad request', { status: 400, statusText: 'Bad Request' });
    });
  });

  describe('getTimezonePreference', () => {
    it('should send GET to /api/settings/timezone and return the response', (done) => {
      const mockResponse: TimezonePreference = {
        timezone: 'Asia/Bangkok',
        utc_offset: '+07:00',
      };

      service.getTimezonePreference().subscribe({
        next: (result) => {
          expect(result).toEqual(mockResponse);
          expect(result.timezone).toBe('Asia/Bangkok');
          expect(result.utc_offset).toBe('+07:00');
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne('/api/settings/timezone');
      expect(req.request.method).toBe('GET');
      expect(req.request.body).toBeNull();
      req.flush(mockResponse);
    });

    it('should accept the null/unset response shape', (done) => {
      const mockResponse: TimezonePreference = { timezone: null, utc_offset: null };

      service.getTimezonePreference().subscribe({
        next: (result) => {
          expect(result).toEqual(mockResponse);
          expect(result.timezone).toBeNull();
          expect(result.utc_offset).toBeNull();
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne('/api/settings/timezone');
      expect(req.request.method).toBe('GET');
      req.flush(mockResponse);
    });

    it('should propagate backend errors on GET', (done) => {
      service.getTimezonePreference().subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(err.status).toBe(500);
          done();
        },
      });

      const req = httpTesting.expectOne('/api/settings/timezone');
      expect(req.request.method).toBe('GET');
      req.flush('Server error', { status: 500, statusText: 'Server Error' });
    });
  });

  describe('setTimezonePreference', () => {
    it("should send PUT to /api/settings/timezone with body { timezone: 'Asia/Bangkok' }", (done) => {
      const mockResponse: TimezonePreference = {
        timezone: 'Asia/Bangkok',
        utc_offset: '+07:00',
      };

      service.setTimezonePreference('Asia/Bangkok').subscribe({
        next: (result) => {
          expect(result).toEqual(mockResponse);
          expect(result.timezone).toBe('Asia/Bangkok');
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne('/api/settings/timezone');
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toEqual({ timezone: 'Asia/Bangkok' });
      req.flush(mockResponse);
    });

    it('should send body { timezone: null } to clear the setting', (done) => {
      const mockResponse: TimezonePreference = { timezone: null, utc_offset: null };

      service.setTimezonePreference(null).subscribe({
        next: (result) => {
          expect(result).toEqual(mockResponse);
          expect(result.timezone).toBeNull();
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne('/api/settings/timezone');
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toEqual({ timezone: null });
      req.flush(mockResponse);
    });

    it('should propagate 4xx backend errors (invalid IANA)', (done) => {
      service.setTimezonePreference('Not/A/Real/Zone').subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(err.status).toBe(422);
          done();
        },
      });

      const req = httpTesting.expectOne('/api/settings/timezone');
      expect(req.request.method).toBe('PUT');
      req.flush('Invalid timezone', {
        status: 422,
        statusText: 'Unprocessable Entity',
      });
    });
  });

  describe('getTimezoneOptions', () => {
    it('should send GET to /api/settings/timezones and return the response', (done) => {
      // Spot-check that the canonical Asia/Ho_Chi_Minh (missing on
      // older Intl.supportedValuesOf) reaches the caller — the whole
      // reason the endpoint exists.
      const mockResponse = {
        timezones: ['Asia/Ho_Chi_Minh', 'Asia/Bangkok', 'UTC'],
      };

      service.getTimezoneOptions().subscribe({
        next: (result) => {
          expect(result).toEqual(mockResponse);
          expect(result.timezones).toContain('Asia/Ho_Chi_Minh');
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne('/api/settings/timezones');
      expect(req.request.method).toBe('GET');
      expect(req.request.body).toBeNull();
      req.flush(mockResponse);
    });

    it('should propagate backend errors (the picker falls back to Intl)', (done) => {
      service.getTimezoneOptions().subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(err.status).toBe(500);
          done();
        },
      });

      const req = httpTesting.expectOne('/api/settings/timezones');
      req.flush('Server error', { status: 500, statusText: 'Server Error' });
    });
  });
});
