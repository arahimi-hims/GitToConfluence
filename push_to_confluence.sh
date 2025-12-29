#!/bin/bash

# Check if mark is installed
if ! command -v mark &> /dev/null; then
    echo "Error: 'mark' CLI tool is not installed."
    read -p "Do you want to install it with Homebrew? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Installing mark..."
        brew tap kovetskiy/mark && brew install mark
    else
        echo "Aborting. Please install 'mark' to continue."
        exit 1
    fi
fi

# Ensure environment variables are set
if [ -z "$MARK_USERNAME" ] || [ -z "$MARK_PASSWORD" ]; then
    echo "Error: MARK_USERNAME and MARK_PASSWORD environment variables must be set."
    echo "Usage: export MARK_USERNAME='your_email' MARK_PASSWORD='your_api_token'"
    echo "You can create a Confludence API token token by visiting https://id.atlassian.com/manage-profile/security/api-tokens"
    exit 1
fi


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

add_header_to_markdown() {
    local source_file="$1"
    local header_text="$2"
    local temp_file="${source_file%.*}_CONFLUENCE.md"

    local first_header_line=$(grep -n "^#" "$source_file" | head -n 1 | cut -d: -f1)

    if [ -n "$first_header_line" ]; then
        HEADER_TEXT="$header_text" awk -v n="$first_header_line" 'NR==n {print ENVIRON["HEADER_TEXT"]} {print}' "$source_file" > "$temp_file"
    else
        {
            echo "$header_text"
            echo ""
            cat "$source_file"
        } > "$temp_file"
    fi
    echo "$temp_file"
}

push_to_confluence() {
    local markdown_file="$1"
    shift
    
    # Slap a header on top of the confluence page.
    local header=$'<!-- -->\n'"**Note:** _This page is automatically generated from [this document in git]($(get_markdown_url "$markdown_file")). Please do not edit directly._"
    local temp_file=$(add_header_to_markdown "$markdown_file" "$header")

    # Run mark on the temporary file
    mark -b "https://forhims.atlassian.net/wiki" --space "MLE" --title-from-h1 --drop-h1 --changes-only --strip-linebreaks -f "$temp_file" "$@"

    # Cleanup
    rm "$temp_file"
}

push_to_confluence "PROPOSAL.md"
push_to_confluence "PHASE_1_DESIGN.md" --mermaid-scale 2.0