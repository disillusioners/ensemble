# Mock Tests Inventory

## Browser Automation Test: Scheduler Frontend E2E

### Metadata
- **Created**: 2025-01-20
- **Script**: `frontend-e2e-scheduler.test.ts` (to be created)
- **Language**: TypeScript with Playwright
- **Status**: PLANNED

### Configuration
- **Timeout**: 60 seconds
- **Frontend Port**: 4200 (development server)
- **Backend Port**: 8000 (API server)
- **Cleanup**: Kill dev server processes after test

### What It Tests
- Navigation to `/schedules` route
- Schedule list/grid view rendering
- Create Schedule dialog functionality
- Schedule card display
- Schedule detail drawer
- Status filter functionality
- Navigation links

### Test Scenarios
1. **Navigate to Schedules Page**
   - Open browser to `http://localhost:4200/schedules`
   - Verify page loads with title/header
   - Verify schedule list container exists

2. **Verify Schedule List/Grid View**
   - Check for schedule cards or grid items
   - Verify layout renders correctly

3. **Test Create Schedule Button**
   - Click "Create Schedule" button
   - Verify dialog opens
   - Verify dialog contains form fields

4. **Verify Schedule Cards**
   - Check schedule cards display with mock data
   - Verify card layout and information

5. **Test Schedule Detail Drawer**
   - Click on a schedule card
   - Verify detail drawer opens
   - Verify drawer displays schedule details

6. **Test Status Filters**
   - Click status filter buttons (active, paused, etc.)
   - Verify schedule list updates based on filter

7. **Test Navigation Link**
   - Click schedules navigation link
   - Verify navigation to `/schedules`

### Success Criteria
- [ ] All scenarios pass
- [ ] Page load time < 5 seconds
- [ ] No browser console errors
- [ ] No process leaks (cleanup successful)

### Implementation Notes
- Requires frontend dev server running on port 4200
- May need to mock backend API responses
- Use Playwright for cross-browser compatibility
- Take screenshots on failure for debugging

### Last Run
- **Date**: 2026-03-22
- **Session**: ses_2e980fc65ffe7xYPaFW6xSuoaI
- **Result**: PASS (7/7 scenarios passed)
- **Quick Fixes Applied**:
  1. Backend API format fix: `/api/schedules` now returns `{ schedules: [...] }`
  2. Frontend click handler: Added click-to-view for schedule detail drawer
- **Commit**: 353b530
- **Report**: `.agents/tester/RESULTS/2026-03-22-scheduler-create-test.md`
- **Screenshots**: `/test-results/` directory (21 screenshots)

### Previous Run
- **Date**: 2026-03-22
- **Session**: ses_2e996ea1fffeXwXa1vEK7WyK2K
- **Result**: PARTIAL (5/7 scenarios passed, 2 skipped due to no data)
- **Quick Fixes**: None required
- **Report**: `.agents/tester/RESULTS/2026-03-22-scheduler-frontend-e2e.md`
- **Screenshots**: `/test-results/` directory (12 screenshots)
