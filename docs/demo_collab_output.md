# Demo: Multi-Agent LLM Collaboration Output

Command: `python demo_collab.py --mock`

```
============================================================
  Cyber Town -- Multi-Agent LLM Collaboration Demo
  Mode: mock (dry-run)
============================================================

[Request] Help me create a complete plan for the new "User Login Page" feature

[Coordinator] Decomposing task...
  -> Decomposed into 3 subtasks:
    [产品经理] Write requirements document
    [Python工程师] Estimate technical solution
    [UI设计师] Design UI layout

[Coordinator] Assigning tasks...
  OK Write requirements document -> li_si
  OK Estimate technical solution -> zhang_san
  OK Design UI layout -> wang_wu

[EventBus] Running...
  Total dispatched: 6 events

[Coordinator] Synthesizing results...
  Collected 3 results

============================================================
  Final Plan
============================================================
[Collaboration Summary]

1. Requirements Overview: The user login page should support email+password
   and SMS verification code methods.

2. Technical Solution: Use JWT authentication, React frontend + FastAPI
   backend, estimated 3 person-days.

3. Design Recommendations: Clean style, mobile-first, reference Apple login
   page layout.

4. Next Steps: PM outputs PRD -> Designer creates high-fidelity mockup ->
   Engineer develops.
============================================================
```

## Event Trace

| # | Type | Source | Target | Content |
|---|------|--------|--------|---------|
| 1 | task_assigned | coordinator | li_si | Write requirements document for user login page... |
| 2 | task_assigned | coordinator | zhang_san | Estimate technical implementation for login feature... |
| 3 | task_assigned | coordinator | wang_wu | Design UI layout for login page... |
| 4 | task_done | li_si | coordinator | [Li Si / PM] Completed task. Output detailed requirements document. |
| 5 | task_done | zhang_san | coordinator | [Zhang San / Engineer] Completed task. Output technical estimation. |
| 6 | task_done | wang_wu | coordinator | [Wang Wu / Designer] Completed task. Output UI design proposal. |

## Flow Summary

```
decompose_with_llm("Plan login page feature")
  -> 3 subtasks (PM / Engineer / Designer)
  -> assign_by_role() x3
  -> EventBus.run_until_idle()
  -> 6 events dispatched (3 task_assigned + 3 task_done)
  -> collect_results() -> 3 results
  -> synthesize_results() -> final integrated plan
```
