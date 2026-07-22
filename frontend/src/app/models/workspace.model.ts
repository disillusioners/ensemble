export interface FileTreeNode {
  name: string;
  path: string;        // relative to workdir
  type: 'file' | 'directory' | 'symlink';
  size: number | null;  // bytes, files only
  children: FileTreeNode[] | null;  // null = not expanded
}

export interface FileTreeResponse {
  project_id: string;
  path: string;
  tree: FileTreeNode[];
  truncated: boolean;
}

export interface FileContentResponse {
  project_id: string;
  path: string;
  content: string;
  language: string | null;
  total_lines: number;
  offset: number;
  limit: number;
  truncated: boolean;
  binary: boolean;
  size_bytes: number;
}

export interface GitDiffResponse {
  project_id: string;
  path: string;
  has_changes: boolean;
  diff: string | null;
  head_content: string | null;
  working_content: string | null;
  error: string | null;
}

export interface FileChangeEvent {
  path: string;
  type: 'modified' | 'created' | 'deleted' | 'moved' | string;
  timestamp?: number;
}
