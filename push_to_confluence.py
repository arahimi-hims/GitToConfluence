#!/usr/bin/env python3
"""
Script to push Markdown to Confluence, handling image uploads and preserving inline comments.


It adds to it the ability to retain comments from the previous version of the
Confluence page.
"""

from typing import List, Optional, Tuple

import argparse
import os
import sys
import re
import subprocess
import shutil
import logging
import requests
import difflib
import uuid
import tempfile
from atlassian import Confluence
from bs4 import BeautifulSoup
from bs4.element import CData
from thefuzz import fuzz

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def run_command(
    command: List[str], input_text: Optional[str] = None, cwd: Optional[str] = None
) -> str:
    """Runs a shell command and returns stdout. Raises RuntimeError on failure."""
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if input_text else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )
        stdout, stderr = process.communicate(input=input_text)

        if process.returncode != 0:
            raise RuntimeError(f"Command failed: {command}\nError: {stderr}")

        return stdout
    except FileNotFoundError:
        logger.error(f"Command not found: {command[0]}")
        raise


def require_command(command_name: str, install_hint: str) -> None:
    """Exit with a helpful message if a required CLI tool is not available on PATH."""
    if shutil.which(command_name):
        return
    logger.error(f"Error: '{command_name}' is not installed.")
    logger.error(install_hint)
    sys.exit(1)


def require_confluence_auth_env() -> None:
    "Fail fast if Confluence credentials are missing."
    if os.environ.get("CONFLUENCE_EMAIL") and os.environ.get("CONFLUENCE_API_TOKEN"):
        return

    logger.error(
        "Confluence auth is required via CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN.\n"
        "You can create a Confluence API token by visiting:\n"
        "https://id.atlassian.com/manage-profile/security/api-tokens"
    )
    sys.exit(1)


def _git(*args: str, cwd: str) -> Optional[str]:
    """Best-effort git helper. Returns stripped stdout, or None if git fails."""
    try:
        out = run_command(["git", *args], cwd=cwd)
        return out.strip()
    except Exception:
        logger.debug("git command failed in %s: git %s", cwd, " ".join(args))
        raise


def get_markdown_url(markdown_file: str) -> Optional[str]:
    """
    Best-effort link to a file in git web UI, matching the logic in push_to_confluence.sh.
    Returns None if we can't determine repo/remote.
    """
    abs_file = os.path.abspath(markdown_file)
    file_dir = os.path.dirname(abs_file)

    repo_root = _git("rev-parse", "--show-toplevel", cwd=file_dir)
    if not repo_root:
        return None

    remote_url = _git("config", "--get", "remote.origin.url", cwd=repo_root)
    if not remote_url:
        return None

    branch = _git("branch", "--show-current", cwd=repo_root)
    if not branch:
        # Detached HEAD: fall back to commit SHA
        sha = _git("rev-parse", "HEAD", cwd=repo_root)
        branch = sha or "HEAD"

    # Convert SSH remote to HTTPS, strip trailing .git
    if remote_url.startswith("git@"):
        # git@github.com:org/repo.git -> https://github.com/org/repo
        remote_url = remote_url.replace(":", "/", 1).replace("git@", "https://", 1)
    if remote_url.endswith(".git"):
        remote_url = remote_url[:-4]

    try:
        rel_path = os.path.relpath(abs_file, repo_root)
    except ValueError:
        rel_path = os.path.basename(abs_file)

    return f"{remote_url}/blob/{branch}/{rel_path}"


