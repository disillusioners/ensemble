# 2026-05-15-tab-bar-visibility-fix.md

## Tab Bar Visibility Fix on Instance Chat Page

### Problem
The `<app-project-tab-bar>` was not visible on the instance chat page (`/instances/{id}`).

### Root Causes (Two Issues)

#### 1. Layout Structure Issue
- Tab bar was placed INSIDE `.instance-sidebar` instead of as a direct child of `.chat-container`
- `.instance-sidebar` had no CSS defined, so it wasn't rendering properly as a sidebar

#### 2. Visual Invisibility (The Subtle Bug)
- Even after fixing layout, the tab bar was STILL nearly invisible
- **Background color (`#0f172a`) was IDENTICAL to the app header's background**
- Tab text was muted gray (`#64748b`) on same dark background — minimal contrast
- Only a 1px bottom border as visual separation

### Solution

**Layout Fix (chat.html + chat.scss):**
```
.chat-container (flex-direction: column)
├── app-project-tab-bar  ← Direct child, full width
└── .main-content (row layout)
    ├── .instance-sidebar (280px, flex-shrink: 0)
    └── .chat-area (flex: 1)
```

**Visual Fix (project-tab-bar.component.scss):**
- Changed background to `#1e293b` (lighter than header's `#0f172a`)
- Added top border for separation
- Changed tab text to `$text-secondary` (`#94a3b8`) for better contrast
- Increased "+" button opacity to 0.85

### Key Lesson
When a component appears "missing", it might actually be rendering but with the same visual properties as an adjacent element. Always check:
1. Background colors match adjacent elements
2. Text contrast is sufficient
3. DOM structure (check DevTools, not just visual)
