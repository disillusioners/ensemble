import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Observable, tap, map, finalize, catchError, throwError } from 'rxjs';
import type { McpServer, McpServerCreate, McpServerUpdate, McpServerListResponse, McpServerDeleteResponse, McpServerTestConnectionResponse, BuiltinServerTemplate, BuiltinTemplateListResponse, BuiltinServerConfigure } from '../models';

@Injectable({
  providedIn: 'root'
})
export class McpServerService {
  private readonly http = inject(HttpClient);
  private readonly snackBar = inject(MatSnackBar);
  private readonly API_BASE = '/api/mcp-servers';

  // Signals for state
  readonly servers = signal<McpServer[]>([]);
  readonly loading = signal(false);
  readonly templates = signal<BuiltinServerTemplate[]>([]);
  readonly templatesLoading = signal(false);

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

  /**
   * GET /api/mcp-servers/builtin-templates
   */
  listTemplates(): Observable<BuiltinServerTemplate[]> {
    this.templatesLoading.set(true);
    return this.http.get<BuiltinTemplateListResponse>(`${this.API_BASE}/builtin-templates`).pipe(
      tap(response => this.templates.set(response.templates)),
      map(response => response.templates),
      finalize(() => this.templatesLoading.set(false)),
      catchError(err => {
        console.error('Failed to load builtin templates:', err);
        this.snackBar.open('Failed to load builtin templates', 'Close', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
        return throwError(() => err);
      })
    );
  }

  /**
   * POST /api/mcp-servers/configure-builtin
   */
  configureBuiltin(request: BuiltinServerConfigure): Observable<McpServer> {
    return this.http.post<McpServer>(`${this.API_BASE}/configure-builtin`, request).pipe(
      tap(server => {
        this.servers.update(servers => [server, ...servers]);
      }),
      catchError(err => {
        console.error('Failed to configure builtin server:', err);
        this.snackBar.open('Failed to configure builtin server', 'Close', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
        return throwError(() => err);
      })
    );
  }

  /**
   * POST /api/mcp-servers/{serverId}/reset-builtin
   */
  resetBuiltin(serverId: string): Observable<McpServer> {
    return this.http.post<McpServer>(`${this.API_BASE}/${encodeURIComponent(serverId)}/reset-builtin`, {}).pipe(
      tap(updatedServer => {
        this.servers.update(servers =>
          servers.map(s => s.id === serverId ? updatedServer : s)
        );
      }),
      catchError(err => {
        console.error('Failed to reset builtin server:', err);
        this.snackBar.open('Failed to reset builtin server', 'Close', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
        return throwError(() => err);
      })
    );
  }

  /**
   * POST /api/mcp-servers/test-connection
   */
  testConnection(config: Record<string, unknown>): Observable<McpServerTestConnectionResponse> {
    return this.http.post<McpServerTestConnectionResponse>(`${this.API_BASE}/test-connection`, { config });
  }
}
