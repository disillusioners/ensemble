import { Observable, of } from 'rxjs';

// Simplified mock HttpClient for testing
class MockHttpClient {
  private requests: { method: string; url: string; body?: any; options?: any }[] = [];

  get<T>(url: string, options?: any): Observable<T> {
    this.requests.push({ method: 'GET', url, options });
    return of(null) as Observable<T>;
  }

  post<T>(url: string, body: any): Observable<T> {
    this.requests.push({ method: 'POST', url, body });
    return of(null) as Observable<T>;
  }

  put<T>(url: string, body: any): Observable<T> {
    this.requests.push({ method: 'PUT', url, body });
    return of(null) as Observable<T>;
  }

  delete<T>(url: string): Observable<T> {
    this.requests.push({ method: 'DELETE', url });
    return of(null) as Observable<T>;
  }

  getRequests(): { method: string; url: string; body?: any; options?: any }[] {
    return this.requests;
  }

  clearRequests(): void {
    this.requests = [];
  }
}

// Testable ApiService implementation (mirrors actual service for testing)
class TestApiService {
  private readonly API_BASE = '/api';

  constructor(private http: MockHttpClient) {}

  health(): Observable<any> {
    return this.http.get(`${this.API_BASE}/health`);
  }

  listAgents(): Observable<any> {
    return this.http.get(`${this.API_BASE}/agents`);
  }

  createAgent(agent: any): Observable<any> {
    return this.http.post(`${this.API_BASE}/agents`, agent);
  }

  deleteAgent(agentId: string): Observable<any> {
    return this.http.delete(`${this.API_BASE}/agents/${agentId}`);
  }

  createInstance(agentId: string, instanceId?: string, projectId?: string): Observable<any> {
    const body: Record<string, string> = { agent_id: agentId };
    if (instanceId) {
      body.instance_id = instanceId;
    }
    if (projectId) {
      body.project_id = projectId;
    }
    return this.http.post(`${this.API_BASE}/instances`, body);
  }

  listInstances(limit: number = 100, offset: number = 0, projectId?: string, excludeKb: boolean = true, search?: string, order?: string): Observable<any> {
    // Mirrors the real ApiService.listInstances param construction
    // (HttpParams set-order serialization: limit, offset, exclude_kb,
    // then optional project_id, search, order).
    const params: string[] = [
      `limit=${limit}`,
      `offset=${offset}`,
      `exclude_kb=${excludeKb}`,
    ];
    if (projectId) {
      params.push(`project_id=${projectId}`);
    }
    if (search && search.trim().length > 0) {
      params.push(`search=${search.trim()}`);
    }
    if (order) {
      params.push(`order=${order}`);
    }
    return this.http.get(`${this.API_BASE}/instances?${params.join('&')}`);
  }

  getInstance(instanceId: string): Observable<any> {
    return this.http.get(`${this.API_BASE}/instances/${instanceId}`);
  }

  deleteInstance(instanceId: string): Observable<any> {
    return this.http.delete(`${this.API_BASE}/instances/${instanceId}`);
  }

  pauseInstance(instanceId: string): Observable<any> {
    return this.http.post(`${this.API_BASE}/instances/${instanceId}/pause`, {});
  }

  sendMessage(instanceId: string, content: string, images?: string[]): Observable<any> {
    const body = images?.length ? { content, images } : { content };
    return this.http.post(`${this.API_BASE}/instances/${instanceId}/messages`, body);
  }

  getMessages(instanceId: string): Observable<any> {
    return this.http.get(`${this.API_BASE}/instances/${instanceId}/messages`);
  }
}

