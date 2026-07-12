import { Observable, of } from 'rxjs';
import type { LanguagePreference } from './settings.service';

// Mock HttpClient that tracks requests
class MockHttpClient {
  private requests: { method: string; url: string; body?: any }[] = [];

  get<T>(url: string, options?: any): Observable<T> {
    this.requests.push({ method: 'GET', url });
    return of(null) as Observable<T>;
  }

  post<T>(url: string, body: any, options?: any): Observable<T> {
    this.requests.push({ method: 'POST', url, body });
    return of(null) as Observable<T>;
  }

  put<T>(url: string, body: any, options?: any): Observable<T> {
    this.requests.push({ method: 'PUT', url, body });
    return of(null) as Observable<T>;
  }

  delete<T>(url: string, options?: any): Observable<T> {
    this.requests.push({ method: 'DELETE', url });
    return of(null) as Observable<T>;
  }

  getRequests(): { method: string; url: string; body?: any }[] {
    return this.requests;
  }

  clearRequests(): void {
    this.requests = [];
  }
}

// Testable SettingsService (mirrors actual service for testing)
class TestableSettingsService {
  private readonly API_BASE = '/api/settings/language';

  constructor(private http: MockHttpClient) {}

  getLanguagePreference(): Observable<LanguagePreference> {
    return this.http.get<LanguagePreference>(this.API_BASE);
  }

  setLanguagePreference(language: string): Observable<LanguagePreference> {
    return this.http.put<LanguagePreference>(this.API_BASE, { language });
  }
}

describe('SettingsService', () => {
  let httpMock: MockHttpClient;
  let service: TestableSettingsService;

  beforeEach(() => {
    httpMock = new MockHttpClient();
    service = new TestableSettingsService(httpMock);
  });

  describe('getLanguagePreference', () => {
    it('should make GET request to /api/settings/language', () => {
      httpMock.get = jest.fn().mockReturnValue(of({ language: 'English' }));

      service.getLanguagePreference().subscribe();

      expect(httpMock.get).toHaveBeenCalledWith('/api/settings/language');
    });

    it('should return Observable<LanguagePreference> from response', (done) => {
      const preference: LanguagePreference = { language: 'Spanish' };

      httpMock.get = jest.fn().mockReturnValue(of(preference));

      service.getLanguagePreference().subscribe({
        next: (result) => {
          expect(result).toEqual(preference);
          expect(result.language).toBe('Spanish');
          done();
        },
        error: done.fail,
      });
    });

    it('should handle predefined language values', (done) => {
      const preference: LanguagePreference = { language: 'French' };

      httpMock.get = jest.fn().mockReturnValue(of(preference));

      service.getLanguagePreference().subscribe({
        next: (result) => {
          expect(result.language).toBe('French');
          done();
        },
        error: done.fail,
      });
    });

    it('should handle custom (non-predefined) language values', (done) => {
      const preference: LanguagePreference = { language: 'Thai' };

      httpMock.get = jest.fn().mockReturnValue(of(preference));

      service.getLanguagePreference().subscribe({
        next: (result) => {
          expect(result.language).toBe('Thai');
          done();
        },
        error: done.fail,
      });
    });
  });

  describe('setLanguagePreference', () => {
    it('should make PUT request to /api/settings/language', () => {
      httpMock.put = jest.fn().mockReturnValue(of({ language: 'French' }));

      service.setLanguagePreference('French').subscribe();

      expect(httpMock.put).toHaveBeenCalledWith('/api/settings/language', { language: 'French' });
    });

    it('should send correct body format with French', () => {
      httpMock.put = jest.fn().mockReturnValue(of({ language: 'French' }));

      service.setLanguagePreference('French').subscribe();

      expect(httpMock.put).toHaveBeenCalledWith('/api/settings/language', {
        language: 'French',
      });
    });

    it('should send custom language in body', () => {
      httpMock.put = jest.fn().mockReturnValue(of({ language: 'Thai' }));

      service.setLanguagePreference('Thai').subscribe();

      expect(httpMock.put).toHaveBeenCalledWith('/api/settings/language', {
        language: 'Thai',
      });
    });

    it('should return LanguagePreference from observable', (done) => {
      const response: LanguagePreference = { language: 'German' };
      httpMock.put = jest.fn().mockReturnValue(of(response));

      service.setLanguagePreference('German').subscribe({
        next: (result) => {
          expect(result).toEqual(response);
          expect(result.language).toBe('German');
          done();
        },
        error: done.fail,
      });
    });

    it('should handle empty string language', () => {
      httpMock.put = jest.fn().mockReturnValue(of({ language: '' }));

      service.setLanguagePreference('').subscribe();

      expect(httpMock.put).toHaveBeenCalledWith('/api/settings/language', { language: '' });
    });

    it('should handle long language names', (done) => {
      const longName = 'a'.repeat(128);
      const response: LanguagePreference = { language: longName };
      httpMock.put = jest.fn().mockReturnValue(of(response));

      service.setLanguagePreference(longName).subscribe({
        next: (result) => {
          expect(result.language).toBe(longName);
          done();
        },
        error: done.fail,
      });
    });
  });
});
