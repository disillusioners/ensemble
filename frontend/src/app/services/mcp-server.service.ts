import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap, map, finalize } from 'rxjs';
import type { McpServer, McpServerCreate, McpServerUpdate, McpServerListResponse, McpServerDeleteResponse } from '../models';

@Injectable({
  providedIn: 'root'
})
export class McpServerService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/mcp-servers';

  // Signals for state
  readonly servers = signal<McpServer[]>([]);
  readonly loading = signal(false);

  /**
   * GET /api/mcp-servers
   */
  listServers(): Observable<McpServer[]> {
    this.loading.set(true);
    return this.http.get<McpServerListResponse>(this.API_BASE).pipe(
      tap(response => this.servers.set(response.mcp_servers)),
      map(response => response.mcp_servers),
      finalize(() => this.loading.set(false))
    );
  }

  /**
   * GET /api/mcp-servers/{id}
   */
  getServer(id: string): Observable<McpServer> {
    return this.http.get<McpServer>(`${this.API_BASE}/${encodeURIComponent(id)}`);
  }

  /**
   * POST /api/mcp-servers
   */
  createServer(data: McpServerCreate): Observable<McpServer> {
    return this.http.post<McpServer>(this.API_BASE, data).pipe(
      tap(server => {
        this.servers.update(servers => [server, ...servers]);
      })
    );
  }

  /**
   * PUT /api/mcp-servers/{id}
   */
  updateServer(id: string, data: McpServerUpdate): Observable<McpServer> {
    return this.http.put<McpServer>(`${this.API_BASE}/${encodeURIComponent(id)}`, data).pipe(
      tap(updatedServer => {
        this.servers.update(servers =>
          servers.map(s => s.id === id ? updatedServer : s)
        );
      })
    );
  }

  /**
   * DELETE /api/mcp-servers/{id}
   */
  deleteServer(id: string): Observable<McpServerDeleteResponse> {
    return this.http.delete<McpServerDeleteResponse>(`${this.API_BASE}/${encodeURIComponent(id)}`).pipe(
      tap(() => {
        this.servers.update(servers => servers.filter(s => s.id !== id));
      })
    );
  }
}
