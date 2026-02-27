# Workflow

## Task Processing

1. **Parse Requirements** — Extract specific implementation needs
2. **Design** — Sketch the approach before coding

---

## Implementation Loop (Max 3 iterations)

### Setup Sessions
- Use `opencode_skill` to create **Session A (Implementer)**: runs the current workflow (implement + test)
- Use `opencode_skill` to create **Session B (Reviewer)**: standalone review session

### Loop (up to 3 times)

**Iteration N:**

1. **Session A — Implement & Test**
   - Implement the code based on requirements
   - Run tests to verify functionality
   - Report completion status

2. **Request Session B Review**
   - Use `opencode_skill --sync` to send prompt to Session B
   - Ask Session B to verify implementation against requirements
   - Request specific feedback on:
     - Correctness
     - Code quality
     - Edge cases
     - Missing functionality

3. **Receive Review Feedback**
   - Get Session B's review report

4. **Evaluate & Decide**
   - If review passes AND coder agrees implementation is good → **Proceed to Report**
   - If issues found → Pass feedback to Session A for improvements
   - Increment iteration count
   - If iteration >= 3 → **Proceed to Report** (even if not perfect)

5. **Improve (if not done)**
   - Send Session B's feedback to Session A
   - Session A implements fixes
   - Session A re-runs tests
   - Continue to next iteration

---

## Post-Loop

6. **Report** — Summarize what was done, including iteration count and any remaining issues
7. **Learn** — Record observations in memory.md
8. **Evolve** — Propose improvements per growth.md rules

---

## Code Quality Standards

- Follow language idioms and best practices
- Add comments for complex logic
- Use meaningful variable names
- Keep functions focused and small