def default_auto_header(markdown_file: str) -> str:
    url = get_markdown_url(markdown_file)
    return (
        "**Note:** _This page is automatically generated from [this source document]"
        f"({url}). Please do not edit directly._"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Push Markdown to Confluence.")
    parser.add_argument("file", help="Path to the Markdown file.")
    parser.add_argument("--space", required=True, help="Confluence space key.")
    # Historically this flag accepted a parent page title. We now use parent page id
    # (Confluence content id) to avoid ambiguity.
    parser.add_argument(
        "--parent-id",
        help="Parent Confluence page id (content id) or title. If omitted, page is created at the space root.",
    )
    parser.add_argument(
        "--header", help="Markdown string to inject at the top of the page."
    )
    parser.add_argument(
        "--confluence-url",
        default="https://forhims.atlassian.net/wiki",
        help="Confluence base URL.",
    )
    parser.add_argument(
        "--copy-comments-from-version",
        type=int,
        help="Specific version of the page to fetch for comment preservation. Defaults to the latest version.",
    )
    parser.add_argument(
        "--scale-mermaid",
        type=float,
        default=1.0,
        help="Scale factor for Mermaid diagrams (default: 1.0).",
    )
    parser.add_argument(
        "--puppeteer-config",
        default=os.environ.get("PUPPETEER_CONFIG")
        or os.environ.get("MERMAID_PUPPETEER_CONFIG")
        or os.path.join(os.path.expanduser("~"), "puppeteer-config.json"),
        help=(
            "Path to a Puppeteer config JSON file for mermaid-cli (mmdc -p). "
            "This may be necessary if mermaid-cli's bundled Chromium doesn't match your system "
            "and you need to point it at a local Chrome binary. "
            "Can also be set via PUPPETEER_CONFIG or MERMAID_PUPPETEER_CONFIG. "
            'Example config: {"executablePath": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}'
        ),
    )

    return parser.parse_args()


def convert_markdown_to_html(markdown_content: str) -> str:
    """Converts Markdown content to HTML using pandoc."""
    try:
        return run_command(
            ["pandoc", "--from=markdown-hard_line_breaks", "--to=html", "--wrap=none"],
            input_text=markdown_content,
        )
    except FileNotFoundError:
        logger.error("Pandoc not found. Please install pandoc.")
        raise


def convert_html_code_blocks_to_confluence_macros(html: str) -> str:
    """
    Confluence Cloud often sanitizes raw <pre><code> blocks in "storage" format.
    Convert them to Confluence's code macro to preserve newlines/formatting.
    """
    soup = BeautifulSoup(html, "html.parser")

    for pre in soup.find_all("pre"):
        code = pre.find("code")
        if not code:
            continue

        code_text = code.get_text()

        # Pandoc emits classes like: class="language-python" or class="python"
        lang = None
        classes = code.get("class") or []
        for c in classes:
            if c.startswith("language-"):
                lang = c.removeprefix("language-")
                break
        if not lang and classes:
            lang = classes[0]

        macro = soup.new_tag("ac:structured-macro", attrs={"ac:name": "code"})
        if lang:
            param = soup.new_tag("ac:parameter", attrs={"ac:name": "language"})
            param.string = lang
            macro.append(param)

        body = soup.new_tag("ac:plain-text-body")
        body.append(CData(code_text))
        macro.append(body)

        pre.replace_with(macro)

    return str(soup)


def normalize_confluence_list_spacing(html: str) -> str:
    """
    Pandoc emits "loose" lists as <li><p>...</p></li> when the markdown has blank
    lines between items. Confluence renders <p> with extra vertical spacing,
    causing visually separated list items.

    Convert simple <li><p>...</p></li> into <li>...</li> (and allow nested ul/ol)
    while keeping true multi-paragraph list items intact.
    """
    soup = BeautifulSoup(html, "html.parser")

    blockish = {"p", "div", "pre", "table", "blockquote", "hr"}

    def p_has_block_children(p_tag) -> bool:
        return p_tag.find(list(blockish | {"ul", "ol"})) is not None

    # 1) Remove/unwrap paragraph wrappers directly under list items to avoid Confluence
    # rendering paragraph margins between items.
    for li in soup.find_all("li"):
        direct_ps = li.find_all("p", recursive=False)
        for p in direct_ps:
            # Keep true multi-block content intact.
            if p_has_block_children(p):
                continue
            p.unwrap()

    # 2) Drop empty paragraphs that can show up as visual blank lines in Confluence.
    for p in soup.find_all("p"):
        txt = p.get_text().replace("\xa0", "").strip()
        if txt == "" and not p.find(True):
            p.decompose()

    return str(soup)


def tighten_markdown_lists(markdown: str) -> str:
    """
    Remove blank lines between consecutive list items so Pandoc emits "tight" lists.
    This avoids <li><p>..</p></li> output (which Confluence may render with extra spacing).

    Only operates outside fenced code blocks.
    """
    lines = markdown.splitlines()
    out: List[str] = []
    in_fence = False

    def is_list_item(line: str) -> bool:
        return bool(re.match(r"^\s*(?:\d+[.)]|[-*+])\s+\S", line))

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue

        if not in_fence and stripped == "":
            prev_line = lines[i - 1] if i > 0 else ""
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            if is_list_item(prev_line) and is_list_item(next_line):
                # Drop this blank line to tighten the list
                continue

        out.append(line)

    return "\n".join(out)


def ensure_blank_line_before_lists(markdown: str) -> str:
    """
    When using Pandoc with hard_line_breaks, a newline does NOT necessarily end a
    paragraph (it can become <br/>). That means ordered/bulleted lists can get
    "stuck" inside a paragraph and render like:
      <p><strong>Header</strong><br/>1. ...<br/>2. ...</p>

    Ensure list blocks start on a new block by inserting a blank line before the
    first list item when the previous line is non-blank and not already a list.

    Only operates outside fenced code blocks.
    """
    lines = markdown.splitlines()
    out: List[str] = []
    in_fence = False

    def is_list_item(line: str) -> bool:
        return bool(re.match(r"^\s*(?:\d+[.)]|[-*+])\s+\S", line))

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue

        if not in_fence and is_list_item(line):
            prev = out[-1] if out else ""
            if prev.strip() != "" and not is_list_item(prev):
                out.append("")

        out.append(line)

    return "\n".join(out)


def extract_title_and_body(markdown_content: str) -> Tuple[Optional[str], str]:
    """Extracts the first H1 as title and returns the rest of the body."""
    lines = iter(markdown_content.splitlines())
    title = None
    body_lines = []

    header_found = False

    for line in lines:
        if not header_found and line.strip().startswith("# "):
            title = line.strip()[2:].strip()
            header_found = True
        else:
            body_lines.append(line)

    return title, "\n".join(body_lines)


def ensure_page_exists(
    confluence, space: str, title: str, parent_id: Optional[str] = None
) -> str:
    """Ensures the page exists, creating it if necessary. Returns page_id."""
    page = confluence.get_page_by_title(space, title)
    if page:
        return page["id"]

    logger.info(f"Page '{title}' not found. Creating...")
    # Create with minimal content initially
    page = confluence.create_page(
        space=space, title=title, body="<p>Placeholder content</p>", parent_id=parent_id
    )
    return page["id"]


def process_mermaid_images(
    confluence,
    page_id: str,
    markdown_content: str,
    scale: float = 1.0,
    puppeteer_config: Optional[str] = None,
) -> str:
    """
    Finds mermaid code blocks, renders them to PNG using mmdc, uploads them,
    and replaces the blocks with Confluence image macros.
    """
    mermaid_pattern = re.compile(r"```mermaid\s*\n(.*?)\n\s*```", re.DOTALL)

    def replace_with_image(match: re.Match) -> str:
        mermaid_content = match.group(1)
        # Unique filename
        filename = f"mermaid-{uuid.uuid4()}.png"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".mmd", delete=False
        ) as tmp_mmd:
            tmp_mmd.write(mermaid_content)
            mmd_path = tmp_mmd.name

        png_path = mmd_path.replace(".mmd", ".png")

        try:
            # 1. Render to PNG
            cmd = ["mmdc", "-i", mmd_path, "-o", png_path, "-b", "white"]
            if puppeteer_config:
                if os.path.exists(puppeteer_config):
                    cmd.extend(["-p", puppeteer_config])
                else:
                    logger.warning(
                        f"Puppeteer config not found at {puppeteer_config}; running mmdc without -p"
                    )
            if scale != 1.0:
                cmd.extend(["--scale", str(scale)])

            run_command(cmd)

            # 2. Upload to Confluence
            logger.info(f"Uploading mermaid diagram: {filename}")
            confluence.attach_file(png_path, name=filename, page_id=page_id)

            # 3. Return Image Macro
            return f'<ac:image ac:width="100%"><ri:attachment ri:filename="{filename}" /></ac:image>'

        except (RuntimeError, FileNotFoundError):
            logger.exception(
                "Failed to render Mermaid diagram. Please ensure mermaid-cli is installed "
                "(npm install -g @mermaid-js/mermaid-cli). If you see a Chromium/Chrome mismatch, "
                "try providing --puppeteer-config (or PUPPETEER_CONFIG) with e.g. "
                '{"executablePath": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}.'
            )
            raise
        finally:
            if os.path.exists(mmd_path):
                os.remove(mmd_path)
            if os.path.exists(png_path):
                os.remove(png_path)

    return mermaid_pattern.sub(replace_with_image, markdown_content)


