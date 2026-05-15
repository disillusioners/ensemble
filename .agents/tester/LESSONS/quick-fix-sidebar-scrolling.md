# Quick Fix: Instance Sidebar Scrolling

**Date**: 2026-05-15
**Commit**: 88580db
**Session**: fix-sidebar-scroll

## Issue
Instance list sidebar on chat page (`/instances/{id}`) cannot scroll when there are many instances. Previous fix attempt (b373d6d) didn't resolve the issue.

## Root Cause
Flexbox height chain was broken — containers grew to fit content instead of being constrained to viewport:

- `.instance-sidebar` had `overflow: visible` → should be `hidden`
- Angular component `<app-instance-list>` had no `:host` styling → couldn't fill parent properly
- `.instance-list-container` grew to 4649px (content height) instead of being constrained to 628px (sidebar height)
- `.instance-list` had `overflow-y: auto` but no constrained parent height, so it couldn't scroll

## Investigation Method
Browser automation (Playwright):
1. Created 25 instances via API to overflow sidebar
2. Checked computed styles with `page.evaluate()`
3. Compared `scrollHeight` vs `clientHeight` for each container
4. Tested programmatic scrolling

## Fix (2 files, 14 lines added, 1 line changed)

### `frontend/src/app/pages/chat/chat.scss`
- Changed `.instance-sidebar` from `overflow: visible` to `overflow: hidden`
- Added `height: 100%`

### `frontend/src/app/components/instance-list/instance-list.scss`
- Added `:host` styles: `display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden`
- Added `max-height: 100%` on `.instance-list-container` and `.instance-list`
- Added `min-height: 0` on `.instance-list`

## Key CSS Insight
When using flexbox for scrolling containers, the entire chain from viewport to scrollable element must have constrained heights:
1. Parent: `overflow: hidden; height: 100%` (constrains to viewport)
2. Component host: `:host { flex: 1; min-height: 0; overflow: hidden }` (fills parent, allows shrinking)
3. Scrollable child: `overflow-y: auto; max-height: 100%` (scrolls within constraint)

Without `min-height: 0` on flex children, they won't shrink below their content size.
