#!/bin/bash
# Quick dead code summary

echo "=========================================="
echo "DEAD CODE QUICK SUMMARY"
echo "=========================================="
echo ""

echo "📊 Coverage Status:"
uv run coverage report --format=total 2>/dev/null | tail -1
echo ""

echo "📄 Files with 0% Coverage:"
uv run coverage report --fail-under=0 --show-missing 2>/dev/null | grep "0%" | head -10
echo ""

echo "🔍 Custom Detector (High Confidence Only):"
echo "Running..."
uv run python scripts/find_dead_code.py custom_components/localshift --min-confidence high 2>&1 | grep "SUMMARY" -A 6
echo ""

echo "📚 Full Report: docs/DEAD_CODE_REPORT.md"
echo "📊 HTML Coverage: htmlcov/index.html"
echo ""
