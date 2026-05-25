# Quick Fix: Sidebar Pause Button Visibility Alignment

**Date:** 2026-05-26  
**Feature:** Fix Pause Button for Instance with Job Queue  
**Commit:** `7101ab7`

## Issue
The instance list sidebar only showed the pause button for `status === 'running'`, while the message-input component considered `waiting_children` and `queued` as pausable states too. This inconsistency meant users couldn't pause instances in `waiting_children` or `queued` states from the sidebar.

## Fix
Updated `frontend/src/app/components/instance-list/instance-list.html` to match the message-input component's visibility conditions:

```html
<!-- Before: Only running -->
@if (instance.status === 'running') {

<!-- After: Running, waiting_children, queued -->
@if (instance.status === 'running' || instance.status === 'waiting_children' || instance.status === 'queued') {
```

## Lesson
When implementing UI toggle buttons, check ALL components that use the same toggle for consistent visibility conditions. Cross-reference with other components that implement similar functionality.
