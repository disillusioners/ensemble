import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { SettingsService, LanguagePreference } from './settings.service';

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
});
