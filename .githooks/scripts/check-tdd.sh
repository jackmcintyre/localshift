#!/bin/bash
# TDD enforcement pre-commit hook
# Verifies test files exist for modified code and coverage meets 95% threshold per file

set -e

echo "🔍 Checking TDD compliance..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COVERAGE_CHECKER="$PROJECT_ROOT/scripts/coverage_checker.py"

check_xdist_available() {
    uv run python -c "import xdist" 2>/dev/null
    return $?
}

STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep '^custom_components/localshift/.*\.py$' || true)

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

PARALLEL_FLAG=""
if [ "${LOCALSHIFT_PRECOMMIT_USE_XDIST:-0}" = "1" ] && check_xdist_available; then
    PARALLEL_FLAG="-n logical"
    echo "🚀 Using parallel execution (pytest-xdist)"
else
    echo "🚶 Using serial execution"
fi

TEST_FILES="${TEST_FILE_PATHS[*]}"
COVERAGE_JSON="$PROJECT_ROOT/.coverage-tdd.json"

echo ""
echo "🔍 Running tests with coverage for modified files..."

if [ -n "$COV_FLAGS" ]; then
    set +e
    env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_PREFIX \
        uv run python -m pytest $TEST_FILES $COV_FLAGS \
        $PARALLEL_FLAG \
        --cov-report=json:"$COVERAGE_JSON" \
        --cov-report=term-missing \
        -v --tb=short 2>&1
    PYTEST_EXIT=$?
    set -e
    if [ "$PYTEST_EXIT" -ne 0 ]; then
        echo ""
        echo "❌ ERROR: Tests failed (exit $PYTEST_EXIT)"
        echo ""
        echo "All tests must pass before commit (TDD GREEN phase)"
        echo "See: .agents/rules/tdd-workflow.md"
        rm -f "$COVERAGE_JSON"
        exit 1
    fi
    
    echo ""
    echo "📊 Checking per-file coverage (95% threshold)..."
    
    if [ -f "$COVERAGE_CHECKER" ]; then
        if ! uv run python "$COVERAGE_CHECKER" "$COVERAGE_JSON" "${SOURCE_FILE_PATHS[@]}"; then
            rm -f "$COVERAGE_JSON"
            exit 1
        fi
    else
        echo "⚠️  coverage_checker.py not found, falling back to --cov-fail-under"
        set +e
        env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_PREFIX \
            uv run python -m pytest $TEST_FILES $COV_FLAGS \
            --cov-fail-under=95 \
            -q 2>&1
        PYTEST_EXIT=$?
        set -e
        if [ "$PYTEST_EXIT" -ne 0 ]; then
            echo ""
            echo "❌ ERROR: Coverage below 95% (exit $PYTEST_EXIT)"
            rm -f "$COVERAGE_JSON"
            exit 1
        fi
    fi
    
    rm -f "$COVERAGE_JSON"
    echo ""
    echo "✅ All tests pass with 95%+ coverage per file"
else
    if ! uv run pytest $TEST_FILES $PARALLEL_FLAG -v --tb=short 2>&1; then
        echo ""
        echo "❌ ERROR: Tests failed"
        echo ""
        echo "All tests must pass before commit (TDD GREEN phase)"
        echo "See: .agents/rules/tdd-workflow.md"
        exit 1
    fi
    echo ""
    echo "✅ All tests pass"
fi

echo ""
echo "✅ TDD compliance checks passed"
