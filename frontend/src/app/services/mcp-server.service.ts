import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap, catchError, map, finalize, throwError } from 'rxjs';
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
  readonly error = signal<string | null>(null);

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
    return this.http.get<McpServer>(`${this.API_BASE}/${encodeURIComponent(id)}`).pipe(
      catchError(err => {
        this.error.set(err.message || 'Failed to fetch MCP server');
        throw err;
      })
    );
  }

  /**
   * POST /api/mcp-servers
   */
  createServer(data: McpServerCreate): Observable<McpServer> {
    return this.http.post<McpServer>(this.API_BASE, data).pipe(
      tap(server => {
        this.servers.update(servers => [server, ...servers]);
      }),
      catchError(err => {
        this.error.set(err.message || 'Failed to create MCP server');
        throw err;
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
      }),
      catchError(err => {
        this.error.set(err.message || 'Failed to update MCP server');
        throw err;
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
      }),
      catchError(err => {
        this.error.set(err.message || 'Failed to delete MCP server');
        throw err;
      })
    );
  }

  /**
   * Helper to clear error
   */
  clearError(): void {
    this.error.set(null);
  }
}
