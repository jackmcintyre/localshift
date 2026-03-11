#!/bin/bash
# TDD enforcement pre-commit hook
# Verifies test files exist for modified code and coverage meets threshold

set -e

echo "🔍 Checking TDD compliance..."

# Function to find test file for a given source file
# Returns the path to the test file if found, or empty string if not found
find_test_file() {
    local SOURCE_FILE="$1"
    local MODULE_NAME=$(basename "$SOURCE_FILE" .py)
    
    # Pattern 1: Flat test structure (most common)
    # custom_components/localshift/module.py -> tests/test_module.py
    local TEST_FLAT="tests/test_${MODULE_NAME}.py"
    if [ -f "$TEST_FLAT" ]; then
        echo "$TEST_FLAT"
        return 0
    fi
    
    # Pattern 2: Subdirectory-preserving test structure
    # custom_components/localshift/subdir/module.py -> tests/subdir/test_module.py
    local REL_PATH=$(echo "$SOURCE_FILE" | sed 's|custom_components/localshift/||')
    if [[ "$REL_PATH" == *"/"* ]]; then
        local SUBDIR=$(dirname "$REL_PATH")
        local TEST_SUBDIR="tests/${SUBDIR}/test_${MODULE_NAME}.py"
        if [ -f "$TEST_SUBDIR" ]; then
            echo "$TEST_SUBDIR"
            return 0
        fi
    fi
    
    # No test file found
    return 1
}

# Check if pytest-xdist is available for parallel execution
check_xdist_available() {
    uv run python -c "import xdist" 2>/dev/null
    return $?
}

# Get list of staged Python files in custom_components/localshift/
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep '^custom_components/localshift/.*\.py$' || true)

if [ -z "$STAGED_FILES" ]; then
    echo "✅ No production code changes detected"
    exit 0
fi

# Check if any are exception files (docs, config, etc.)
CHANGES_REQUIRE_TESTS=false

# Array to collect test file paths
declare -a TEST_FILE_PATHS

for FILE in $STAGED_FILES; do
    # Skip __init__.py, manifest.json, etc.
    if [[ "$FILE" == *"__init__.py" ]] || [[ "$FILE" == *"manifest.json" ]]; then
        continue
    fi
    
    # This file requires tests
    CHANGES_REQUIRE_TESTS=true
    
    # Find the test file using our function
    TEST_FILE=$(find_test_file "$FILE")
    
    if [ -z "$TEST_FILE" ]; then
        echo "❌ ERROR: No test file found for $FILE"
        echo ""
        echo "Searched locations:"
        echo "  - tests/test_$(basename "$FILE" .py).py"
        
        # Check if file is in subdirectory
        REL_PATH=$(echo "$FILE" | sed 's|custom_components/localshift/||')
        if [[ "$REL_PATH" == *"/"* ]]; then
            SUBDIR=$(dirname "$REL_PATH")
            echo "  - tests/${SUBDIR}/test_$(basename "$FILE" .py).py"
        fi
        
        echo ""
        echo "TDD Workflow:"
        echo "  1. Create a test file at one of the above locations"
        echo "  2. Write failing test (RED phase)"
        echo "  3. Then commit"
        echo ""
        echo "See: .agents/rules/tdd-workflow.md"
        exit 1
    fi
    
    echo "✅ Test file exists: $TEST_FILE"
    TEST_FILE_PATHS+=("$TEST_FILE")
done

if [ "$CHANGES_REQUIRE_TESTS" = false ]; then
    echo "✅ Changes don't require tests (config/init files)"
    exit 0
fi

# Build --cov flags only for staged files (not entire codebase)
COV_FLAGS=""
for FILE in $STAGED_FILES; do
    if [[ "$FILE" == *"__init__.py" ]]; then
        continue
    fi
    MODULE=$(echo "$FILE" | sed 's|\.py$||' | tr '/' '.')
    COV_FLAGS="$COV_FLAGS --cov=$MODULE"
done

# Check for xdist availability and set parallel flag
PARALLEL_FLAG=""
if check_xdist_available; then
    PARALLEL_FLAG="-n logical"
    echo "🚀 Using parallel execution (pytest-xdist)"
fi

# Convert array to space-separated string
TEST_FILES="${TEST_FILE_PATHS[*]}"

# Run tests with coverage in single invocation
echo ""
echo "🔍 Running tests with coverage for modified files..."

if [ -n "$COV_FLAGS" ]; then
    if ! uv run pytest $TEST_FILES $COV_FLAGS \
        $PARALLEL_FLAG \
        --cov-report=term-missing \
        --cov-fail-under=95 \
        -v --tb=short 2>&1; then
        echo ""
        echo "❌ ERROR: Tests failed or coverage below 95%"
        echo ""
        echo "All tests must pass and modified files must have 95% coverage"
        echo "See: .agents/rules/tdd-workflow.md"
        exit 1
    fi
    echo ""
    echo "✅ All tests pass with 95%+ coverage"
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