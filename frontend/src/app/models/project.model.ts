// Project status type
export type ProjectStatus = 'active' | 'paused' | 'completed' | 'archived';

// Project type
export type ProjectType = 'software' | 'documentation' | 'research' | 'task' | 'general' | 'infrastructure' | 'gitops' | 'devops' | 'library' | 'data' | 'mobile' | string;

// Main Project interface matching backend
export interface Project {
  project_id: string;
  name: string;
  project_type: ProjectType;
  status: ProjectStatus;
  main_directory: string | null;
  related_directories: string[];
  description: string | null;
  tags: string[];
  shortnames: string[];
  metadata: Record<string, any>;
  relationships: {
    session?: string[];
    [key: string]: string[] | undefined;
  };
  creator_instance_id: string | null;
  creator_agent_id: string | null;
  created_at: string;
  updated_at: string | null;
  job_queue_paused: boolean;
}

// Response for list endpoint
export interface ProjectListResponse {
  projects: Project[];
  total: number;
}

// For creating/updating projects
export interface ProjectCreate {
  name: string;
  project_type?: ProjectType;
  main_directory?: string;
  related_directories?: string[];
  description?: string;
  tags?: string[];
  metadata?: Record<string, any>;
}

export interface ProjectUpdate {
  name?: string;
  project_type?: ProjectType;
  status?: ProjectStatus;
  main_directory?: string;
  description?: string;
  tags?: string[];
}

/**
 * Helper function to check if a project's job queue is paused
 * @param project - The project to check
 * @returns boolean indicating if the job queue is paused
 */
export function isJobQueuePaused(project: Project): boolean {
  return project.job_queue_paused === true;
}
