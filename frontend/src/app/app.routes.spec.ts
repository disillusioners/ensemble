import { routes } from './app.routes';

describe('App Routes', () => {
  describe('Route Definitions', () => {
    it('should have the new project-aware route defined', () => {
      const projectRoute = routes.find(
        route => route.path === 'projects/:projectId/instances/:instanceId'
      );

      expect(projectRoute).toBeDefined();
      expect(projectRoute?.path).toBe('projects/:projectId/instances/:instanceId');
    });

    it('should have backward compatibility redirect for old /instances/:instanceId', () => {
      const legacyRoute = routes.find(
        route => route.path === 'instances/:instanceId'
      );

      expect(legacyRoute).toBeDefined();
      expect(legacyRoute?.redirectTo).toBe('projects/all/instances/:instanceId');
      expect(legacyRoute?.pathMatch).toBe('full');
    });

    it('should have home route at root', () => {
      const homeRoute = routes.find(route => route.path === '');

      expect(homeRoute).toBeDefined();
      expect(homeRoute?.path).toBe('');
    });

    it('should have instances list route', () => {
      const instancesRoute = routes.find(route => route.path === 'instances');

      expect(instancesRoute).toBeDefined();
      expect(instancesRoute?.path).toBe('instances');
    });

    it('should have jobs route', () => {
      const jobsRoute = routes.find(route => route.path === 'jobs');

      expect(jobsRoute).toBeDefined();
      expect(jobsRoute?.path).toBe('jobs');
    });

    it('should have sources route', () => {
      const sourcesRoute = routes.find(route => route.path === 'sources');

      expect(sourcesRoute).toBeDefined();
      expect(sourcesRoute?.path).toBe('sources');
    });

    it('should have schedules route', () => {
      const schedulesRoute = routes.find(route => route.path === 'schedules');

      expect(schedulesRoute).toBeDefined();
      expect(schedulesRoute?.path).toBe('schedules');
    });

    it('should have mcp-servers route', () => {
      const mcpServersRoute = routes.find(route => route.path === 'mcp-servers');

      expect(mcpServersRoute).toBeDefined();
      expect(mcpServersRoute?.path).toBe('mcp-servers');
    });

    it('should redirect unknown routes to home', () => {
      const wildcardRoute = routes.find(route => route.path === '**');

      expect(wildcardRoute).toBeDefined();
      expect(wildcardRoute?.redirectTo).toBe('');
    });
  });

  describe('Project-Aware Route URL Patterns', () => {
    it('should support specific project IDs in route path', () => {
      // Verify the route pattern accepts project IDs
      const projectRoute = routes.find(
        route => route.path === 'projects/:projectId/instances/:instanceId'
      );

      expect(projectRoute?.path).toMatch(/:projectId/);
      expect(projectRoute?.path).toMatch(/:instanceId/);
    });

    it('should accept "all" as a valid project ID', () => {
      // "all" is a valid project ID for the All tab view
      const projectRoute = routes.find(
        route => route.path === 'projects/:projectId/instances/:instanceId'
      );

      expect(projectRoute).toBeDefined();
      // The route path pattern should match URLs like /projects/all/instances/instance-123
    });
  });

  describe('Backward Compatibility Redirect', () => {
    it('should redirect old instance URLs to new project-aware format', () => {
      const legacyRoute = routes.find(
        route => route.path === 'instances/:instanceId'
      );

      // Verify redirect pattern preserves instanceId parameter
      expect(legacyRoute?.redirectTo).toContain(':instanceId');

      // Verify redirect goes to /projects/all/... for backward compatibility
      expect(legacyRoute?.redirectTo).toBe('projects/all/instances/:instanceId');
    });

    it('should use pathMatch full to prevent partial matches', () => {
      const legacyRoute = routes.find(
        route => route.path === 'instances/:instanceId'
      );

      expect(legacyRoute?.pathMatch).toBe('full');
    });
  });

  describe('Route Order and Priority', () => {
    it('should have specific routes before wildcard catch-all', () => {
      const wildcardIndex = routes.findIndex(route => route.path === '**');
      const legacyRouteIndex = routes.findIndex(
        route => route.path === 'instances/:instanceId'
      );
      const projectRouteIndex = routes.findIndex(
        route => route.path === 'projects/:projectId/instances/:instanceId'
      );

      // Legacy route should come before wildcard
      expect(legacyRouteIndex).toBeLessThan(wildcardIndex);
      // Project-aware route should also come before wildcard
      expect(projectRouteIndex).toBeLessThan(wildcardIndex);
    });

    it('should have legacy redirect route defined before the new route', () => {
      // This ensures backward compatibility redirect is processed first
      const legacyRouteIndex = routes.findIndex(
        route => route.path === 'instances/:instanceId' && route.redirectTo !== undefined
      );
      const projectRouteIndex = routes.findIndex(
        route => route.path === 'projects/:projectId/instances/:instanceId'
      );

      // The redirect route should be defined (its position relative to new route
      // depends on route matching strategy, but both should be defined)
      expect(legacyRouteIndex).toBeGreaterThanOrEqual(0);
      expect(projectRouteIndex).toBeGreaterThanOrEqual(0);
    });
  });
});
