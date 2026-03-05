import json
import os
import uuid
import pytest
import subprocess
import logging
import sys
from atlassian import Confluence

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "push_to_confluence.py")


def run_push(md_file, space, extra_args=None):
    """Run push_to_confluence.py and return the completed process."""
    cmd = [sys.executable, SCRIPT_PATH, str(md_file), "--space", space]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


class TestConfluenceIntegration:
    @pytest.fixture
    def page_title(self):
        return f"Integration Test Page {uuid.uuid4()}"

    @pytest.fixture
    def confluence(self):
        return Confluence(
            url="https://forhims.atlassian.net/wiki",
            username=os.environ.get("CONFLUENCE_EMAIL"),
            password=os.environ.get("CONFLUENCE_API_TOKEN"),
            cloud=True,
        )

    @pytest.fixture
    def space(self):
        return "MLE"

    @pytest.fixture
    def cleanup_page(self, confluence, space, page_title):
        yield
        # Teardown: Delete the page if it exists
        logger.info(f"Cleaning up page: {page_title}")
        page = confluence.get_page_by_title(space, page_title)
        if page:
            confluence.remove_page(page["id"], status=None, recursive=False)

    def _get_body(self, confluence, page_id):
        """Fetch the storage-format body of a page."""
        return confluence.get_page_by_id(
            page_id, expand="body.storage"
        )["body"]["storage"]["value"]

    def test_full_lifecycle(self, confluence, space, page_title, cleanup_page, tmp_path):
        """
        Tests the full lifecycle of a page:
        1. Create a new page from Markdown.
        2. Verify existence and header.
        3. Manually add a comment to the page via API.
        4. Update the page with new Markdown (simulating an edit).
        5. Verify the comment is preserved.
        """

        # --- Step 1: Create Initial Page ---
        logger.info("Step 1: Creating initial page...")

        md_file_v1 = tmp_path / "test_doc_v1.md"
        header_text = "**Integration Test Header**"
        md_file_v1.write_text(
            f"# {page_title}\n\nThis is the first version of the document.\n\n- Item A\n- Item B\n"
        )

        run_push(md_file_v1, space, ["--header", header_text])

        # Verify page exists
        page = confluence.get_page_by_title(space, page_title)
        assert page is not None, "Page was not created"
        page_id = page["id"]

        # Verify content includes header
        body = self._get_body(confluence, page_id)
        assert "Integration Test Header" in body, "Header not found in page content"
        assert "Item A" in body

        # --- Step 2: Inject Inline Comment ---
        logger.info("Step 2: Injecting inline comment...")

        comment_uuid = str(uuid.uuid4())
        marker = f'<ac:inline-comment-marker ac:ref="{comment_uuid}">Item A</ac:inline-comment-marker>'
        new_body_with_comment = body.replace("Item A", marker)

        confluence.update_page(
            page_id=page_id,
            title=page_title,
            body=new_body_with_comment,
            parent_id=None,
            type="page",
            representation="storage",
            minor_edit=True,
        )

        body_after_comment = self._get_body(confluence, page_id)
        assert "ac:inline-comment-marker" in body_after_comment

        # --- Step 3: Update Page (v2) ---
        logger.info("Step 3: Updating page with v2 content...")

        md_file_v2 = tmp_path / "test_doc_v2.md"
        md_file_v2.write_text(
            f"# {page_title}\n\n"
            "This is the SECOND version of the document.\n\n"
            "- Item A\n"
            "- Item B\n"
            "- Item C (New)\n"
        )

        run_push(md_file_v2, space, ["--header", header_text])

        # --- Step 4: Verify Preservation ---
        logger.info("Step 4: Verifying comment preservation...")

        final_body = self._get_body(confluence, page_id)

        assert "Item C (New)" in final_body, "New content not found"
        assert "ac:inline-comment-marker" in final_body, "Inline comment marker was lost!"
        assert comment_uuid in final_body, "Specific comment UUID was lost!"

        expected_marker_segment = f'<ac:inline-comment-marker ac:ref="{comment_uuid}">Item A</ac:inline-comment-marker>'
        assert expected_marker_segment in final_body, "Comment marker does not wrap the correct content!"

    def test_labels(self, confluence, space, page_title, cleanup_page, tmp_path):
        """
        Tests that --label flags apply Confluence labels to the page and that
        re-pushing with different labels adds to (not replaces) existing labels.
        """
        md_file = tmp_path / "label_test.md"
        md_file.write_text(f"# {page_title}\n\nLabel test content.\n")

        # --- Push with two labels ---
        logger.info("Pushing page with two labels...")
        run_push(md_file, space, ["--header", "test", "--label", "test-label-a", "--label", "test-label-b"])

        page = confluence.get_page_by_title(space, page_title)
        assert page is not None, "Page was not created"
        page_id = page["id"]

        labels = {l["name"] for l in confluence.get_page_labels(page_id)["results"]}
        assert "test-label-a" in labels, "Label 'test-label-a' not found"
        assert "test-label-b" in labels, "Label 'test-label-b' not found"

        # --- Re-push with a third label ---
        logger.info("Re-pushing with an additional label...")
        run_push(md_file, space, ["--header", "test", "--label", "test-label-c"])

        labels = {l["name"] for l in confluence.get_page_labels(page_id)["results"]}
        assert "test-label-a" in labels, "Previously applied label 'test-label-a' was lost"
        assert "test-label-c" in labels, "New label 'test-label-c' not found"

    def test_page_properties(self, confluence, space, page_title, cleanup_page, tmp_path):
        """
        Tests that --page-properties generates a Page Properties (details) macro
        in the page body, including plain text and status lozenge values.
        """
        md_file = tmp_path / "props_test.md"
        md_file.write_text(f"# {page_title}\n\nPage properties test content.\n")

        props = json.dumps([
            {"label": "Title", "value": "Test RFC"},
            {"label": "Status", "value": {"type": "status", "text": "DRAFT", "color": "yellow"}},
            {"label": "Squad", "value": "Platform"},
        ])

        run_push(md_file, space, ["--header", "test", "--page-properties", props])

        page = confluence.get_page_by_title(space, page_title)
        assert page is not None, "Page was not created"
        page_id = page["id"]

        body = self._get_body(confluence, page_id)

        # Verify the details macro (Page Properties) is present
        assert 'ac:name="details"' in body, "Page Properties macro not found"

        # Verify table structure with labels
        assert "<strong>Title</strong>" in body, "Title label not found in properties table"
        assert "Test RFC" in body, "Title value not found"
        assert "<strong>Squad</strong>" in body, "Squad label not found"
        assert "Platform" in body, "Squad value not found"

        # Verify status lozenge
        assert 'ac:name="status"' in body, "Status lozenge macro not found"
        assert "DRAFT" in body, "Status text not found"

        # Verify the actual page content follows the macro
        assert "Page properties test content" in body, "Page body content missing"

    def test_page_properties_from_file(self, confluence, space, page_title, cleanup_page, tmp_path):
        """
        Tests that --page-properties @file.json works identically to inline JSON.
        """
        md_file = tmp_path / "props_file_test.md"
        md_file.write_text(f"# {page_title}\n\nFile-based properties test.\n")

        props_file = tmp_path / "header.json"
        props_file.write_text(json.dumps([
            {"label": "RFC Type", "value": {"type": "status", "text": "GLOBAL", "color": "green"}},
            {"label": "Owner", "value": "Test User"},
        ]))

        run_push(md_file, space, ["--header", "test", "--page-properties", f"@{props_file}"])

        page = confluence.get_page_by_title(space, page_title)
        assert page is not None
        page_id = page["id"]

        body = self._get_body(confluence, page_id)
        assert 'ac:name="details"' in body, "Page Properties macro not found"
        assert "GLOBAL" in body, "Status text not found"
        assert "Test User" in body, "Owner value not found"

    def test_page_properties_survives_repush(self, confluence, space, page_title, cleanup_page, tmp_path):
        """
        Tests that the Page Properties macro is regenerated on re-push
        (not lost when the page body is overwritten).
        """
        md_file = tmp_path / "repush_test.md"
        props = json.dumps([
            {"label": "Status", "value": {"type": "status", "text": "DRAFT", "color": "yellow"}},
        ])

        # V1
        md_file.write_text(f"# {page_title}\n\nVersion 1 content.\n")
        run_push(md_file, space, ["--header", "test", "--page-properties", props])

        page = confluence.get_page_by_title(space, page_title)
        page_id = page["id"]
        body_v1 = self._get_body(confluence, page_id)
        assert "DRAFT" in body_v1

        # V2 — update the status to APPROVED
        props_v2 = json.dumps([
            {"label": "Status", "value": {"type": "status", "text": "APPROVED", "color": "blue"}},
        ])
        md_file.write_text(f"# {page_title}\n\nVersion 2 content.\n")
        run_push(md_file, space, ["--header", "test", "--page-properties", props_v2])

        body_v2 = self._get_body(confluence, page_id)
        assert "APPROVED" in body_v2, "Updated status not found after re-push"
        assert "Version 2 content" in body_v2, "Updated body not found after re-push"

    def test_mermaid_default_no_source(self, confluence, space, page_title, cleanup_page, tmp_path):
        """
        By default, mermaid diagrams are rendered to images but the source
        is NOT included in the page body.
        """
        md_file = tmp_path / "mermaid_test.md"
        mermaid_source = "graph TD\n    A[Start] --> B[End]"
        md_file.write_text(
            f"# {page_title}\n\n"
            f"```mermaid\n{mermaid_source}\n```\n\n"
            "Some text after the diagram.\n"
        )

        run_push(md_file, space, ["--header", "test"])

        page = confluence.get_page_by_title(space, page_title)
        assert page is not None, "Page was not created"
        page_id = page["id"]

        body = self._get_body(confluence, page_id)

        # Image macro present
        assert "ac:image" in body, "Image macro not found"

        # Source code block NOT present
        assert "graph TD" not in body, "Mermaid source should not be in body by default"

        # Regular content present
        assert "Some text after the diagram" in body

    def test_mermaid_source_included_with_flag(self, confluence, space, page_title, cleanup_page, tmp_path):
        """
        With --mermaid-source, the original diagram source is included as a
        collapsed code block after the rendered image.
        """
        md_file = tmp_path / "mermaid_source_test.md"
        mermaid_source = "graph TD\n    A[Start] --> B[End]"
        md_file.write_text(
            f"# {page_title}\n\n"
            f"```mermaid\n{mermaid_source}\n```\n\n"
            "Some text after the diagram.\n"
        )

        run_push(md_file, space, ["--header", "test", "--mermaid-source"])

        page = confluence.get_page_by_title(space, page_title)
        assert page is not None, "Page was not created"
        page_id = page["id"]

        body = self._get_body(confluence, page_id)

        # Image macro present
        assert "ac:image" in body, "Image macro not found"
        assert "ri:attachment" in body, "Attachment reference not found"

        # Source in a collapsed code block
        assert 'ac:name="code"' in body, "Code macro not found"
        assert "graph TD" in body, "Mermaid source not found in code block"
        assert "A[Start]" in body, "Mermaid node definition not found"

        # Regular content present
        assert "Some text after the diagram" in body

    def test_all_features_combined(self, confluence, space, page_title, cleanup_page, tmp_path):
        """
        Smoke test: push a page that uses labels, page-properties, and a header
        simultaneously to ensure they don't interfere with each other.
        """
        md_file = tmp_path / "combined_test.md"
        md_file.write_text(
            f"# {page_title}\n\n"
            "Combined feature test.\n\n"
            "- Item A\n"
            "- Item B\n"
        )

        props = json.dumps([
            {"label": "Title", "value": "Combined Test"},
            {"label": "Status", "value": {"type": "status", "text": "DRAFT", "color": "yellow"}},
        ])

        run_push(md_file, space, [
            "--label", "combined-test-label",
            "--page-properties", props,
            "--header", "**Auto-generated test page**",
        ])

        page = confluence.get_page_by_title(space, page_title)
        assert page is not None
        page_id = page["id"]

        body = self._get_body(confluence, page_id)

        # Page Properties macro present
        assert 'ac:name="details"' in body, "Page Properties macro missing"
        assert "Combined Test" in body
        assert "DRAFT" in body

        # Header present
        assert "Auto-generated test page" in body

        # Body content present
        assert "Item A" in body
        assert "Item B" in body

        # Labels applied
        labels = {l["name"] for l in confluence.get_page_labels(page_id)["results"]}
        assert "combined-test-label" in labels
