import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { FileTreeComponent } from './file-tree.component';
import { WorkspaceService } from '../../services/workspace.service';
import type { FileTreeNode } from '../../models/workspace.model';

/**
 * Tests for `FileTreeComponent`.
 *
 * Pattern: Angular `TestBed` with the real `WorkspaceService` (HTTP
 * tests via `provideHttpClientTesting` keep the unused HTTP plumbing
 * quiet). Tests focus on:
 *   - `getFileIcon()` (pure method on the REAL component)
 *   - `selectFile()` emits on the real `fileSelected` EventEmitter
 *   - `setTree()` populates the real `dataSource`
 *   - `hasChild()` correctly distinguishes file vs directory nodes
 */
describe('FileTreeComponent', () => {
  let fixture: ComponentFixture<FileTreeComponent>;
  let component: FileTreeComponent;

  function makeNode(overrides: Partial<FileTreeNode> = {}): FileTreeNode {
    return {
      name: 'src',
      path: 'src',
      type: 'directory',
      size: null,
      children: null,
      ...overrides,
    };
  }

  // Mirror the FlatNode shape produced by the component's
  // MatTreeFlattener so we can drive selectFile / hasChild directly.
  function makeFlatNode(overrides: Partial<{
    expandable: boolean;
    name: string;
    path: string;
    type: string;
    level: number;
    loaded: boolean;
  }> = {}) {
    return {
      expandable: true,
      name: 'src',
      path: 'src',
      type: 'directory',
      level: 0,
      loaded: true,
      ...overrides,
    };
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [FileTreeComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideNoopAnimations(),
        WorkspaceService,
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(FileTreeComponent);
    component = fixture.componentInstance;
  });

  // ── 1) Component creation ─────────────────────────────────────

  it('creates successfully', () => {
    expect(component).toBeTruthy();
  });

  it('exposes an Output<string> named fileSelected', () => {
    expect(component.fileSelected).toBeTruthy();
  });

  // ── 2) getFileIcon (pure method on real component) ────────────

  describe('getFileIcon', () => {
    it.each([
      ['directory', 'src', 'folder'],
      ['file', 'main.py', 'description'],
      ['file', 'component.TS', 'code'],          // case-insensitive extension
      ['file', 'component.tsx', 'insert_drive_file'],
      ['file', 'LICENSE', 'insert_drive_file'],
      ['file', 'foo.test.ts', 'code'],
      ['file', 'package.json', 'data_object'],
      ['file', 'README.md', 'article'],
      ['file', 'config.yaml', 'settings'],
      ['file', 'run.sh', 'terminal'],
      ['file', 'schema.sql', 'storage'],
      ['file', 'main.js', 'code'],
      ['file', 'index.html', 'html'],
      ['file', 'styles.css', 'style'],
    ])('should map %s %s to %s', (type, name, expected) => {
      expect(component.getFileIcon(type, name)).toBe(expected);
    });
  });

  // ── 3) selectFile (real EventEmitter on real component) ───────

  describe('selectFile', () => {
    it('should emit the file path on fileSelected', () => {
      let emitted: string | null = null;
      component.fileSelected.subscribe((path) => (emitted = path));

      component.selectFile(makeFlatNode({
        expandable: false,
        name: 'main.ts',
        path: 'src/main.ts',
        type: 'file',
      }));

      expect(emitted).toBe('src/main.ts');
    });

    it('should emit the path for nested files too', () => {
      let emitted: string | null = null;
      component.fileSelected.subscribe((path) => (emitted = path));

      component.selectFile(makeFlatNode({
        expandable: false,
        name: 'viewer.ts',
        path: 'src/app/components/viewer.ts',
        type: 'file',
        level: 2,
      }));

      expect(emitted).toBe('src/app/components/viewer.ts');
    });
  });

  // ── 4) setTree populates the real dataSource ──────────────────

  describe('setTree', () => {
    it('should populate dataSource.data from the nested tree', () => {
      component.setTree([
        makeNode({ name: 'README.md', path: 'README.md', type: 'file' }),
      ]);

      expect(component.dataSource.data.length).toBe(1);
      expect(component.dataSource.data[0].name).toBe('README.md');
    });

    it('should preserve the nested shape (children=null means not expanded)', () => {
      component.setTree([
        makeNode({ name: 'src', path: 'src', type: 'directory', children: null }),
        makeNode({ name: 'README.md', path: 'README.md', type: 'file' }),
      ]);

      const data = component.dataSource.data;
      expect(data.length).toBe(2);
      expect(data[0].name).toBe('src');
      expect(data[0].children).toBeNull();
      expect(data[1].name).toBe('README.md');
      expect(data[1].type).toBe('file');
    });
  });

  // ── 5) hasChild (tree predicate) ──────────────────────────────

  describe('hasChild', () => {
    it('should return true for directories (expandable=true)', () => {
      const node = makeFlatNode({ expandable: true });
      expect(component.hasChild(0, node as never)).toBe(true);
    });

    it('should return false for files (expandable=false)', () => {
      const node = makeFlatNode({ expandable: false });
      expect(component.hasChild(0, node as never)).toBe(false);
    });
  });

  // ── 6) projectId input ────────────────────────────────────────

  describe('projectId input', () => {
    it('should accept a projectId input via setInput', () => {
      fixture.componentRef.setInput('projectId', 'project-42');
      expect(component.projectId).toBe('project-42');
    });
  });

  // ── 7) setTree clears live expansion; restoreExpandedPaths repopulates ──
  // Contract: switching projects calls setTree() with the incoming
  // tree (which clears the live expansion set to []), then calls
  // restoreExpandedPaths() with the cached expansion paths. Verify
  // both halves of that round-trip on the real component.
  describe('setTree + restoreExpandedPaths round trip', () => {
    it('clears the live expansion on setTree() and repopulates from restoreExpandedPaths()', () => {
      // Seed the expansion set via restoreExpandedPaths — mirrors what
      // the user accumulates by toggling directories open.
      component.restoreExpandedPaths(['src', 'src/components']);
      expect(component.getExpandedPaths()).toEqual([
        'src',
        'src/components',
      ]);

      // Incoming project data arrives via setTree(). Per the cache-hit
      // contract, setTree clears the live expansion set before
      // assigning the new tree data.
      component.setTree([makeNode()]);
      expect(component.getExpandedPaths()).toEqual([]);

      // After restore, the workspace calls restoreExpandedPaths with
      // the cached expansion paths for the incoming project. The set
      // must be repopulated so the previously-expanded directories
      // re-open in the new tree.
      component.restoreExpandedPaths(['src']);
      expect(component.getExpandedPaths()).toEqual(['src']);
    });
  });
});