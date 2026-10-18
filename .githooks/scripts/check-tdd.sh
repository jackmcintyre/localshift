#!/bin/bash
# TDD enforcement pre-commit hook
# Verifies a test file exists for each modified module, the tests pass, and at
# least 95% of the executable lines the commit adds or modifies are covered
# (issue #1212). Changed lines are measured with the mapped tests first, then
# with the full suite (-n 2) if the mapped tests alone fall short.

set -e

echo "🔍 Checking TDD compliance..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COVERAGE_CHECKER="$PROJECT_ROOT/scripts/coverage_checker.py"

check_xdist_available() {
    uv run python -c "import xdist" 2>/dev/null
    return $?
}

STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACMR | grep '^custom_components/localshift/.*\.py$' || true)

if [ -z "$STAGED_FILES" ]; then
    echo "✅ No production code changes detected"
    exit 0
fi

CHANGES_REQUIRE_TESTS=false
declare -a TEST_FILE_PATHS
declare -a SOURCE_FILE_PATHS

for FILE in $STAGED_FILES; do
    if [[ "$FILE" == *"__init__.py" ]] || [[ "$FILE" == *"manifest.json" ]]; then
        continue
    fi
    
    # Coverage is measured on the working tree, so it must match the index.
    if ! git diff --quiet -- "$FILE"; then
        echo "❌ ERROR: $FILE has unstaged edits, so its coverage would be"
        echo "   measured on code this commit does not contain."
        echo "   stage or stash the remaining edits in $FILE"
        exit 1
    fi

    CHANGES_REQUIRE_TESTS=true
    SOURCE_FILE_PATHS+=("$FILE")
    
    MODULE_NAME=$(basename "$FILE" .py)
    TEST_FLAT="tests/test_${MODULE_NAME}.py"
    
    if [ -f "$TEST_FLAT" ]; then
        TEST_FILE_PATHS+=("$TEST_FLAT")
        echo "✅ Test file exists: $TEST_FLAT"
        continue
    fi
    
    REL_PATH=$(echo "$FILE" | sed 's|custom_components/localshift/||')
    if [[ "$REL_PATH" == *"/"* ]]; then
        SUBDIR=$(dirname "$REL_PATH")
        TEST_SUBDIR="tests/${SUBDIR}/test_${MODULE_NAME}.py"
        if [ -f "$TEST_SUBDIR" ]; then
            TEST_FILE_PATHS+=("$TEST_SUBDIR")
            echo "✅ Test file exists: $TEST_SUBDIR"
            
            # Check for additional test files that test the same module
            # e.g., test_solar_5min.py, test_solar_helpers.py for solar.py
            ADDITIONAL_TESTS=$(find "tests/${SUBDIR}" -name "test_${MODULE_NAME}_*.py" -type f 2>/dev/null)
            if [ -n "$ADDITIONAL_TESTS" ]; then
                for TEST in $ADDITIONAL_TESTS; do
                    TEST_FILE_PATHS+=("$TEST")
                    echo "✅ Additional test file: $TEST"
                done
            fi
            continue
        fi
    fi
    
    echo "❌ ERROR: No test file found for $FILE"
    echo ""
    echo "Searched locations:"
    echo "  - tests/test_${MODULE_NAME}.py"
    REL_PATH=$(echo "$FILE" | sed 's|custom_components/localshift/||')
    if [[ "$REL_PATH" == *"/"* ]]; then
        SUBDIR=$(dirname "$REL_PATH")
        echo "  - tests/${SUBDIR}/test_${MODULE_NAME}.py"
    fi
    echo ""
    echo "TDD Workflow:"
    echo "  1. Create a test file at one of the above locations"
    echo "  2. Write failing test (RED phase)"
    echo "  3. Then commit"
    echo ""
    echo "See: .agents/rules/tdd-workflow.md"
    exit 1
done

if [ "$CHANGES_REQUIRE_TESTS" = false ]; then
    echo "✅ Changes don't require tests (config/init files)"
    exit 0
fi

COV_FLAGS=""
for FILE in "${SOURCE_FILE_PATHS[@]}"; do
    MODULE=$(echo "$FILE" | sed 's|\.py$||' | tr '/' '.')
    COV_FLAGS="$COV_FLAGS --cov=$MODULE"
done

# Parallelism is capped at 2 workers: uncapped fan-out has exhausted memory.
FULL_SUITE_PARALLEL=""
if check_xdist_available; then
    FULL_SUITE_PARALLEL="-n 2"
fi
MAPPED_PARALLEL=""
if [ "${LOCALSHIFT_PRECOMMIT_USE_XDIST:-0}" = "1" ]; then
    MAPPED_PARALLEL="$FULL_SUITE_PARALLEL"
fi

COVERAGE_JSON="$PROJECT_ROOT/.coverage-tdd.json"
trap 'rm -f "$COVERAGE_JSON"' EXIT

# run_with_coverage <label> <pytest args...>: run tests, write $COVERAGE_JSON,
# exit the hook if any test fails.
run_with_coverage() {
    local label="$1"
    shift
    set +e
    env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_PREFIX \
        uv run python -m pytest "$@" $COV_FLAGS \
        --cov-report=json:"$COVERAGE_JSON" --cov-report= \
        -q --tb=short 2>&1
    local status=$?
    set -e
    if [ "$status" -ne 0 ]; then
        echo ""
        echo "❌ ERROR: Tests failed in $label (exit $status)"
        echo ""
        echo "All tests must pass before commit (TDD GREEN phase)"
        echo "See: .agents/rules/tdd-workflow.md"
        exit 1
    fi
}

# check_changed_lines: 95% of the executable lines this commit adds or
# modifies must be covered (issue #1212). Whole-file coverage is not checked.
check_changed_lines() {
    uv run python "$COVERAGE_CHECKER" --changed-lines "$COVERAGE_JSON" \
        "${SOURCE_FILE_PATHS[@]}"
}

STARTED=$(date +%s)
echo ""
echo "🔍 Running mapped tests with coverage..."
run_with_coverage "mapped tests" "${TEST_FILE_PATHS[@]}" $MAPPED_PARALLEL

echo ""
echo "📊 Checking coverage of changed lines (95% threshold)..."
if CHECK_OUTPUT=$(check_changed_lines); then
    echo "$CHECK_OUTPUT"
else
    # Large modules are exercised by many test files, not just the mapped
    # one, so measure again with the whole suite before failing.
    echo "↪  Mapped tests leave changed lines uncovered; re-measuring with"
    echo "   the full suite ${FULL_SUITE_PARALLEL:-(serial)} (takes a few minutes)..."
    run_with_coverage "full suite" $FULL_SUITE_PARALLEL
    echo ""
    if ! check_changed_lines; then
        exit 1
    fi
fi

echo ""
echo "✅ TDD compliance checks passed ($(( $(date +%s) - STARTED ))s)"
