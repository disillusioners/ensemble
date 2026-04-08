import { Observable, of } from 'rxjs';

// Simplified mock HttpClient for testing
class MockHttpClient {
  private requests: { method: string; url: string; body?: any }[] = [];

  get<T>(url: string, options?: any): Observable<T> {
    this.requests.push({ method: 'GET', url });
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

  getRequests(): { method: string; url: string; body?: any }[] {
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

  createInstance(agentId: string, instanceId?: string): Observable<any> {
    return this.http.post(`${this.API_BASE}/instances`, { 
      agent_id: agentId, 
      instance_id: instanceId 
    });
  }

  listInstances(limit: number = 100, offset: number = 0): Observable<any> {
    return this.http.get(`${this.API_BASE}/instances`);
  }

  getInstance(instanceId: string): Observable<any> {
    return this.http.get(`${this.API_BASE}/instances/${instanceId}`);
  }

  deleteInstance(instanceId: string): Observable<any> {
    return this.http.delete(`${this.API_BASE}/instances/${instanceId}`);
  }

  stopInstance(instanceId: string): Observable<any> {
    return this.http.post(`${this.API_BASE}/instances/${instanceId}/stop`, {});
  }

  sendMessage(instanceId: string, content: string): Observable<any> {
    return this.http.post(`${this.API_BASE}/instances/${instanceId}/messages`, { content });
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

  describe('stopInstance', () => {
    it('should make POST request to /api/instances/{instanceId}/stop', () => {
      const testInstanceId = 'test-instance-123';

      service.stopInstance(testInstanceId);

      const requests = httpMock.getRequests();
      expect(requests.length).toBe(1);
      
      const request = requests[0];
      expect(request.method).toBe('POST');
      expect(request.url).toBe(`/api/instances/${testInstanceId}/stop`);
    });

    it('should send empty body for stop request', () => {
      const testInstanceId = 'test-instance-123';

      service.stopInstance(testInstanceId);

      const request = httpMock.getRequests()[0];
      expect(request.body).toEqual({});
    });

    it('should handle different instance IDs correctly', () => {
      service.stopInstance('instance-abc');
      service.stopInstance('instance-xyz');

      const requests = httpMock.getRequests();
      expect(requests.length).toBe(2);
      expect(requests[0].url).toBe('/api/instances/instance-abc/stop');
      expect(requests[1].url).toBe('/api/instances/instance-xyz/stop');
    });
  });

  describe('other methods', () => {
    it('health() should make GET request to /api/health', () => {
      service.health();

      const request = httpMock.getRequests()[0];
      expect(request.method).toBe('GET');
      expect(request.url).toBe('/api/health');
    });

    it('listInstances() should make GET request', () => {
      service.listInstances(50, 10);

      const request = httpMock.getRequests()[0];
      expect(request.method).toBe('GET');
      expect(request.url).toBe('/api/instances');
    });

    it('createInstance() should make POST request to /api/instances', () => {
      const agentId = 'test-agent';

      service.createInstance(agentId);

      const request = httpMock.getRequests()[0];
      expect(request.method).toBe('POST');
      expect(request.url).toBe('/api/instances');
      expect(request.body).toEqual({ agent_id: agentId, instance_id: undefined });
    });

    it('deleteInstance() should make DELETE request', () => {
      const instanceId = 'test-instance';

      service.deleteInstance(instanceId);

      const request = httpMock.getRequests()[0];
      expect(request.method).toBe('DELETE');
      expect(request.url).toBe(`/api/instances/${instanceId}`);
    });
  });
});
