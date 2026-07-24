import { Component, computed, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * Subtle version picker for agents with multiple versions.
 *
 * Renders a native `<select>` of available tags. The picker is hidden when
 * the agent has only a single version (`available_versions.length <= 1`),
 * so callers don't have to gate visibility themselves. Selection is wired
 * through Angular 21 signals — no NgModel two-way binding.
 *
 * Tag semantics:
 *   - `null`        → base / unversioned agent (displayed as "Base")
 *   - "v2", "exp"   → arbitrary version tags supplied by the backend
 *
 * The list is sorted with `null` (base) first, then alphabetically. This
 * keeps "Base" at the top so it stays the easy default pick.
 */
@Component({
  selector: 'app-version-picker',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './version-picker.html',
  styleUrl: './version-picker.scss',
})
export class VersionPickerComponent {
  /** All version tags available for the current agent, including `null`
   *  for the base version when one exists. */
  readonly availableVersions = input<(string | null)[]>([]);

  /** Currently selected tag (null = base). */
  readonly selectedTag = input<string | null>(null);

  /** Emits the newly selected tag on change. */
  readonly tagChange = output<string | null>();

  /** Hide the picker entirely when there's only one version to choose from. */
  readonly hasMultipleVersions = computed(
    () => this.availableVersions().length > 1,
  );

  /** Sort null (base) first, then alphabetically. Returns a new array so the
   *  computed is safe to memoize against `availableVersions` mutations. */
  readonly sortedVersions = computed(() => {
    const versions = [...this.availableVersions()];
    return versions.sort((a, b) => {
      if (a === null) return -1;
      if (b === null) return 1;
      return a.localeCompare(b);
    });
  });

  onSelect(tag: string | null): void {
    this.tagChange.emit(tag);
  }

  /** Display label for a tag: `null` → "Base", anything else → the tag. */
  getDisplayLabel(tag: string | null): string {
    return tag === null ? 'Base' : tag;
  }

  /** Match the option's value against the currently-selected tag. Coerces
   *  null to null so the native select correctly marks the base option. */
  isSelected(tag: string | null): boolean {
    return this.selectedTag() === tag;
  }

  /** Track-by for @for — tags are strings (or the literal null). */
  trackByTag(_index: number, tag: string | null): string {
    return tag ?? '__base__';
  }
}