import logging
from unittest.mock import MagicMock, patch
from push_to_confluence import extract_title_and_body, preserve_comments, process_images


class TestExtractTitleAndBody:
    def test_extract_title_simple(self):
        content = """# My Title
Body line 1
Body line 2"""
        title, body = extract_title_and_body(content)
        assert title == "My Title"
        assert body == "Body line 1\nBody line 2"

    def test_extract_title_with_single_preceding_line(self):
        content = """Preceding line
# The Title
Body content"""
        title, body = extract_title_and_body(content)
        assert title == "The Title"
        assert body == "Preceding line\nBody content"

    def test_extract_title_with_multiple_preceding_lines(self):
        content = """Line 1
Line 2
Line 3
# The Title
Body content"""
        title, body = extract_title_and_body(content)
        assert title == "The Title"
        assert body == "Line 1\nLine 2\nLine 3\nBody content"

    def test_extract_title_with_preceding_empty_lines(self):
        content = """
Line 1

# The Title
Body content"""
        title, body = extract_title_and_body(content)
        assert title == "The Title"
        assert body == "\nLine 1\n\nBody content"

    def test_no_title(self):
        content = """Line 1
Line 2"""
        title, body = extract_title_and_body(content)
        assert title is None
        assert body == "Line 1\nLine 2"

    def test_empty_content(self):
        content = ""
        title, body = extract_title_and_body(content)
        assert title is None
        assert body == ""

    def test_multiple_headers(self):
        content = """# First Title
Body 1
# Second Header (kept in body)"""
        title, body = extract_title_and_body(content)
        assert title == "First Title"
        assert body == "Body 1\n# Second Header (kept in body)"


class TestPreserveComments:
    def test_no_comments_in_old_html(self):
        old_html = "<p>Old content</p>"
        new_html = "<p>New content</p>"
        result = preserve_comments(old_html, new_html)
        assert result == new_html

    def test_exact_match_preservation(self):
        # Marker with simple text
        old_html = '<p>Start <ac:inline-comment-marker ac:ref="ref-1">commented text</ac:inline-comment-marker> End</p>'
        new_html = "<p>Start commented text End</p>"

        result = preserve_comments(old_html, new_html)

        # Check if marker is present
        assert "ac:inline-comment-marker" in result
        assert 'ac:ref="ref-1"' in result
        assert ">commented text<" in result

    def test_context_match_preservation(self):
        # Text is same, but appears multiple times. Context helps disambiguate?
        # The logic tries context match first.

        old_html = '<p>Context before <ac:inline-comment-marker ac:ref="ref-2">target</ac:inline-comment-marker> Context after</p>'
        # In new HTML, we have the same structure
        new_html = "<p>Context before target Context after</p>"

        result = preserve_comments(old_html, new_html)

        assert 'ac:ref="ref-2"' in result
        assert ">target<" in result

    def test_fuzzy_match_preservation(self, caplog):
        # Logic update: fuzzy matches should now be preserved using best-effort matching.

        old_html = '<p><ac:inline-comment-marker ac:ref="ref-3">original text</ac:inline-comment-marker></p>'
        # new text is slightly different
        new_html = "<p>originall text</p>"

        with caplog.at_level(logging.WARNING):
            result = preserve_comments(old_html, new_html)

        # Should have the marker
        assert "ac:inline-comment-marker" in result
        assert 'ac:ref="ref-3"' in result
        # The text inside might vary depending on how we match, but "originall text" should be wrapped
        assert ">originall text<" in result

        # Should not log the "not implemented" warning
        assert not any(
            "partial replacement not implemented yet" in r.message
            for r in caplog.records
        )

    def test_no_match_found(self, caplog):
        old_html = '<p><ac:inline-comment-marker ac:ref="ref-4">gone text</ac:inline-comment-marker></p>'
        new_html = "<p>completely different content</p>"

        with caplog.at_level(logging.WARNING):
            result = preserve_comments(old_html, new_html)

        assert result == new_html
        assert any(
            "Could not find location for comment" in r.message for r in caplog.records
        )

    def test_empty_marker_text(self):
        old_html = '<p><ac:inline-comment-marker ac:ref="ref-5">   </ac:inline-comment-marker></p>'
        new_html = "<p>content</p>"
        result = preserve_comments(old_html, new_html)
        assert result == new_html

    def test_comment_preservation_complex_structure(self):
        # Test nested structure
        old_html = '<div><p>Para 1 <ac:inline-comment-marker ac:ref="ref-6">nested comment</ac:inline-comment-marker></p></div>'
        new_html = "<div><p>Para 1 nested comment</p></div>"

        result = preserve_comments(old_html, new_html)
        assert 'ac:ref="ref-6"' in result
        assert ">nested comment<" in result

    def test_context_disambiguation_middle_match(self):
        # Scenario: X ... X(commented) ... X
        # The middle occurrence should be the one receiving the comment in the output.

        # "common" appears 3 times. The middle one is commented.
        # Context is "prefix " and " suffix".
        old_html = '<p>common prefix <ac:inline-comment-marker ac:ref="ref-ctx">common</ac:inline-comment-marker> suffix common</p>'
        new_html = "<p>common prefix common suffix common</p>"

        result = preserve_comments(old_html, new_html)

        # Verify that the comment is applied to the middle 'common'
        # We expect: ...common prefix <marker>common</marker> suffix common...

        # Check that we have the sequence: prefix <marker>
        assert 'prefix <ac:inline-comment-marker ac:ref="ref-ctx">' in result
        # Check that we have the sequence: </marker> suffix
        assert "</ac:inline-comment-marker> suffix" in result


class TestProcessImages:
    def test_process_images_with_attributes(self):
        confluence = MagicMock()
        page_id = "12345"
        base_path = "/tmp"
        markdown = '![Alt](image.png ac:width="400")'

        with patch("os.path.exists", return_value=True):
            result = process_images(confluence, page_id, markdown, base_path)

        assert (
            '<ac:image ac:width="400"><ri:attachment ri:filename="image.png" /></ac:image>'
            in result
        )
        confluence.attach_file.assert_called_once()

    def test_process_images_no_title(self):
        confluence = MagicMock()
        page_id = "12345"
        base_path = "/tmp"
        markdown = "![Alt](image.png)"

        with patch("os.path.exists", return_value=True):
            result = process_images(confluence, page_id, markdown, base_path)

        assert (
            '<ac:image ><ri:attachment ri:filename="image.png" /></ac:image>' in result
        )
        confluence.attach_file.assert_called_once()
