# Git to Confluence

Synchronize Markdown files that are versioned in Git to Confluence.

It's often easier for people to drop comments in Confluence than in a Git repo.
At the same time, Confluence is a poor place to write documents. This tools lets
you write your documents in Markdown in your favorite editor, then upload it to
Confluence. When you update the Markdown, the comments from previous version of
the Confluence document are migrated to the revised Confluence document.

## Setup

You can run `setup.sh` to install all the dependnecies, or do it manually like this:

Install the following system dependencies:

- **Pandoc**: Required for converting Markdown to HTML: `brew install pandoc`
- **Mermaid CLI** (Optional): Required if your documents contain Mermaid diagrams: `npm install -g @mermaid-js/mermaid-cli`.

Install [uv](https://github.com/astral-sh/uv) to manage this project's dependencies:

```bash
pip install uv
uv sync
```

## Usage

**Environment Variables:**

You'll need to create a confluence token [from here](https://id.atlassian.com/manage-profile/security/api-tokens). Then populate these environment variables:

- `CONFLUENCE_EMAIL`: Your Atlassian email.
- `CONFLUENCE_API_TOKEN`: The Atlassian API token you just created.

**Run the script:**

Push a Markdown file to a Confluence page:

```bash
uv run push_to_confluence.py <file> --space <SPACE_KEY> [options]
```

**Arguments:**

- `file`: Path to the Markdown file.
- `--space`: Confluence space key (required).
- `--parent-id`: Parent page ID or title.
- `--header`: Markdown string to inject at the top. Defaults to a note that links to the location of the file in the Git repository.
- `--confluence-url`: Confluence base URL (default: https://forhims.atlassian.net/wiki).
- `--copy-comments-from-version`: Version to copy comments from. Defaults to the latest version of the page.
- `--scale-mermaid`: Scale factor for Mermaid diagrams. Defaults to 1.0. Use a larger value if your mermaid diagrams look pixelated on Confluence.

## Testing

There are unit tests that check the underlying algorithms. There are also CI
tests that make sure the Confluence integration works. These are run through
GitHub Actions. You can also run them locally like this:

```bash
uv run pytest
```
