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

    cp "$source_file" "$temp_file"

    if grep -q "^-->" "$temp_file"; then
        # Insert after the closing comment tag
        sed -i '' '/^-->/a\
'"$header_text"'' "$temp_file"
    else
        # Insert before the first H1 if no frontmatter block found, or fallback to top
        if grep -q "^# " "$temp_file"; then
            awk -v header="$header_text" '!f && /^# / { print header; print ""; f=1 } 1' "$temp_file" > "${temp_file}.tmp" && mv "${temp_file}.tmp" "$temp_file"
        else
            echo -e "$header_text\n\n$(cat "$temp_file")" > "$temp_file"
        fi
    fi
    echo "$temp_file"
}

# Slap a header on top of the confluence page.
HEADER="**Note:** This page is automatically generated from [this document in git]($(get_markdown_url "PROPOSAL.md")). Please do not edit directly."
TEMP_FILE=$(add_header_to_markdown "PROPOSAL.md" "$HEADER")

# Run mark on the temporary file
mark -b "https://forhims.atlassian.net/wiki" --space "MLE" --title-from-h1 --drop-h1 --changes-only --strip-linebreaks -f "$TEMP_FILE"

# Cleanup
rm "$TEMP_FILE"
