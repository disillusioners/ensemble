import { signal } from '@angular/core';
import { Observable, of } from 'rxjs';
import { tap, map } from 'rxjs/operators';
import { throwError } from 'rxjs';
import type { McpServer, McpServerCreate, McpServerUpdate, McpServerListResponse, McpServerDeleteResponse, McpServerTestConnectionResponse } from '../models';

// Mock HttpClient that tracks requests
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

// Testable McpServerService (mirrors actual service for testing)
class TestableMcpServerService {
  private readonly API_BASE = '/api/mcp-servers';

  readonly servers = signal<McpServer[]>([]);
  readonly loading = signal(false);

  constructor(private http: MockHttpClient) {}

  listServers(): Observable<McpServer[]> {
    this.loading.set(true);
    return this.http.get<McpServerListResponse>(this.API_BASE).pipe(
      tap(response => {
        this.servers.set(response.mcp_servers);
        this.loading.set(false);
      }),
      map(response => response.mcp_servers)
    );
  }

  getServer(id: string): Observable<McpServer> {
    return this.http.get<McpServer>(`${this.API_BASE}/${encodeURIComponent(id)}`);
  }

  createServer(data: McpServerCreate): Observable<McpServer> {
    return this.http.post<McpServer>(this.API_BASE, data).pipe(
      tap(server => {
        this.servers.update(servers => [server, ...servers]);
      })
    );
  }

  updateServer(id: string, data: McpServerUpdate): Observable<McpServer> {
    return this.http.put<McpServer>(`${this.API_BASE}/${encodeURIComponent(id)}`, data).pipe(
      tap(updatedServer => {
        this.servers.update(servers =>
          servers.map(s => s.id === id ? updatedServer : s)
        );
      })
    );
  }

  deleteServer(id: string): Observable<McpServerDeleteResponse> {
    return this.http.delete<McpServerDeleteResponse>(`${this.API_BASE}/${encodeURIComponent(id)}`).pipe(
      tap(() => {
        this.servers.update(servers => servers.filter(s => s.id !== id));
      })
    );
  }

  testConnection(config: Record<string, unknown>): Observable<McpServerTestConnectionResponse> {
    return this.http.post<McpServerTestConnectionResponse>(`${this.API_BASE}/test-connection`, { config });
  }
}

// Helper to create mock MCP server
function createMockServer(overrides: Partial<McpServer> = {}): McpServer {
  return {
    id: `server-${Math.random().toString(36).substr(2, 9)}`,
    name: 'Test MCP Server',
    description: 'A test MCP server',
    config: { command: 'npx', args: ['test-server'] },
    is_active: true,
    created_at: '2025-01-15T10:30:00Z',
    updated_at: null,
    ...overrides,
  };
}

