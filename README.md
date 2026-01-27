# Git to Confluence

Synchronize Markdown files that are versioned in Git to Confluence.

## Setup

This project uses [uv](https://github.com/astral-sh/uv) for dependency management.

```bash
pip install uv
uv sync
```

## Usage

### push_to_confluence.py

Pushes a Markdown file to a Confluence page.

```bash
uv run push_to_confluence.py <file> --space <SPACE_KEY> [options]
```

**Arguments:**

- `file`: Path to the Markdown file.
- `--space`: Confluence space key (required).
- `--parent-id`: Parent page ID or title.
- `--header`: Markdown string to inject at the top.
- `--confluence-url`: Confluence base URL (default: https://forhims.atlassian.net/wiki).
- `--copy-comments-from-version`: Version to copy comments from.
- `--scale-mermaid`: Scale factor for Mermaid diagrams.

**Environment Variables:**

Create a confluence token [from here](https://id.atlassian.com/manage-profile/security/api-tokens). Then
populate these environment variables:

- `CONFLUENCE_EMAIL`: Your Atlassian email.
- `CONFLUENCE_API_TOKEN`: Your Atlassian API token.

## Testing

```bash
uv run pytest
```
