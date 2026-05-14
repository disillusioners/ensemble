import { request, APIRequestContext } from '@playwright/test';

const BASE_URL = 'http://localhost:8088';

/**
 * Create an API request context for making HTTP requests to the backend.
 */
async function createRequestContext(): Promise<APIRequestContext> {
  return request.newContext({ baseURL: BASE_URL });
}

/**
 * Create a test project via the API.
 * @param name - The name of the project to create
 * @returns The created project data including project_id
 */
export async function createTestProject(name: string): Promise<{
  project_id: string;
  name: string;
  created_at?: string;
}> {
  const context = await createRequestContext();
  const response = await context.post('/api/projects', {
    data: { name },
    headers: { 'Content-Type': 'application/json' },
  });

  if (!response.ok()) {
    const error = await response.text();
    throw new Error(`Failed to create project: ${response.status()} ${error}`);
  }

  return response.json();
}

/**
 * Create a test instance via the API.
 * @param agentId - The agent ID to use for the instance
 * @param projectId - Optional project ID to associate the instance with
 * @returns The created instance data
 */
export async function createTestInstance(
  agentId: string = 'leader',
  projectId?: string
): Promise<{
  instance_id: string;
  agent_id: string;
  project_id?: string;
  status: string;
  created_at: string;
}> {
  const context = await createRequestContext();
  const body: { agent_id: string; project_id?: string } = { agent_id: agentId };
  if (projectId) {
    body.project_id = projectId;
  }

  const response = await context.post('/api/instances', {
    data: body,
    headers: { 'Content-Type': 'application/json' },
  });

  if (!response.ok()) {
    const error = await response.text();
    throw new Error(`Failed to create instance: ${response.status()} ${error}`);
  }

  return response.json();
}

/**
 * Delete a test instance via the API.
 * @param instanceId - The instance ID to delete
 */
export async function deleteTestInstance(instanceId: string): Promise<void> {
  const context = await createRequestContext();
  const response = await context.delete(`/api/instances/${instanceId}`);

  if (!response.ok()) {
    const error = await response.text();
    throw new Error(`Failed to delete instance ${instanceId}: ${response.status()} ${error}`);
  }
}

/**
 * List all projects.
 * @returns Array of project objects
 */
export async function listProjects(): Promise<
  Array<{
    project_id: string;
    name: string;
    created_at?: string;
  }>
> {
  const context = await createRequestContext();
  const response = await context.get('/api/projects');

  if (!response.ok()) {
    const error = await response.text();
    throw new Error(`Failed to list projects: ${response.status()} ${error}`);
  }

  return response.json();
}

/**
 * List instances, optionally filtered by project.
 * @param projectId - Optional project ID to filter by
 * @returns Object with instances array and total count
 */
export async function listInstances(
  projectId?: string
): Promise<{
  instances: Array<{
    instance_id: string;
    agent_id: string;
    project_id?: string;
    status: string;
    created_at: string;
  }>;
  total: number;
}> {
  const context = await createRequestContext();
  const url = projectId ? `/api/instances?project_id=${projectId}` : '/api/instances';
  const response = await context.get(url);

  if (!response.ok()) {
    const error = await response.text();
    throw new Error(`Failed to list instances: ${response.status()} ${error}`);
  }

  return response.json();
}
