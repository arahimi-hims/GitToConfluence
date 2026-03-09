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
    """Run push_to_confluence.py as a subprocess and assert success."""
    cmd = [
        sys.executable,
        SCRIPT_PATH,
        str(md_file),
        "--space", space,
    ]
    if extra_args:
        cmd.extend(extra_args)
    subprocess.run(cmd, check=True, capture_output=True)


class TestConfluenceIntegration:
    @pytest.fixture
    def page_title(self):
        return f"Integration Test Page {uuid.uuid4()}"

    @pytest.fixture
    def confluence(self):
        return Confluence(
            url= "https://forhims.atlassian.net/wiki",
            username=os.environ.get("CONFLUENCE_EMAIL"),
            password=os.environ.get("CONFLUENCE_API_TOKEN"),
            cloud=True
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
        """Return the storage-format body HTML for a page."""
        return confluence.get_page_by_id(page_id, expand="body.storage")["body"]["storage"]["value"]

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
        md_file_v1.write_text(f"# {page_title}\n\nThis is the first version of the document.\n\n- Item A\n- Item B\n")

        # Run push_to_confluence.py
        script_path = os.path.join(os.path.dirname(__file__), "push_to_confluence.py")
        subprocess.run(
            [
                sys.executable,
                script_path,
                str(md_file_v1),
                "--space", space,
                "--header", header_text,
            ],
            check=True,
            capture_output=True
        )

        # Verify page exists
        page = confluence.get_page_by_title(space, page_title)
        assert page is not None, "Page was not created"
        page_id = page["id"]
        
        # Verify content includes header
        body = confluence.get_page_by_id(page_id, expand="body.storage")["body"]["storage"]["value"]
        assert "Integration Test Header" in body, "Header not found in page content"
        assert "Item A" in body
        
        # --- Step 2: Inject Inline Comment ---
        logger.info("Step 2: Injecting inline comment...")
        
        # Inline comments in Confluence storage format wrap text with
        #  <ac:inline-comment-marker ac:ref="uuid">text</ac:inline-comment-marker>
        comment_uuid = str(uuid.uuid4())
        marker = f'<ac:inline-comment-marker ac:ref="{comment_uuid}">Item A</ac:inline-comment-marker>'
        
        # Simple string replacement to inject the marker
        new_body_with_comment = body.replace("Item A", marker)
        
        confluence.update_page(
            page_id=page_id,
            title=page_title,
            body=new_body_with_comment,
            parent_id=None,
            type="page",
            representation="storage",
            minor_edit=True
        )
        
        # Verify comment marker is there
        body_after_comment = confluence.get_page_by_id(page_id, expand="body.storage")["body"]["storage"]["value"]
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
        
        # Run push_to_confluence.py again
        subprocess.run(
            [
                sys.executable,
                script_path,
                str(md_file_v2),
                "--space", space,
                "--header", header_text,
            ],
            check=True,
            capture_output=True
        )
        
        # --- Step 4: Verify Preservation ---
        logger.info("Step 4: Verifying comment preservation...")
        
        final_body = confluence.get_page_by_id(page_id, expand="body.storage")["body"]["storage"]["value"]
        
        assert "Item C (New)" in final_body, "New content not found"
        assert "ac:inline-comment-marker" in final_body, "Inline comment marker was lost!"
        assert comment_uuid in final_body, "Specific comment UUID was lost!"
        
        # Ensure the marker wraps "Item A"
        expected_marker_segment = f'<ac:inline-comment-marker ac:ref="{comment_uuid}">Item A</ac:inline-comment-marker>'
        assert expected_marker_segment in final_body, "Comment marker does not wrap the correct content!"

    def test_labels(self, confluence, space, page_title, cleanup_page, tmp_path):
        """
        Tests that --label applies Confluence labels and that labels are
        additive across re-pushes (existing labels are not removed).
        """
        md_file = tmp_path / "test_labels.md"
        md_file.write_text(f"# {page_title}\n\nLabel test content.\n")

        # --- Push with two labels ---
        logger.info("Pushing with --label test-label-a --label test-label-b")
        run_push(md_file, space, extra_args=[
            "--header", "test",
            "--label", "test-label-a",
            "--label", "test-label-b",
        ])

        page = confluence.get_page_by_title(space, page_title)
        assert page is not None, "Page was not created"
        page_id = page["id"]

        labels = confluence.get_page_labels(page_id)
        label_names = {l["name"] for l in labels.get("results", [])}
        assert "test-label-a" in label_names, f"test-label-a missing from {label_names}"
        assert "test-label-b" in label_names, f"test-label-b missing from {label_names}"

        # --- Re-push with a third label only ---
        logger.info("Re-pushing with --label test-label-c")
        run_push(md_file, space, extra_args=[
            "--header", "test",
            "--label", "test-label-c",
        ])

        labels = confluence.get_page_labels(page_id)
        label_names = {l["name"] for l in labels.get("results", [])}
        assert "test-label-a" in label_names, f"test-label-a lost after re-push: {label_names}"
        assert "test-label-b" in label_names, f"test-label-b lost after re-push: {label_names}"
        assert "test-label-c" in label_names, f"test-label-c missing after re-push: {label_names}"
