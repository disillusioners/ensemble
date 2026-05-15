# Test Report: Instance Sidebar Scrolling Fix
**Date**: 2026-05-15
**Session**: fix-sidebar-scroll (ses_1d5d86954ffeMc5PhC0WR5yBDI)

## Summary
- **Task**: Fix instance sidebar scrolling on chat page
- **Status**: ✅ PASS
- **Quick Fixes Applied**: 1 (CSS fix, 14 lines added, 1 changed)
- **Commit**: 88580db

## Investigation Results (Browser Automation)

### Before Fix
| Element | Height | ScrollHeight | ClientHeight | Can Scroll |
|---------|--------|-------------|-------------|------------|
| `.instance-sidebar` | 628px | 4649 | 628 | ✓ (but overflow visible) |
| `.instance-list-container` | 4649px | 4649 | 4649 | ✗ |
| `.instance-list` | 4546px | 4546 | 4546 | ✗ |

### After Fix
| Element | Height | ScrollHeight | ClientHeight | Can Scroll |
|---------|--------|-------------|-------------|------------|
| `.instance-sidebar` | 628px | 628 | 628 | ✓ |
| `.instance-list-container` | 628px | 628 | 628 | ✓ |
| `.instance-list` | 525px | 4546 | 525 | ✓ (scrollable!) |

## Root Cause
Flexbox height chain broken — containers grew to fit content (4649px) instead of being constrained to viewport (628px). Three issues:
1. `.instance-sidebar` had `overflow: visible`
2. Angular component `<app-instance-list>` had no `:host` styling
3. List container and list had no `max-height` constraint

## Fix Applied
- **`chat.scss`**: Changed `overflow: visible` → `overflow: hidden`, added `height: 100%`
- **`instance-list.scss`**: Added `:host` flex styles, `max-height: 100%` on containers, `min-height: 0` on list

## Verification (Browser Automation)
- ✅ Scrolling works (scrollTop changes from 0 to 500+)
- ✅ Tab bar remains visible (36px at y=56)
- ✅ Chat area unaffected (628px height)
- ✅ Layout doesn't break

## Files Changed
- `frontend/src/app/pages/chat/chat.scss` (1 changed)
- `frontend/src/app/components/instance-list/instance-list.scss` (14 added)
