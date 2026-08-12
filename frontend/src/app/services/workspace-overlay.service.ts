import { Injectable, signal, WritableSignal } from '@angular/core';

/**
 * Singleton state for the global workspace overlay.
 *
 * The workspace overlay (VS Code editor + file tree) is mounted once at the
 * App root so it survives route changes and can be toggled from any page
 * via the Alt+` global hotkey. Centralizing the visibility/projectId
 * signals in a root-provided service lets the host component (App) own
 * the overlay element while the chat page, the project tab bar, and the
 * global hotkey all read/write through the same API.
 *
 * Visibility contract (mirrors the old ChatComponent-local signals):
 *   - `showWorkspace` controls whether the overlay is visible.
 *   - `workspaceProjectId` tracks which project the overlay is bound to.
 *   - The overlay element is ALWAYS mounted; the `[visible]` input and
 *     `[style.display]` binding control whether its SSE / keyboard
 *     listeners activate and whether it occupies layout. This keeps the
 *     editor cache alive across hide/show cycles.
 */
@Injectable({
  providedIn: 'root'
})
export class WorkspaceOverlayService {
  readonly showWorkspace: WritableSignal<boolean> = signal(false);
  readonly workspaceProjectId: WritableSignal<string | null> = signal(null);

  /**
   * Toggle the workspace overlay.
   *
   * - When called with the same project that's currently shown → hide.
   * - When called with a different project → switch to that project (show).
   * - When called with no argument and a project is already shown → toggle.
   * - When called with no argument and no project is shown → no-op (we
   *   don't have a project to show yet).
   */
  toggle(projectId?: string): void {
    const currentId = this.workspaceProjectId();
    const targetId = projectId ?? currentId;
    if (!targetId) return;

    if (this.showWorkspace() && currentId === targetId) {
      this.showWorkspace.set(false);
      return;
    }
    this.workspaceProjectId.set(targetId);
    this.showWorkspace.set(true);
  }

  /** Hide the workspace overlay (e.g. from the overlay's Hide button). */
  hide(): void {
    this.showWorkspace.set(false);
  }

  /** Set the project and show the overlay. */
  show(projectId: string): void {
    this.workspaceProjectId.set(projectId);
    this.showWorkspace.set(true);
  }
}
