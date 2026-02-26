import { Component, signal, inject, OnInit } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { ApiService } from '../../services/api.service';
import { SourceCardComponent } from '../source-card/source-card.component';
import { AddSourceModalComponent } from '../add-source-modal/add-source-modal.component';
import type { Source, SourceCreate, SourceUpdate } from '../../models';

@Component({
  selector: 'app-source-list',
  standalone: true,
  imports: [
    CommonModule,
    MatDialogModule,
    MatSnackBarModule,
    MatProgressSpinnerModule,
    MatIconModule,
    MatButtonModule,
    SourceCardComponent
  ],
  templateUrl: './source-list.html',
  styleUrl: './source-list.scss'
})
export class SourceListComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);

  readonly sources = signal<Source[]>([]);
  readonly isLoading = signal(false);

  ngOnInit(): void {
    this.loadSources();
  }

  protected loadSources(): void {
    this.isLoading.set(true);
    
    this.api.listSources().pipe(takeUntilDestroyed()).subscribe({
      next: (response) => {
        this.sources.set(response.sources);
        this.isLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to load sources:', err);
        this.showError('Failed to load sources');
        this.isLoading.set(false);
      }
    });
  }

  protected onAddSource(): void {
    const dialogRef = this.dialog.open(AddSourceModalComponent, {
      panelClass: 'dark-modal-panel',
      disableClose: true
    });

    dialogRef.afterClosed().pipe(takeUntilDestroyed()).subscribe((result?: SourceCreate) => {
      if (result) {
        this.createSource(result);
      }
    });
  }

  private createSource(sourceCreate: SourceCreate): void {
    this.isLoading.set(true);
    
    this.api.createSource(sourceCreate).pipe(takeUntilDestroyed()).subscribe({
      next: (newSource) => {
        this.sources.update(prev => [...prev, newSource]);
        this.showSuccess(`Source "${newSource.name}" created successfully`);
        this.isLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to create source:', err);
        this.showError('Failed to create source');
        this.isLoading.set(false);
      }
    });
  }

  protected onStartSource(sourceId: string): void {
    this.isLoading.set(true);
    
    this.api.startSource(sourceId).pipe(takeUntilDestroyed()).subscribe({
      next: (response) => {
        this.sources.update(prev => 
          prev.map(s => s.source_id === sourceId ? { ...s, status: response.status } : s)
        );
        this.showSuccess(response.message);
        this.isLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to start source:', err);
        this.showError('Failed to start source');
        this.isLoading.set(false);
      }
    });
  }

  protected onStopSource(sourceId: string): void {
    this.isLoading.set(true);
    
    this.api.stopSource(sourceId).pipe(takeUntilDestroyed()).subscribe({
      next: (response) => {
        this.sources.update(prev => 
          prev.map(s => s.source_id === sourceId ? { ...s, status: response.status } : s)
        );
        this.showSuccess(response.message);
        this.isLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to stop source:', err);
        this.showError('Failed to stop source');
        this.isLoading.set(false);
      }
    });
  }

  protected onDeleteSource(sourceId: string): void {
    const source = this.sources().find(s => s.source_id === sourceId);
    if (!source) return;

    if (!confirm(`Are you sure you want to delete the source "${source.name}"?`)) {
      return;
    }

    this.isLoading.set(true);
    
    this.api.deleteSource(sourceId).pipe(takeUntilDestroyed()).subscribe({
      next: () => {
        this.sources.update(prev => prev.filter(s => s.source_id !== sourceId));
        this.showSuccess('Source deleted successfully');
        this.isLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to delete source:', err);
        this.showError('Failed to delete source');
        this.isLoading.set(false);
      }
    });
  }

  protected onUpdateSource(sourceId: string, update: SourceUpdate): void {
    this.isLoading.set(true);
    
    this.api.updateSource(sourceId, update).pipe(takeUntilDestroyed()).subscribe({
      next: (updatedSource) => {
        this.sources.update(prev => 
          prev.map(s => s.source_id === sourceId ? updatedSource : s)
        );
        this.showSuccess('Source updated successfully');
        this.isLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to update source:', err);
        this.showError('Failed to update source');
        this.isLoading.set(false);
      }
    });
  }

  protected onToggleEnabled(source: Source): void {
    this.onUpdateSource(source.source_id, { enabled: !source.enabled });
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

  protected goHome(): void {
    this.router.navigate(['/']);
  }
}
