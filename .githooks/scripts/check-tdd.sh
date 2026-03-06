#!/bin/bash
# TDD enforcement pre-commit hook
# Verifies test files exist for modified code and coverage meets threshold

set -e

echo "🔍 Checking TDD compliance..."

# Get list of staged Python files in custom_components/localshift/
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep '^custom_components/localshift/.*\.py$' || true)

if [ -z "$STAGED_FILES" ]; then
    echo "✅ No production code changes detected"
    exit 0
fi

# Check if any are exception files (docs, config, etc.)
CHANGES_REQUIRE_TESTS=false

for FILE in $STAGED_FILES; do
    # Skip __init__.py, manifest.json, etc.
    if [[ "$FILE" == *"__init__.py" ]] || [[ "$FILE" == *"manifest.json" ]]; then
        continue
    fi
    
    # This file requires tests
    CHANGES_REQUIRE_TESTS=true
    
    # Extract module name
    MODULE_NAME=$(basename "$FILE" .py)
    
    # Check if corresponding test file exists
    TEST_FILE="tests/test_${MODULE_NAME}.py"
    
    if [ ! -f "$TEST_FILE" ]; then
        echo "❌ ERROR: No test file found for $FILE"
        echo ""
        echo "Expected test file: $TEST_FILE"
        echo ""
        echo "TDD Workflow:"
        echo "  1. Create: $TEST_FILE"
        echo "  2. Write failing test (RED phase)"
        echo "  3. Then commit"
        echo ""
        echo "See: .agents/rules/tdd-workflow.md"
        exit 1
    fi
    
    echo "✅ Test file exists: $TEST_FILE"
done

if [ "$CHANGES_REQUIRE_TESTS" = false ]; then
    echo "✅ Changes don't require tests (config/init files)"
    exit 0
fi

# Run tests for modified files
echo ""
echo "🔍 Running tests for modified files..."

# Extract test file paths
TEST_FILES=$(echo "$STAGED_FILES" | sed 's|custom_components/localshift/\(.*\)\.py|tests/test_\1.py|' | xargs)

# Check if test files exist before running
for TEST_FILE in $TEST_FILES; do
    if [ ! -f "$TEST_FILE" ]; then
        # Already checked above, but double-check
        continue
    fi
done

# Run the tests
if ! uv run pytest $TEST_FILES -v --tb=short 2>&1; then
    echo ""
    echo "❌ ERROR: Tests failed"
    echo ""
    echo "All tests must pass before commit (TDD GREEN phase)"
    echo "See: .agents/rules/tdd-workflow.md"
    exit 1
fi

echo "✅ All tests pass"

# Check coverage threshold (95%)
echo ""
echo "🔍 Checking test coverage..."

COVERAGE_OUTPUT=$(uv run pytest --cov=custom_components/localshift --cov-report=term-missing --cov-fail-under=95 2>&1)
COVERAGE_EXIT_CODE=$?

if [ $COVERAGE_EXIT_CODE -ne 0 ]; then
    echo "$COVERAGE_OUTPUT"
    echo ""
    echo "❌ ERROR: Test coverage below 95% threshold"
    echo ""
    echo "Write more tests to reach 95% coverage before committing"
    echo "See: .agents/rules/tdd-workflow.md"
    exit 1
fi

echo "✅ Coverage meets 95% threshold"
echo ""
echo "✅ TDD compliance checks passed"