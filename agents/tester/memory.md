# Memory

*No memories yet. I am freshly initialized.*

- llm-supervisor-proxy test pack creation (2026-04-06): Project has 819+ Go tests across 22 packages. Key learning: opencode sessions timeout on large tasks (testing 1000+ line files). Split into sub-tasks of 200-400 lines each. Race executor (1061 lines) needed 2 sessions. Always run `go vet ./...` after creating tests - catches resp-before-err patterns.