import type { FileTreeNode } from '../../models/workspace.model';

type FlatNode = {
  expandable: boolean;
  name: string;
  path: string;
  type: string;
  level: number;
  loaded: boolean;
};

function getFileIcon(type: string, name: string): string {
  if (type !== 'file') return 'folder';
  const ext = name.split('.').pop()?.toLowerCase();
  const iconMap: Record<string, string> = {
    py: 'description',
    ts: 'code',
    js: 'code',
    html: 'html',
    css: 'style',
    json: 'data_object',
    md: 'article',
    sql: 'storage',
    sh: 'terminal',
    yaml: 'settings',
  };
  return iconMap[ext || ''] || 'insert_drive_file';
}

function selectFile(node: FlatNode, emitter: { emit(path: string): void }): void {
  emitter.emit(node.path);
}

type TreeControl = {
  dataNodes: FlatNode[];
  expand(node: FlatNode): void;
};

type UpdateResult = {
  tree: FileTreeNode[];
};

function updateNodeChildren(
  nestedTree: FileTreeNode[],
  path: string,
  children: FileTreeNode[],
  expandedPaths: Set<string>,
  treeControl: TreeControl
): UpdateResult {
  const patch = (nodes: FileTreeNode[]): FileTreeNode[] =>
    nodes.map((currentNode) => {
      if (currentNode.path === path) {
        return { ...currentNode, children };
      }
      if (currentNode.children) {
        return { ...currentNode, children: patch(currentNode.children) };
      }
      return currentNode;
    });

  const tree = patch(nestedTree);
  treeControl.dataNodes.forEach((currentNode) => {
    if (currentNode.expandable && expandedPaths.has(currentNode.path)) {
      treeControl.expand(currentNode);
    }
  });
  return { tree };
}

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

function makeFlatNode(overrides: Partial<FlatNode> = {}): FlatNode {
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

describe('FileTreeComponent logic', () => {
  describe('getFileIcon', () => {
    it.each([
      ['directory', 'src', 'folder'],
      ['file', 'main.py', 'description'],
      ['file', 'component.TS', 'code'],
      ['file', 'component.tsx', 'insert_drive_file'],
      ['file', 'LICENSE', 'insert_drive_file'],
      ['file', 'foo.test.ts', 'code'],
      ['file', 'package.json', 'data_object'],
      ['file', 'README.md', 'article'],
      ['file', 'config.yaml', 'settings'],
      ['file', 'run.sh', 'terminal'],
      ['file', 'schema.sql', 'storage'],
    ])('should map %s %s to %s', (type, name, expected) => {
      expect(getFileIcon(type, name)).toBe(expected);
    });
  });

  it('should emit the selected file path', () => {
    const emitter = { emit: jest.fn<void, [string]>() };
    const node = makeFlatNode({
      expandable: false,
      name: 'main.ts',
      path: 'src/main.ts',
      type: 'file',
    });

    selectFile(node, emitter);

    expect(emitter.emit).toHaveBeenCalledWith('src/main.ts');
  });

  describe('updateNodeChildren', () => {
    function makeTreeControl(dataNodes: FlatNode[] = []): TreeControl & {
      expand: jest.Mock<void, [FlatNode]>;
    } {
      return {
        dataNodes,
        expand: jest.fn<void, [FlatNode]>(),
      };
    }

    it('should patch top-level node children', () => {
      const tree = [makeNode()];
      const children = [makeNode({ name: 'main.ts', path: 'src/main.ts', type: 'file' })];
      const treeControl = makeTreeControl();

      const result = updateNodeChildren(tree, 'src', children, new Set(), treeControl);

      expect(result.tree[0].children).toEqual(children);
    });

    it('should patch deeply nested node children', () => {
      const tree = [
        makeNode({
          children: [
            makeNode({
              name: 'app',
              path: 'src/app',
              children: [makeNode({ name: 'components', path: 'src/app/components' })],
            }),
          ],
        }),
      ];
      const children = [
        makeNode({
          name: 'viewer.ts',
          path: 'src/app/components/viewer.ts',
          type: 'file',
        }),
      ];

      const result = updateNodeChildren(
        tree,
        'src/app/components',
        children,
        new Set(),
        makeTreeControl()
      );

      expect(result.tree[0].children?.[0].children?.[0].children).toEqual(children);
    });

    it('should preserve sibling nodes', () => {
      const sibling = makeNode({ name: 'README.md', path: 'README.md', type: 'file' });
      const tree = [makeNode(), sibling];

      const result = updateNodeChildren(
        tree,
        'src',
        [makeNode({ name: 'main.ts', path: 'src/main.ts', type: 'file' })],
        new Set(),
        makeTreeControl()
      );

      expect(result.tree[1]).toBe(sibling);
    });

    it('should leave the tree unchanged when the path is missing', () => {
      const tree = [makeNode({ children: [makeNode({ name: 'app', path: 'src/app' })] })];

      const result = updateNodeChildren(
        tree,
        'missing',
        [makeNode({ name: 'new.ts', path: 'new.ts', type: 'file' })],
        new Set(),
        makeTreeControl()
      );

      expect(result.tree).toEqual(tree);
    });

    it('should re-expand expanded paths after patching', () => {
      const src = makeFlatNode({ path: 'src' });
      const app = makeFlatNode({ name: 'app', path: 'src/app', level: 1 });
      const file = makeFlatNode({
        expandable: false,
        name: 'main.ts',
        path: 'src/main.ts',
        type: 'file',
        level: 1,
      });
      const treeControl = makeTreeControl([src, app, file]);

      updateNodeChildren(
        [makeNode()],
        'src',
        [],
        new Set(['src', 'src/app', 'src/main.ts']),
        treeControl
      );

      expect(treeControl.expand).toHaveBeenCalledTimes(2);
      expect(treeControl.expand).toHaveBeenNthCalledWith(1, src);
      expect(treeControl.expand).toHaveBeenNthCalledWith(2, app);
    });
  });
});
