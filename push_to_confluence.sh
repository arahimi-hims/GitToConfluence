#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/push_to_confluence.py"

push_to_confluence() {
    local markdown_file="$1"
    shift
    
    echo "Pushing $markdown_file to Confluence..."
    uv run "$PYTHON_SCRIPT" \
        "$markdown_file" \
        --space "MLE" \
        ${CONFLUENCE_PARENT_ID:+--parent-id "$CONFLUENCE_PARENT_ID"} \
        "$@"
}

push_to_confluence "PROPOSAL.md" 
push_to_confluence "PHASE_1_DESIGN.md" --scale-mermaid 2. 
