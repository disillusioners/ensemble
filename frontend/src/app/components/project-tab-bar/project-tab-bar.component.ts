import { Component, inject, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { TabStateService } from '../../services/tab-state.service';
import { ProjectService } from '../../services/project.service';
import { ProjectDeleteDialogComponent } from '../project-delete-dialog/project-delete-dialog.component';
import { Project } from '../../models/project.model';

@Component({
  selector: 'app-project-tab-bar',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatMenuModule,
    MatTooltipModule,
    MatDialogModule,
  ],
  templateUrl: './project-tab-bar.component.html',
  styleUrl: './project-tab-bar.component.scss',
})
export class ProjectTabBarComponent {
  protected readonly tabStateService = inject(TabStateService);
  protected readonly projectService = inject(ProjectService);
  private readonly dialog = inject(MatDialog);
  private readonly router = inject(Router);

  /**
   * Computed list of projects that are not currently open as tabs.
   * Filters out projects that already have a tab open (except 'All').
   */
  protected readonly unopenedProjects = computed(() => {
    const openTabIds = new Set(
      this.tabStateService.openTabs().map((tab) => tab.id)
    );
    return this.projectService.projects().filter(
      (project) => !openTabIds.has(project.project_id)
    );
  });

  /**
   * Handle closing a project tab.
   * Uses stopPropagation to prevent the tab from becoming active.
   */
  protected onCloseTab(event: Event, tabId: string): void {
    event.stopPropagation();
    this.tabStateService.removeTab(tabId);
  }

  /**
   * Handle delete project with confirmation dialog.
   * Closes the tab and navigates away from project if successful.
   */
  protected onDeleteProject(event: Event, project: Project): void {
    event.stopPropagation();

    const dialogRef = this.dialog.open(ProjectDeleteDialogComponent, {
      data: { project },
      panelClass: 'dark-modal-panel',
      width: '400px'
    });

    dialogRef.afterClosed().subscribe((deleted: boolean | undefined) => {
      if (deleted) {
        // Remove the tab if deletion was successful
        this.tabStateService.removeTab(project.project_id);
        // Navigate to home to leave project context
        this.router.navigate(['/']);
      }
    });
  }

  /**
   * Get project by ID from the projects list.
   */
  protected getProjectById(projectId: string): Project | undefined {
    return this.projectService.projects().find(p => p.project_id === projectId);
  }
}
