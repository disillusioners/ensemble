import { Component, inject, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { MatTooltipModule } from '@angular/material/tooltip';
import { TabStateService } from '../../services/tab-state.service';
import { ProjectService } from '../../services/project.service';

@Component({
  selector: 'app-project-tab-bar',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatMenuModule,
    MatTooltipModule,
  ],
  templateUrl: './project-tab-bar.component.html',
  styleUrl: './project-tab-bar.component.scss',
})
export class ProjectTabBarComponent {
  protected readonly tabStateService = inject(TabStateService);
  protected readonly projectService = inject(ProjectService);

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
}
