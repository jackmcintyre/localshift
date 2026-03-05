---
trigger: on_pr_creation
---

# PR CI Checks Workflow

**Automatically check GitHub Actions status after PR creation.**

## When This Applies

This workflow triggers when creating a pull request using `gh pr create`.

## Required Steps

### 1. After PR Creation

When you create a PR with `gh pr create`:

1. **Extract PR number** from the output (e.g., `https://github.com/owner/repo/pull/123` → PR #123)
2. **Immediately check CI status:**
   ```bash
   gh pr checks <PR_NUMBER>
   ```
3. **Report initial status** to user with estimated time

### 2. Monitor CI Progress

**Interactive approach (recommended):**

1. After creating PR, inform user:
   ```
   ✅ PR #123 created
   🔄 CI checks running (typically 2-5 minutes)
   
   Options:
   - [W] Wait for results (I'll monitor and report)
   - [C] Continue working (I'll check later)
   ```

2. **If user chooses to wait:**
   - Poll status every 30 seconds:
     ```bash
     while true; do
       gh pr checks <PR_NUMBER> --watch
       sleep 30
     done
     ```
   - Or use interactive watch:
     ```bash
     gh run watch
     ```
   - Report progress for each check
   - Alert immediately on failure
   - Confirm success when all pass

3. **If user chooses to continue:**
   - Note the PR number for later
   - Continue with other tasks
   - Check status periodically (every 2-3 minutes)
   - Notify user when checks complete

### 3. Status Reporting

**Check Status Categories:**

| Status | Icon | Action |
|--------|------|--------|
| Pending | ⏳ | Continue monitoring |
| In Progress | 🔄 | Show progress if available |
| Success | ✅ | Report success, ready to merge |
| Failure | ❌ | Alert user, show failure details |
| Skipped | ⏭️ | Note but continue |

**Detailed Status Command:**
```bash
gh pr checks <PR_NUMBER> --watch
```

Or for more details:
```bash
gh run list --branch <BRANCH_NAME>
gh run view <RUN_ID>
```

### 4. Failure Handling

**If CI checks fail:**

1. **Stop and alert immediately:**
   ```
   ❌ CI checks failed on PR #123
   
   Failed checks:
   - lint: ruff found formatting issues
   - test: 3 tests failed
   
   View details: gh run view <RUN_ID>
   ```

2. **Fetch failure details:**
   ```bash
   gh run view <RUN_ID> --log-failed
   ```

3. **Ask user:**
   - Fix issues now?
   - View full logs?
   - Continue anyway?

4. **If fixing:**
   - Make necessary changes
   - Commit and push
   - Monitor new CI run

### 5. Success Handling

**When all checks pass:**

1. **Report success:**
   ```
   ✅ All CI checks passed on PR #123
   
   Checks:
   - lint: ✅ passed (45s)
   - test: ✅ passed (1m 23s)
   - duplicate-check: ✅ passed (12s)
   
   PR is ready for review/merge.
   View PR: gh pr view 123
   ```

2. **Suggest next steps:**
   - Ready to merge?
   - Request review?
   - Continue working?

## CI Workflows in This Repository

The following workflows run on PRs:

1. **Lint** (`.github/workflows/lint.yml`)
   - ruff lint check
   - ruff format check
   - vulture (dead code)
   - bandit (security)
   - pyright (types, non-blocking)
   - interrogate (docstrings)
   - deptry (dependencies)

2. **Test** (`.github/workflows/test.yml`)
   - pytest test suite

3. **Duplicate Check** (`.github/workflows/duplicate-check.yml`)
   - jscpd duplicate code detection

**Typical duration:** 2-5 minutes total

## Example Workflow

```
Agent: Creating PR for issue #123...
Agent: Running: gh pr create --title "Fix widget bug" --body "..."
Agent: 
  ✅ PR #456 created
  https://github.com/owner/repo/pull/456
  
  🔄 CI checks starting...
  
  Wait for results? [W] or Continue [C]

User: W

Agent: Monitoring CI checks...
  - lint: ⏳ pending
  - test: ⏳ pending
  - duplicate-check: ⏳ pending
  
  [30s later]
  - lint: 🔄 in progress
  - test: ⏳ pending
  - duplicate-check: ✅ passed
  
  [60s later]
  - lint: ✅ passed (1m 5s)
  - test: 🔄 in progress
  
  [90s later]
  - lint: ✅ passed
  - test: ✅ passed (1m 23s)
  - duplicate-check: ✅ passed
  
  ✅ All CI checks passed!
  PR #456 is ready for review.
```

## Integration with Worktree Workflow

**When creating PR from worktree:**

1. Verify you're on issue branch (not main)
2. Create PR
3. Run CI checks workflow (this document)
4. If checks fail:
   - Make fixes in current worktree
   - Commit and push (triggers new CI run)
   - Re-monitor
5. If checks pass:
   - PR ready for review
   - User can merge when approved

## Notes

- CI checks are **blocking** for this repository (branch protection rules)
- All checks must pass before merge
- Pre-commit hooks run locally, but CI runs additional checks
- Use `gh run watch` for detailed real-time progress
- Use `gh pr checks` for quick status overview