describe('ApiService', () => {
  let httpMock: MockHttpClient;
  let service: TestApiService;

  beforeEach(() => {
    httpMock = new MockHttpClient();
    service = new TestApiService(httpMock);
  });

  describe('pauseInstance', () => {
    it('should make POST request to /api/instances/{instanceId}/pause', () => {
      const testInstanceId = 'test-instance-123';

      service.pauseInstance(testInstanceId);

      const requests = httpMock.getRequests();
      expect(requests.length).toBe(1);
      
      const request = requests[0];
      expect(request.method).toBe('POST');
      expect(request.url).toBe(`/api/instances/${testInstanceId}/pause`);
    });

    it('should send empty body for pause request', () => {
      const testInstanceId = 'test-instance-123';

      service.pauseInstance(testInstanceId);

      const request = httpMock.getRequests()[0];
      expect(request.body).toEqual({});
    });

    it('should handle different instance IDs correctly', () => {
      service.pauseInstance('instance-abc');
      service.pauseInstance('instance-xyz');

      const requests = httpMock.getRequests();
      expect(requests.length).toBe(2);
      expect(requests[0].url).toBe('/api/instances/instance-abc/pause');
      expect(requests[1].url).toBe('/api/instances/instance-xyz/pause');
    });
  });

  describe('sendMessage', () => {
    it('should make POST request to /api/instances/{instanceId}/messages', () => {
      const testInstanceId = 'test-instance-123';
      const testContent = 'Hello, world!';

      service.sendMessage(testInstanceId, testContent);

      const requests = httpMock.getRequests();
      expect(requests.length).toBe(1);
      
      const request = requests[0];
      expect(request.method).toBe('POST');
      expect(request.url).toBe(`/api/instances/${testInstanceId}/messages`);
    });

    it('should send just content when no images provided', () => {
      const testInstanceId = 'test-instance-123';
      const testContent = 'Hello!';

      service.sendMessage(testInstanceId, testContent);

      const request = httpMock.getRequests()[0];
      expect(request.body).toEqual({ content: testContent });
    });

    it('should send content with images when images provided', () => {
      const testInstanceId = 'test-instance-123';
      const testContent = 'Check this out!';
      const testImages = ['data:image/png;base64,abc123', 'data:image/png;base64,def456'];

      service.sendMessage(testInstanceId, testContent, testImages);

      const request = httpMock.getRequests()[0];
      expect(request.body).toEqual({ content: testContent, images: testImages });
    });

    it('should send just content when images array is empty', () => {
      const testInstanceId = 'test-instance-123';
      const testContent = 'Hello!';
      const emptyImages: string[] = [];

      service.sendMessage(testInstanceId, testContent, emptyImages);

      const request = httpMock.getRequests()[0];
      expect(request.body).toEqual({ content: testContent });
    });

    it('should send content with images array even if it contains empty strings', () => {
      const testInstanceId = 'test-instance-123';
      const testContent = 'Hello!';
      const emptyImages = [''];

      service.sendMessage(testInstanceId, testContent, emptyImages);

      // Implementation checks array length, not content - empty strings still included
      const request = httpMock.getRequests()[0];
      expect(request.body).toEqual({ content: testContent, images: emptyImages });
    });

    it('should include single image correctly', () => {
      const testInstanceId = 'instance-abc';
      const testContent = 'Here is one image';
      const singleImage = ['data:image/jpeg;base64,singleimage'];

      service.sendMessage(testInstanceId, testContent, singleImage);

      const request = httpMock.getRequests()[0];
      expect(request.body).toEqual({ content: testContent, images: singleImage });
    });
  });

  describe('other methods', () => {
    it('health() should make GET request to /api/health', () => {
      service.health();

      const request = httpMock.getRequests()[0];
      expect(request.method).toBe('GET');
      expect(request.url).toBe('/api/health');
    });

    it('listInstances() should make GET request with base params', () => {
      service.listInstances(50, 10);

      const request = httpMock.getRequests()[0];
      expect(request.method).toBe('GET');
      expect(request.url).toBe('/api/instances?limit=50&offset=10&exclude_kb=true');
    });

    it('createInstance() should make POST request to /api/instances', () => {
      const agentId = 'test-agent';

      service.createInstance(agentId);

      const request = httpMock.getRequests()[0];
      expect(request.method).toBe('POST');
      expect(request.url).toBe('/api/instances');
      expect(request.body).toEqual({ agent_id: agentId });
    });

    it('createInstance() should include project_id when provided', () => {
      const agentId = 'test-agent';
      const projectId = 'project-123';

      service.createInstance(agentId, undefined, projectId);

      const request = httpMock.getRequests()[0];
      expect(request.method).toBe('POST');
      expect(request.url).toBe('/api/instances');
      expect(request.body).toEqual({ agent_id: agentId, project_id: projectId });
    });

    it('createInstance() should not include project_id when not provided', () => {
      const agentId = 'test-agent';

      service.createInstance(agentId);

      const request = httpMock.getRequests()[0];
      expect(request.body).not.toHaveProperty('project_id');
    });

    it('deleteInstance() should make DELETE request', () => {
      const instanceId = 'test-instance';

      service.deleteInstance(instanceId);

      const request = httpMock.getRequests()[0];
      expect(request.method).toBe('DELETE');
      expect(request.url).toBe(`/api/instances/${instanceId}`);
    });
  });

  describe('listInstances with exclude_kb', () => {
    it('should send exclude_kb query param as false when explicitly set', () => {
      service.listInstances(100, 0, undefined, false);

      // Mirror now builds the query string like the real implementation.
      const request = httpMock.getRequests()[0];
      expect(request.method).toBe('GET');
      expect(request.url).toBe('/api/instances?limit=100&offset=0&exclude_kb=false');
    });

    it('should default exclude_kb to true', () => {
      // Default call without excludeKb param
      service.listInstances(100, 0);

      const request = httpMock.getRequests()[0];
      expect(request.method).toBe('GET');
      expect(request.url).toContain('exclude_kb=true');
    });

    it('should accept excludeKb parameter explicitly', () => {
      service.listInstances(50, 10, undefined, true);
      service.listInstances(50, 10, undefined, false);

      const requests = httpMock.getRequests();
      expect(requests.length).toBe(2);
      expect(requests[0].url).toContain('exclude_kb=true');
      expect(requests[1].url).toContain('exclude_kb=false');
    });
  });

  describe('listInstances with order param', () => {
    it('should append order=activity to the URL (SPEC PIN)', () => {
      // The job-queue panel's listInstanceTree asks the BE for
      // activity ordering — the constructed URL MUST carry it.
      service.listInstances(10, 0, undefined, true, undefined, 'activity');

      const request = httpMock.getRequests()[0];
      expect(request.url).toContain('order=activity');
      expect(request.url).toBe('/api/instances?limit=10&offset=0&exclude_kb=true&order=activity');
    });

    it('should omit the order param when not provided (server default)', () => {
      service.listInstances(100, 0);

      const request = httpMock.getRequests()[0];
      expect(request.url).not.toContain('order=');
    });

    it('should keep limit/offset/exclude_kb alongside order', () => {
      service.listInstances(25, 0, undefined, true, undefined, 'pinned');

      const request = httpMock.getRequests()[0];
      expect(request.url).toBe('/api/instances?limit=25&offset=0&exclude_kb=true&order=pinned');
    });
  });
});
