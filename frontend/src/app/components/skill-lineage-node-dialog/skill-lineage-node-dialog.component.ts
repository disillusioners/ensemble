import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  MAT_DIALOG_DATA,
  MatDialogModule,
  MatDialogRef,
} from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { SkillLineageNode } from '../../models/skill.model';

/**
 * Dialog payload — the lineage node whose edge metadata we want to
 * inspect plus the id of the currently-viewed skill (used to
 * disambiguate the "this is the current node" empty-state case
 * versus an edge without metadata).
 */
export interface SkillLineageNodeDialogData {
  node: SkillLineageNode;
  currentSkillId: string;
}

/**
 * Edge metadata popup for the skill lineage tree.
 *
 * Shown when the user clicks a node in the lineage graph. Displays
 * the node's ``change_summary`` (one-line description of what
 * changed), the ``content_diff`` body (a unified-diff text), and the
 * ``edge_created_at`` timestamp. For the current skill itself
 * (which has no edge metadata) the dialog renders an explanatory
 * empty state instead of a confusing blank.
 *
 * Marked standalone with ``MatDialogModule`` and ``MatButtonModule``
 * imports only — no other feature surface touches it.
 */
@Component({
  selector: 'app-skill-lineage-node-dialog',
  standalone: true,
  imports: [CommonModule, MatDialogModule, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './skill-lineage-node-dialog.component.html',
  styleUrl: './skill-lineage-node-dialog.component.scss',
})
export class SkillLineageNodeDialogComponent {
  protected readonly dialogRef = inject(MatDialogRef<SkillLineageNodeDialogComponent>);
  protected readonly data = inject<SkillLineageNodeDialogData>(MAT_DIALOG_DATA);

  /**
   * True when the dialog is showing the "current skill" empty state
   * — i.e. the node the user clicked is the one they're already
   * viewing, so there is no edge metadata to display.
   */
  protected get isCurrentNode(): boolean {
    return !!this.data?.node && this.data.node.id === this.data?.currentSkillId;
  }

  /**
   * Display value for the edge timestamp. Falls back to a friendly
   * placeholder when the field is absent (orphan edges).
   */
  protected get createdAtDisplay(): string {
    const ts = this.data?.node?.edge_created_at;
    if (!ts) {
      return 'Unknown';
    }
    return ts;
  }

  /**
   * Display value for the change summary. The ``buildLineageGraph``
   * edge label uses ``Auto-evolved`` as a fallback when the
   * backend returned an empty string — we mirror that here so the
   * dialog and the graph stay consistent.
   */
  protected get changeSummaryDisplay(): string {
    const summary = this.data?.node?.change_summary;
    if (!summary || !summary.trim()) {
      return 'Auto-evolved';
    }
    return summary;
  }

  protected get contentDiffDisplay(): string {
    return this.data?.node?.content_diff ?? '';
  }

  protected get nodeName(): string {
    return this.data?.node?.name || this.data?.node?.id || 'Unknown';
  }

  protected get nodeGeneration(): number {
    return Number.isFinite(this.data?.node?.generation)
      ? (this.data?.node?.generation as number)
      : 0;
  }

  protected get nodeStatus(): string {
    return this.data?.node?.status ?? '';
  }

  protected onClose(): void {
    this.dialogRef.close();
  }
}