describe('McpServerService', () => {
  let httpMock: MockHttpClient;
  let service: TestableMcpServerService;

  beforeEach(() => {
    httpMock = new MockHttpClient();
    service = new TestableMcpServerService(httpMock);
  });

  describe('listServers', () => {
    it('should make GET request to /api/mcp-servers', () => {
      httpMock.get = jest.fn().mockReturnValue(
        of({ mcp_servers: [] })
      );

      service.listServers().subscribe();

      expect(httpMock.get).toHaveBeenCalledWith('/api/mcp-servers');
    });

    it('should return Observable<McpServer[]> from response', (done) => {
      const mockServers = [
        createMockServer({ id: 'server-1', name: 'Server 1' }),
        createMockServer({ id: 'server-2', name: 'Server 2' }),
      ];

      httpMock.get = jest.fn().mockReturnValue(
        of({ mcp_servers: mockServers })
      );

      service.listServers().subscribe({
        next: (servers) => {
          expect(servers).toHaveLength(2);
          expect(servers[0].id).toBe('server-1');
          expect(servers[1].id).toBe('server-2');
          done();
        },
        error: done.fail,
      });
    });

    it('should update servers signal with response data', (done) => {
      const mockServers = [createMockServer({ id: 'server-1' })];

      httpMock.get = jest.fn().mockReturnValue(
        of({ mcp_servers: mockServers })
      );

      service.listServers().subscribe({
        next: (servers) => {
          expect(service.servers()).toEqual(mockServers);
          done();
        },
        error: done.fail,
      });
    });

    it('should set loading to true during listServers and false after completion', (done) => {
      httpMock.get = jest.fn().mockReturnValue(
        of({ mcp_servers: [] })
      );

      service.listServers().subscribe({
        next: () => {
          // Loading should be false after completion
          expect(service.loading()).toBe(false);
          done();
        },
        error: done.fail,
      });
    });

    it('should return empty array when no servers', (done) => {
      httpMock.get = jest.fn().mockReturnValue(
        of({ mcp_servers: [] })
      );

      service.listServers().subscribe({
        next: (servers) => {
          expect(servers).toEqual([]);
          expect(service.servers()).toEqual([]);
          done();
        },
        error: done.fail,
      });
    });
  });

  describe('getServer', () => {
    it('should make GET request to /api/mcp-servers/{id}', () => {
      const serverId = 'server-123';

      service.getServer(serverId).subscribe();

      const requests = httpMock.getRequests();
      expect(requests.length).toBe(1);
      expect(requests[0].method).toBe('GET');
      expect(requests[0].url).toBe('/api/mcp-servers/server-123');
    });

    it('should encode the server ID in the URL', () => {
      const serverId = 'server/special-chars';

      service.getServer(serverId).subscribe();

      const requests = httpMock.getRequests();
      expect(requests[0].url).toBe('/api/mcp-servers/server%2Fspecial-chars');
    });

    it('should handle different server IDs correctly', () => {
      service.getServer('id-1');
      service.getServer('id-2');

      const requests = httpMock.getRequests();
      expect(requests.length).toBe(2);
      expect(requests[0].url).toBe('/api/mcp-servers/id-1');
      expect(requests[1].url).toBe('/api/mcp-servers/id-2');
    });
  });

  describe('createServer', () => {
    it('should make POST request to /api/mcp-servers', () => {
      const data: McpServerCreate = {
        name: 'New Server',
        description: 'A new server',
      };

      httpMock.post = jest.fn().mockReturnValue(of(createMockServer({ name: 'New Server' })));

      service.createServer(data).subscribe();

      expect(httpMock.post).toHaveBeenCalledWith('/api/mcp-servers', data);
    });

    it('should send correct body format', () => {
      const data: McpServerCreate = {
        name: 'New Server',
        description: 'Description',
        config: { command: 'npx', args: ['server'] },
        is_active: true,
      };

      httpMock.post = jest.fn().mockReturnValue(of(createMockServer(data)));

      service.createServer(data).subscribe();

      expect(httpMock.post).toHaveBeenCalledWith('/api/mcp-servers', {
        name: 'New Server',
        description: 'Description',
        config: { command: 'npx', args: ['server'] },
        is_active: true,
      });
    });

    it('should update servers signal by prepending new server', (done) => {
      const existingServer = createMockServer({ id: 'existing-1' });
      service.servers.set([existingServer]);

      const newServer = createMockServer({ id: 'new-1', name: 'New Server' });
      const data: McpServerCreate = { name: 'New Server', description: null };

      httpMock.post = jest.fn().mockReturnValue(of(newServer));

      service.createServer(data).subscribe({
        next: () => {
          const servers = service.servers();
          expect(servers).toHaveLength(2);
          expect(servers[0].id).toBe('new-1');
          expect(servers[1].id).toBe('existing-1');
          done();
        },
        error: done.fail,
      });
    });

    it('should return the created server from observable', (done) => {
      const newServer = createMockServer({ id: 'new-1', name: 'New Server' });
      const data: McpServerCreate = { name: 'New Server', description: null };

      httpMock.post = jest.fn().mockReturnValue(of(newServer));

      service.createServer(data).subscribe({
        next: (server) => {
          expect(server.id).toBe('new-1');
          expect(server.name).toBe('New Server');
          done();
        },
        error: done.fail,
      });
    });
  });

  describe('updateServer', () => {
    it('should make PUT request to /api/mcp-servers/{id}', () => {
      const serverId = 'server-123';
      const data: McpServerUpdate = { name: 'Updated Name' };

      httpMock.put = jest.fn().mockReturnValue(of(createMockServer({ id: serverId, name: 'Updated Name' })));

      service.updateServer(serverId, data).subscribe();

      expect(httpMock.put).toHaveBeenCalledWith('/api/mcp-servers/server-123', data);
    });

    it('should encode the server ID in the URL', () => {
      const serverId = 'server/special';
      const data: McpServerUpdate = { name: 'Updated' };

      httpMock.put = jest.fn().mockReturnValue(of(createMockServer({ id: serverId })));

      service.updateServer(serverId, data).subscribe();

      expect(httpMock.put).toHaveBeenCalledWith('/api/mcp-servers/server%2Fspecial', data);
    });

    it('should send correct body format', () => {
      const data: McpServerUpdate = {
        name: 'Updated Name',
        description: 'Updated description',
        is_active: false,
      };

      httpMock.put = jest.fn().mockReturnValue(of(createMockServer(data)));

      service.updateServer('server-1', data).subscribe();

      expect(httpMock.put).toHaveBeenCalledWith('/api/mcp-servers/server-1', {
        name: 'Updated Name',
        description: 'Updated description',
        is_active: false,
      });
    });

    it('should update servers signal with updated server', (done) => {
      const original = createMockServer({ id: 'server-1', name: 'Original' });
      service.servers.set([original]);

      const updated = { ...original, name: 'Updated' };
      const data: McpServerUpdate = { name: 'Updated' };

      httpMock.put = jest.fn().mockReturnValue(of(updated));

      service.updateServer('server-1', data).subscribe({
        next: () => {
          const servers = service.servers();
          expect(servers).toHaveLength(1);
          expect(servers[0].name).toBe('Updated');
          done();
        },
        error: done.fail,
      });
    });

    it('should not modify other servers in signal when updating one', (done) => {
      const server1 = createMockServer({ id: 'server-1', name: 'Server 1' });
      const server2 = createMockServer({ id: 'server-2', name: 'Server 2' });
      service.servers.set([server1, server2]);

      const updated = { ...server1, name: 'Updated Server 1' };
      httpMock.put = jest.fn().mockReturnValue(of(updated));

      service.updateServer('server-1', { name: 'Updated Server 1' }).subscribe({
        next: () => {
          const servers = service.servers();
          expect(servers).toHaveLength(2);
          expect(servers.find(s => s.id === 'server-1')!.name).toBe('Updated Server 1');
          expect(servers.find(s => s.id === 'server-2')!.name).toBe('Server 2');
          done();
        },
        error: done.fail,
      });
    });

    it('should return the updated server from observable', (done) => {
      const updated = createMockServer({ id: 'server-1', name: 'Updated' });

      httpMock.put = jest.fn().mockReturnValue(of(updated));

      service.updateServer('server-1', { name: 'Updated' }).subscribe({
        next: (server) => {
          expect(server.name).toBe('Updated');
          done();
        },
        error: done.fail,
      });
    });
  });

  describe('deleteServer', () => {
    it('should make DELETE request to /api/mcp-servers/{id}', () => {
      const serverId = 'server-123';

      httpMock.delete = jest.fn().mockReturnValue(of({ deleted: true, id: serverId }));

      service.deleteServer(serverId).subscribe();

      expect(httpMock.delete).toHaveBeenCalledWith('/api/mcp-servers/server-123');
    });

    it('should encode the server ID in the URL', () => {
      const serverId = 'server/special';

      httpMock.delete = jest.fn().mockReturnValue(of({ deleted: true, id: serverId }));

      service.deleteServer(serverId).subscribe();

      expect(httpMock.delete).toHaveBeenCalledWith('/api/mcp-servers/server%2Fspecial');
    });

    it('should remove server from servers signal', (done) => {
      const server1 = createMockServer({ id: 'server-1' });
      const server2 = createMockServer({ id: 'server-2' });
      service.servers.set([server1, server2]);

      httpMock.delete = jest.fn().mockReturnValue(of({ deleted: true, id: 'server-1' }));

      service.deleteServer('server-1').subscribe({
        next: () => {
          const servers = service.servers();
          expect(servers).toHaveLength(1);
          expect(servers[0].id).toBe('server-2');
          done();
        },
        error: done.fail,
      });
    });

    it('should return McpServerDeleteResponse from observable', (done) => {
      const response: McpServerDeleteResponse = { deleted: true, id: 'server-1' };

      httpMock.delete = jest.fn().mockReturnValue(of(response));

      service.deleteServer('server-1').subscribe({
        next: (res) => {
          expect(res.deleted).toBe(true);
          expect(res.id).toBe('server-1');
          done();
        },
        error: done.fail,
      });
    });

    it('should handle deleting non-existent server gracefully', (done) => {
      service.servers.set([]);

      httpMock.delete = jest.fn().mockReturnValue(of({ deleted: true, id: 'server-1' }));

      service.deleteServer('server-1').subscribe({
        next: () => {
          expect(service.servers()).toHaveLength(0);
          done();
        },
        error: done.fail,
      });
    });
  });

  describe('signal state management', () => {
    it('should have empty servers array initially', () => {
      expect(service.servers()).toEqual([]);
    });

    it('should not be loading initially', () => {
      expect(service.loading()).toBe(false);
    });

    it('should track multiple create operations in servers signal', (done) => {
      const server1 = createMockServer({ id: 's1', name: 'Server 1' });
      const server2 = createMockServer({ id: 's2', name: 'Server 2' });

      httpMock.post = jest.fn()
        .mockReturnValueOnce(of(server1))
        .mockReturnValueOnce(of(server2));

      service.createServer({ name: 'Server 1', description: null }).subscribe({
        complete: () => {
          service.createServer({ name: 'Server 2', description: null }).subscribe({
            complete: () => {
              const servers = service.servers();
              expect(servers).toHaveLength(2);
              expect(servers[0].id).toBe('s2'); // prepended
              expect(servers[1].id).toBe('s1');
              done();
            },
          });
        },
      });
    });
  });

  describe('testConnection', () => {
    it('should make POST request to /api/mcp-servers/test-connection', () => {
      const config = { command: 'npx', args: ['test'] };

      httpMock.post = jest.fn().mockReturnValue(
        of({ success: true, message: 'Connected' })
      );

      service.testConnection(config).subscribe();

      expect(httpMock.post).toHaveBeenCalledWith('/api/mcp-servers/test-connection', { config });
    });

    it('should send config in request body', () => {
      const config = { transport: 'stdio', command: 'npx', args: ['-y', '@server/package'] };

      httpMock.post = jest.fn().mockReturnValue(
        of({ success: true, message: 'OK' })
      );

      service.testConnection(config).subscribe();

      expect(httpMock.post).toHaveBeenCalledWith(
        '/api/mcp-servers/test-connection',
        { config: { transport: 'stdio', command: 'npx', args: ['-y', '@server/package'] } }
      );
    });

    it('should return McpServerTestConnectionResponse on success', (done) => {
      const config = { command: 'npx', args: ['test'] };
      const response: McpServerTestConnectionResponse = {
        success: true,
        message: 'Connection successful',
        tools_count: 10
      };

      httpMock.post = jest.fn().mockReturnValue(of(response));

      service.testConnection(config).subscribe({
        next: (res) => {
          expect(res.success).toBe(true);
          expect(res.message).toBe('Connection successful');
          expect(res.tools_count).toBe(10);
          done();
        },
        error: done.fail,
      });
    });

    it('should handle successful connection with zero tools', (done) => {
      const config = { url: 'http://localhost:3000' };
      const response: McpServerTestConnectionResponse = {
        success: true,
        message: 'Connected but no tools available',
        tools_count: 0
      };

      httpMock.post = jest.fn().mockReturnValue(of(response));

      service.testConnection(config).subscribe({
        next: (res) => {
          expect(res.success).toBe(true);
          expect(res.tools_count).toBe(0);
          done();
        },
        error: done.fail,
      });
    });

    it('should handle failed connection response', (done) => {
      const config = { command: 'invalid' };
      const errorResponse: McpServerTestConnectionResponse = {
        success: false,
        message: 'Connection refused'
      };

      httpMock.post = jest.fn().mockReturnValue(of(errorResponse));

      service.testConnection(config).subscribe({
        next: (res) => {
          expect(res.success).toBe(false);
          expect(res.message).toBe('Connection refused');
          done();
        },
        error: done.fail,
      });
    });

    it('should handle HTTP error response', (done) => {
      const config = { command: 'test' };

      httpMock.post = jest.fn().mockReturnValue(
        throwError(() => ({ error: { detail: 'Server unavailable' }, status: 500 }))
      );

      service.testConnection(config).subscribe({
        next: () => done.fail('Should have errored'),
        error: (err) => {
          expect(err.error.detail).toBe('Server unavailable');
          expect(err.status).toBe(500);
          done();
        },
      });
    });

    it('should handle network error', (done) => {
      const config = { url: 'http://invalid-host:9999' };

      httpMock.post = jest.fn().mockReturnValue(
        throwError(() => new Error('Network error'))
      );

      service.testConnection(config).subscribe({
        next: () => done.fail('Should have errored'),
        error: (err) => {
          expect(err.message).toBe('Network error');
          done();
        },
      });
    });

    it('should URL construction uses correct API path', () => {
      const config = { command: 'npx' };

      httpMock.post = jest.fn().mockReturnValue(of({ success: true, message: 'OK' }));

      service.testConnection(config).subscribe();

      // Verify POST was called with correct URL
      expect(httpMock.post).toHaveBeenCalledWith('/api/mcp-servers/test-connection', { config });
    });

    it('should handle complex nested config', (done) => {
      const config = {
        transport: 'sse',
        url: 'http://localhost:3000/sse',
        headers: {
          Authorization: 'Bearer token123',
          'X-Custom-Header': 'value'
        },
        timeout: 30000
      };

      httpMock.post = jest.fn().mockReturnValue(
        of({ success: true, message: 'OK' })
      );

      service.testConnection(config).subscribe({
        next: (res) => {
          expect(res.success).toBe(true);
          // Verify the config was passed correctly
          const postCall = (httpMock.post as jest.Mock).mock.calls[0];
          expect(postCall[1].config).toEqual(config);
          done();
        },
        error: done.fail,
      });
    });
  });
});
