import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { ProjectService } from '../../services/project.service';
import { Project } from '../../models/project.model';

export interface ProjectDeleteDialogData {
  project: Project;
}

@Component({
  selector: 'app-project-delete-dialog',
  standalone: true,
  imports: [
    CommonModule,
    MatDialogModule,
    MatButtonModule,
    MatProgressSpinnerModule,
    MatSnackBarModule
  ],
  templateUrl: './project-delete-dialog.html',
  styleUrl: './project-delete-dialog.scss'
})
export class ProjectDeleteDialogComponent {
  private readonly projectService = inject(ProjectService);
  private readonly snackBar = inject(MatSnackBar);

  protected readonly dialogRef = inject(MatDialogRef<ProjectDeleteDialogComponent>);
  protected readonly data = inject<ProjectDeleteDialogData>(MAT_DIALOG_DATA);

  protected readonly isDeleting = signal(false);

  protected get project(): Project {
    return this.data.project;
  }

  protected handleCancel(): void {
    this.dialogRef.close(false);
  }

  protected handleDelete(): void {
    this.isDeleting.set(true);

    this.projectService.deleteProject(this.project.project_id).subscribe({
      next: () => {
        this.snackBar.open('Project deleted successfully', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar'
        });
        this.dialogRef.close(true);
      },
      error: (err) => {
        this.isDeleting.set(false);
        const errorMessage = err?.error?.detail || err.message || 'Failed to delete project';
        this.snackBar.open(errorMessage, 'Close', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
        // Close dialog on error so user can see the snackbar
        this.dialogRef.close(false);
      }
    });
  }
}
