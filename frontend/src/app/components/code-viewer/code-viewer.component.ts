import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceService } from '../../services/workspace.service';
import { CodemirrorDirective } from './codemirror.directive';

@Component({
  selector: 'app-code-viewer',
  standalone: true,
  imports: [CommonModule, CodemirrorDirective],
  template: `
    @if (file(); as f) {
      <div class="code-viewer">
        <div class="code-header">
          <span class="filepath">{{ f.path }}</span>
          <span class="meta">{{ f.total_lines }} lines · {{ formatSize(f.size_bytes) }}</span>
          @if (f.binary) {
            <span class="badge binary">Binary</span>
          }
          @if (f.truncated) {
            <span class="badge truncated">Truncated</span>
          }
        </div>
        @if (!f.binary) {
          <div class="code-content" [appCodemirror]=""
               [content]="f.content"
               [language]="f.language"></div>
        } @else {
          <div class="binary-placeholder">
            Binary file — preview not available ({{ formatSize(f.size_bytes) }})
          </div>
        }
      </div>
    }
  `,
  styleUrl: './code-viewer.component.scss',
})
export class CodeViewerComponent {
  private readonly workspace = inject(WorkspaceService);
  public readonly file = this.workspace.currentFile.asReadonly();

  formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1_048_576).toFixed(1)} MB`;
  }
}