def process_images(
    confluence, page_id: str, markdown_content: str, base_path: str
) -> str:
    """
    Finds images in markdown, uploads them to Confluence, and replaces
    the markdown links with Confluence storage format macros.
    """
    # Regex for standard markdown images
    # Matches: ![Alt Text](image.png ac:width="500" ac:height="300")
    #            ^^^^^^^^  ^^^^^^^^  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    #            Group 1    Group 2    Group 3
    # Matches ![Alt](img.png width="500")
    image_pattern = re.compile(r"!\[(.*?)\]\((\S+)(?:\s+(.*?))?\)")

    def upload_and_replace(match: re.Match) -> str:
        image_src = match.group(2)
        attributes = match.group(3) or ""

        # Check if it's a local file
        if image_src.startswith("http") or image_src.startswith("//"):
            return match.group(0)  # Skip remote images

        image_path = os.path.join(base_path, image_src)
        if not os.path.exists(image_path):
            logger.warning(f"Image not found: {image_path}")
            return match.group(0)

        filename = os.path.basename(image_path)

        logger.info(f"Uploading image: {filename}")
        confluence.attach_file(image_path, name=filename, page_id=page_id)

        # Confluence storage format for image
        return f'<ac:image {attributes}><ri:attachment ri:filename="{filename}" /></ac:image>'

    return image_pattern.sub(upload_and_replace, markdown_content)


