import { Component, inject, OnInit, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { McpServerService } from '../../services/mcp-server.service';
import { McpServerDialogComponent } from '../mcp-server-dialog/mcp-server-dialog.component';
import type { McpServer, McpServerCreate, McpServerUpdate } from '../../models';

@Component({
  selector: 'app-mcp-server-list',
  standalone: true,
  imports: [
    CommonModule,
    MatDialogModule,
    MatSnackBarModule,
    MatProgressSpinnerModule,
    MatIconModule,
    MatButtonModule
  ],
  templateUrl: './mcp-server-list.html',
  styleUrl: './mcp-server-list.scss'
})
export class McpServerListComponent implements OnInit {
  private readonly mcpServerService = inject(McpServerService);
  private readonly router = inject(Router);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);
  private readonly destroyRef = inject(DestroyRef);

  readonly servers = this.mcpServerService.servers;
  readonly loading = this.mcpServerService.loading;

  ngOnInit(): void {
    this.loadServers();
  }

  protected loadServers(): void {
    this.mcpServerService.listServers().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {},
      error: (err) => {
        console.error('Failed to load MCP servers:', err);
        this.showError('Failed to load MCP servers');
      }
    });
  }

  protected onAddServer(): void {
    const dialogRef = this.dialog.open(McpServerDialogComponent, {
      panelClass: 'dark-modal-panel',
      disableClose: true
    });

    dialogRef.afterClosed().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((result?: McpServerCreate) => {
      if (result) {
        this.createServer(result);
      }
    });
  }

  protected onEditServer(server: McpServer): void {
    const dialogRef = this.dialog.open(McpServerDialogComponent, {
      panelClass: 'dark-modal-panel',
      disableClose: true,
      data: { server }
    });

    dialogRef.afterClosed().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((result?: McpServerUpdate) => {
      if (result) {
        this.updateServer(server.id, result);
      }
    });
  }

  protected onDeleteServer(id: string): void {
    const server = this.servers().find(s => s.id === id);
    if (!server) return;

    if (!confirm(`Are you sure you want to delete the MCP server "${server.name}"?`)) {
      return;
    }

    this.mcpServerService.deleteServer(id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.showSuccess('MCP server deleted successfully');
      },
      error: (err) => {
        console.error('Failed to delete MCP server:', err);
        this.showError('Failed to delete MCP server');
      }
    });
  }

  private createServer(data: McpServerCreate): void {
    this.mcpServerService.createServer(data).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (newServer) => {
        this.showSuccess(`MCP server "${newServer.name}" created successfully`);
      },
      error: (err) => {
        console.error('Failed to create MCP server:', err);
        this.showError('Failed to create MCP server');
      }
    });
  }

  private updateServer(id: string, data: McpServerUpdate): void {
    this.mcpServerService.updateServer(id, data).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.showSuccess('MCP server updated successfully');
      },
      error: (err) => {
        console.error('Failed to update MCP server:', err);
        this.showError('Failed to update MCP server');
      }
    });
  }

  protected formatDate(dateString: string): string {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    });
  }

  protected truncateText(text: string | null, maxLength: number = 100): string {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
  }

  protected goHome(): void {
    this.router.navigate(['/']);
  }

  private showSuccess(message: string): void {
    this.snackBar.open(message, 'Close', {
      duration: 3000,
      panelClass: 'success-snackbar'
    });
  }

  private showError(message: string): void {
    this.snackBar.open(message, 'Close', {
      duration: 5000,
      panelClass: 'error-snackbar'
    });
  }
}
