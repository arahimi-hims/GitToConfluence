 #!/bin/bash

# Check if pandoc is installed
if ! command -v pandoc &> /dev/null; then
    echo "Error: 'pandoc' is not installed."
    echo "Please install it via brew install pandoc"
    exit 1
fi

# Check if mmdc is installed
if ! command -v mmdc &> /dev/null; then
    echo "Error: 'mmdc' is not installed."
    echo "Please install it via npm install -g @mermaid-js/mermaid-cli"
    exit 1
fi

# Ensure environment variables are set
if [ -z "$MARK_USERNAME" ] || [ -z "$MARK_PASSWORD" ]; then
    echo "Error: MARK_USERNAME and MARK_PASSWORD environment variables must be set."
    echo "Usage: export MARK_USERNAME='your_email' MARK_PASSWORD='your_api_token'"
    echo "You can create a Confluence API token token by visiting https://id.atlassian.com/manage-profile/security/api-tokens"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/push_to_confluence.py"


get_markdown_url() {
    local target_file="$1"
    local remote_url=$(git config --get remote.origin.url)
    local branch=$(git branch --show-current)
    local repo_root=$(git rev-parse --show-toplevel)

    if [[ "$remote_url" =~ ^git@ ]]; then
        remote_url=${remote_url/:/\/}
        remote_url=${remote_url/git@/https:\/\/}
    fi
    remote_url=${remote_url%.git}

    local file_path=$(cd "$repo_root" && git ls-files --full-name "**/${target_file}" 2>/dev/null | head -n 1)
    [ -z "$file_path" ] && file_path="${target_file}"

    echo "${remote_url}/blob/${branch}/${file_path}"
}

push_to_confluence() {
    local markdown_file="$1"
    shift
    
    local header="**Note:** _This page is automatically generated from [this document in git]($(get_markdown_url "$markdown_file")). Please do not edit directly._"

    echo "Pushing $markdown_file to Confluence..."
    uv run "$PYTHON_SCRIPT" \
        "$markdown_file" \
        --space "MLE" \
        --parents "Swolness Pamphlet" \
        --header "$header" \
        "$@"
}

push_to_confluence "PROPOSAL.md" 
push_to_confluence "PHASE_1_DESIGN.md" --scale-mermaid 2. 