# This function is 100% vibe-coded. I gave English requirements and guided the
# unit tests.  I asked Gemini to iterate until the tests pass. There be dragons
# in this function.
def preserve_comments(old_html: str, new_html: str) -> str:
    """
    Extracts inline comments from old_html and attempts to re-apply them to new_html
    using fuzzy matching.
    """
    soup_old = BeautifulSoup(old_html, "html.parser")
    soup_new = BeautifulSoup(new_html, "html.parser")

    # Find all inline comment markers
    markers = soup_old.find_all("ac:inline-comment-marker")

    if not markers:
        logger.info("No inline comments found in previous version.")
        return new_html

    logger.info(f"Found {len(markers)} inline comments. Attempting to preserve...")

    for marker in markers:
        ref = marker.get("ac:ref")
        original_text = marker.get_text()

        if not original_text.strip():
            continue

        # Strategy: Search for this text in the new HTML's text nodes
        text_nodes = [
            t
            for t in soup_new.find_all(string=True)
            if t.parent.name not in ["script", "style"]
        ]

        best_match_node = None
        best_match_score = 0
        best_match_start = -1
        match_type = "none"

        # 1. Try Context Match (at least 4 words surrounding)
        context_before = ""
        if marker.previous_sibling and isinstance(marker.previous_sibling, str):
            # Capture last 4 words
            m = re.search(r"((?:\S+\s*){1,4})$", marker.previous_sibling)
            if m:
                context_before = m.group(1)

        context_after = ""
        if marker.next_sibling and isinstance(marker.next_sibling, str):
            # Capture first 4 words
            m = re.search(r"^((?:\s*\S+){1,4})", marker.next_sibling)
            if m:
                context_after = m.group(1)

        search_text_context = context_before + original_text + context_after
        use_context = len(search_text_context) > len(original_text)

        if use_context:
            for node in text_nodes:
                if search_text_context in node:
                    best_match_node = node
                    best_match_score = 100
                    best_match_start = node.find(search_text_context)
                    match_type = "context"
                    break

        # 2. Exact match fallback
        if not best_match_node:
            for node in text_nodes:
                if original_text in node:
                    best_match_node = node
                    best_match_score = 100
                    best_match_start = node.find(original_text)
                    match_type = "exact"
                    break

        # 3. Fuzzy match fallback
        if not best_match_node:
            for node in text_nodes:
                score = fuzz.partial_ratio(original_text, str(node))
                if score > 85 and score > best_match_score:
                    best_match_node = node
                    best_match_score = score
                    match_type = "fuzzy"

        if best_match_node:
            logger.info(
                f"Restoring comment {ref} (Type: {match_type}, Score: {best_match_score})"
            )

            if match_type in ["context", "exact", "fuzzy"]:
                new_marker = soup_new.new_tag(
                    "ac:inline-comment-marker", attrs={"ac:ref": ref}
                )

                node_text = str(best_match_node)
                start_index = -1
                end_index = -1

                if match_type == "context":
                    # Offset by the length of the context before the target
                    start_index = best_match_start + len(context_before)
                    end_index = start_index + len(original_text)
                    new_marker.string = original_text
                elif match_type == "exact":
                    start_index = best_match_start
                    end_index = start_index + len(original_text)
                    new_marker.string = original_text
                elif match_type == "fuzzy":
                    # Use difflib to find the extent of the match in the target node
                    sm = difflib.SequenceMatcher(None, original_text, node_text)
                    blocks = [b for b in sm.get_matching_blocks() if b.size > 0]

                    if blocks:
                        # Find the span covering all matching blocks
                        start_index = min(b.b for b in blocks)
                        end_index = max(b.b + b.size for b in blocks)

                        # Extract the text from the node that corresponds to the match
                        target_text = node_text[start_index:end_index]
                        new_marker.string = target_text

                        logger.info(
                            f"Fuzzy match replaced: '{original_text}' -> '{target_text}'"
                        )
                    else:
                        logger.warning(
                            f"Fuzzy match found but no matching blocks for ref {ref}"
                        )
                        continue

                before = node_text[:start_index]
                after = node_text[end_index:]

                replacement = []
                if before:
                    replacement.append(before)
                replacement.append(new_marker)
                if after:
                    replacement.append(after)

                best_match_node.replace_with(*replacement)
            else:
                logger.warning(
                    f"Could not decide on replacement for {ref} (Type: {match_type})"
                )
        else:
            logger.warning(
                f"Could not find location for comment {ref} on text: '{original_text[:20]}...'"
            )

    return str(soup_new)


def resolve_parent_page(
    confluence, space: str, parent_identifier: Optional[str]
) -> Optional[str]:
    """
    Resolves a parent page identifier (ID or Title) to a Page ID.
    If it's digits, assumes ID. Otherwise, looks up by title in the space.
    """
    if not parent_identifier:
        return None

    parent_identifier = str(parent_identifier).strip()

    if parent_identifier.isdigit():
        return parent_identifier

    # It's a title, look it up
    logger.info(f"Resolving parent page by title: '{parent_identifier}'...")
    page = confluence.get_page_by_title(space, parent_identifier)
    if not page:
        logger.error(
            f"Parent page not found by title: '{parent_identifier}' in space '{space}'"
        )
        sys.exit(1)

    logger.info(f"Resolved parent page by title: page_id={page['id']}")
    return page["id"]


def main() -> None:
    args = parse_args()

    # Fail fast on missing Confluence credentials before doing any work.
    require_confluence_auth_env()

    try:
        with open(args.file, "r") as f:
            content = f.read()
    except FileNotFoundError:
        logger.error(f"Markdown file not found: {args.file}")
        sys.exit(1)

    # External tool checks, done early to fail fast.
    require_command("pandoc", "Please install it via brew install pandoc")

    # 1. Markdown Processing
    title, body_content = extract_title_and_body(content)

    if not title:
        logger.error("Could not find H1 title in the markdown file.")
        sys.exit(1)

    logger.info(f"Page Title: {title}")

    # 2. Header Injection
    # Inject the user-provided header if present, otherwise inject the auto-generated note header.
    header = args.header or default_auto_header(args.file)
    body_content = f"{header}\n\n{body_content}"

    # With hard_line_breaks enabled, lists can accidentally become paragraph text unless
    # they are preceded by a blank line.
    body_content = ensure_blank_line_before_lists(body_content)

    # Tighten lists before Pandoc conversion to avoid loose-list spacing in Confluence.
    body_content = tighten_markdown_lists(body_content)

    # If Mermaid blocks exist, ensure mermaid-cli is installed before we do any API work.
    if re.search(r"```mermaid\s*\n(.*?)\n\s*```", body_content, flags=re.DOTALL):
        require_command(
            "mmdc", "Please install it via npm install -g @mermaid-js/mermaid-cli"
        )

    # Connect to Confluence
    confluence = Confluence(
        url=args.confluence_url,
        username=os.environ.get("CONFLUENCE_EMAIL"),
        password=os.environ.get("CONFLUENCE_API_TOKEN"),
        cloud=True,
    )

    # Parent page handling (id or title)
    parent_id = resolve_parent_page(confluence, args.space, args.parent_id)

    # Ensure page exists to get ID for attachments
    page_id = ensure_page_exists(confluence, args.space, title, parent_id)

    # 3. Image Handling
    base_path = os.path.dirname(os.path.abspath(args.file))
    body_content = process_images(confluence, page_id, body_content, base_path)
    body_content = process_mermaid_images(
        confluence,
        page_id,
        body_content,
        scale=args.scale_mermaid,
        puppeteer_config=args.puppeteer_config,
    )

    # 4. HTML Conversion
    try:
        html_content = convert_markdown_to_html(body_content)
    except (RuntimeError, FileNotFoundError):
        logger.exception("HTML conversion failed")
        sys.exit(1)

    # 5. Comment Preservation
    # Fetch existing content to get comments
    # We always fetch to be safe, even if we just created it (it will be empty/placeholder)
    status = "historical" if args.copy_comments_from_version else None

    logger.info(
        f"Fetching version {args.copy_comments_from_version if args.copy_comments_from_version else 'latest'} for comment preservation..."
    )

    current_page = confluence.get_page_by_id(
        page_id,
        status=status,
        version=args.copy_comments_from_version,
        expand="body.storage",
    )
    current_body = current_page.get("body", {}).get("storage", {}).get("value", "")

    html_content = convert_html_code_blocks_to_confluence_macros(html_content)
    html_content = normalize_confluence_list_spacing(html_content)
    html_content = preserve_comments(current_body, html_content)
    # preserve_comments() reparses HTML; run list normalization again defensively.
    final_html = normalize_confluence_list_spacing(html_content)

    # 6. Page Update
    logger.info(f"Updating page {page_id}...")
    try:
        result = confluence.update_page(
            page_id=page_id,
            title=title,
            body=final_html,
            parent_id=parent_id,
            type="page",
            representation="storage",
            minor_edit=False,
        )

    except requests.exceptions.RequestException:
        logger.exception("Failed to update page")
        sys.exit(1)

    base_url = result["_links"].get("base", args.confluence_url).rstrip("/")
    webui = result["_links"]["webui"]
    full_url = f"{base_url}{webui}"
    logger.info(f"Page URL: {full_url}")


if __name__ == "__main__":
    main()
