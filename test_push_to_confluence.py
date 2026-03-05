import json
import logging
import tempfile
import os
from unittest.mock import MagicMock, patch
from push_to_confluence import (
    build_page_properties_html,
    convert_heading_ids_to_confluence_anchors,
    extract_title_and_body,
    normalize_table_widths,
    parse_page_properties_arg,
    preserve_comments,
    process_images,
    process_mermaid_images,
    _render_cell_value,
)


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


class TestNormalizeTableWidths:
    def test_removes_table_style_width(self):
        html = '<table style="width:100%;"><tr><td>data</td></tr></table>'
        result = normalize_table_widths(html)
        assert 'style=' not in result
        assert "<table>" in result
        assert "<td>data</td>" in result

    def test_removes_table_style_width_with_decimal(self):
        html = '<table style="width:75.5%;"><tr><td>data</td></tr></table>'
        result = normalize_table_widths(html)
        assert 'style=' not in result

    def test_removes_table_style_width_without_semicolon(self):
        html = '<table style="width:100%"><tr><td>data</td></tr></table>'
        result = normalize_table_widths(html)
        assert 'style=' not in result

    def test_removes_colgroup(self):
        html = (
            "<table>"
            '<colgroup><col style="width: 50%" /><col style="width: 50%" /></colgroup>'
            "<tr><td>a</td><td>b</td></tr></table>"
        )
        result = normalize_table_widths(html)
        assert "<colgroup>" not in result
        assert "<col " not in result
        assert "<td>a</td>" in result

    def test_removes_multiline_colgroup(self):
        html = (
            "<table>\n"
            "<colgroup>\n"
            '<col style="width: 33%" />\n'
            '<col style="width: 33%" />\n'
            '<col style="width: 34%" />\n'
            "</colgroup>\n"
            "<tr><td>a</td><td>b</td><td>c</td></tr></table>"
        )
        result = normalize_table_widths(html)
        assert "<colgroup>" not in result
        assert "</colgroup>" not in result

    def test_removes_both_style_and_colgroup(self):
        html = (
            '<table style="width:100%;">'
            '<colgroup><col style="width: 50%" /><col style="width: 50%" /></colgroup>'
            "<tr><td>a</td><td>b</td></tr></table>"
        )
        result = normalize_table_widths(html)
        assert 'style=' not in result
        assert "<colgroup>" not in result

    def test_preserves_other_table_attributes(self):
        html = '<table class="my-table" style="width:100%;"><tr><td>data</td></tr></table>'
        result = normalize_table_widths(html)
        assert 'class="my-table"' in result
        assert 'style=' not in result

    def test_no_table_passes_through(self):
        html = "<p>No tables here</p>"
        result = normalize_table_widths(html)
        assert result == html

    def test_table_without_width_unchanged(self):
        html = '<table class="plain"><tr><td>data</td></tr></table>'
        result = normalize_table_widths(html)
        assert result == html

    def test_multiple_tables(self):
        html = (
            '<table style="width:100%;"><tr><td>1</td></tr></table>'
            "<p>gap</p>"
            '<table style="width:50%;"><colgroup><col style="width:100%" /></colgroup>'
            "<tr><td>2</td></tr></table>"
        )
        result = normalize_table_widths(html)
        assert result.count("style=") == 0
        assert "<colgroup>" not in result
        assert "<td>1</td>" in result
        assert "<td>2</td>" in result


class TestConvertHeadingIdsToConfluenceAnchors:
    def test_heading_id_converted_to_anchor_macro(self):
        html = '<h2 id="my-section">My Section</h2>'
        result = convert_heading_ids_to_confluence_anchors(html)
        assert '<ac:structured-macro ac:name="anchor" ac:schema-version="1">' in result
        assert '<ac:parameter ac:name="">my-section</ac:parameter>' in result
        assert "My Section</h2>" in result

    def test_multiple_heading_levels(self):
        html = (
            '<h1 id="top">Top</h1>'
            '<h2 id="mid">Mid</h2>'
            '<h3 id="low">Low</h3>'
        )
        result = convert_heading_ids_to_confluence_anchors(html)
        assert 'ac:name="">top</ac:parameter>' in result
        assert 'ac:name="">mid</ac:parameter>' in result
        assert 'ac:name="">low</ac:parameter>' in result

    def test_fragment_link_rewritten_to_ac_link(self):
        html = (
            '<h2 id="target-section">Target Section</h2>'
            '<p>See <a href="#target-section">link text</a></p>'
        )
        result = convert_heading_ids_to_confluence_anchors(html)
        assert '<ac:link ac:anchor="target-section">' in result
        assert "<![CDATA[link text]]>" in result
        assert '<a href="#target-section">' not in result

    def test_fragment_link_without_heading_left_alone(self):
        html = '<p>See <a href="#unknown-section">link text</a></p>'
        result = convert_heading_ids_to_confluence_anchors(html)
        assert '<a href="#unknown-section">link text</a>' in result
        assert "ac:link" not in result

    def test_external_links_not_affected(self):
        html = (
            '<h2 id="sec">Section</h2>'
            '<p><a href="https://example.com">external</a></p>'
        )
        result = convert_heading_ids_to_confluence_anchors(html)
        assert '<a href="https://example.com">external</a>' in result

    def test_multiple_links_to_same_anchor(self):
        html = (
            '<h2 id="faq">FAQ</h2>'
            '<p><a href="#faq">link1</a> and <a href="#faq">link2</a></p>'
        )
        result = convert_heading_ids_to_confluence_anchors(html)
        assert result.count('ac:anchor="faq"') == 2
        assert "<![CDATA[link1]]>" in result
        assert "<![CDATA[link2]]>" in result

    def test_heading_without_id_unchanged(self):
        html = "<h2>No ID Here</h2>"
        result = convert_heading_ids_to_confluence_anchors(html)
        assert result == html

    def test_heading_with_extra_attributes(self):
        html = '<h2 id="slug" class="special">Title</h2>'
        result = convert_heading_ids_to_confluence_anchors(html)
        assert 'ac:name="">slug</ac:parameter>' in result
        assert "Title</h2>" in result

    def test_anchor_placed_inside_heading(self):
        """The anchor macro should be nested inside the heading tag, not before it."""
        html = '<h3 id="intro">Introduction</h3>'
        result = convert_heading_ids_to_confluence_anchors(html)
        # Anchor macro should come after the opening <h3> tag and before the content
        assert result.startswith("<h3>")
        assert "<h3><ac:structured-macro" in result
        assert "Introduction</h3>" in result

    def test_mixed_content_end_to_end(self):
        """Full scenario: headings with ids, fragment links, and regular content."""
        html = (
            "<p>Intro paragraph</p>"
            '<h2 id="setup">Setup</h2>'
            "<p>Setup instructions</p>"
            '<h2 id="usage">Usage</h2>'
            '<p>See <a href="#setup">setup</a> for details.</p>'
            '<p>Visit <a href="https://example.com">docs</a></p>'
            '<p>Unknown <a href="#appendix">appendix ref</a></p>'
        )
        result = convert_heading_ids_to_confluence_anchors(html)
        # Anchor macros inserted for both headings
        assert 'ac:name="">setup</ac:parameter>' in result
        assert 'ac:name="">usage</ac:parameter>' in result
        # Fragment link to #setup rewritten
        assert '<ac:link ac:anchor="setup">' in result
        assert "<![CDATA[setup]]>" in result
        # External link untouched
        assert '<a href="https://example.com">docs</a>' in result
        # Unknown fragment link untouched
        assert '<a href="#appendix">appendix ref</a>' in result

    def test_empty_html(self):
        result = convert_heading_ids_to_confluence_anchors("")
        assert result == ""

    def test_no_headings_no_links(self):
        html = "<p>Just a paragraph</p>"
        result = convert_heading_ids_to_confluence_anchors(html)
        assert result == html


class TestRenderCellValue:
    def test_plain_string(self):
        result = _render_cell_value("Weight Loss")
        assert result == "Weight Loss"

    def test_plain_string_html_escaped(self):
        result = _render_cell_value("A <b>bold</b> & 'quoted' value")
        assert "&lt;b&gt;" in result
        assert "&amp;" in result

    def test_status_lozenge(self):
        result = _render_cell_value({"type": "status", "text": "DRAFT", "color": "yellow"})
        assert 'ac:name="details"' not in result
        assert 'ac:name="status"' in result
        assert 'ac:name="colour">Yellow</ac:parameter>' in result
        assert 'ac:name="title">DRAFT</ac:parameter>' in result

    def test_status_lozenge_color_capitalized(self):
        result = _render_cell_value({"type": "status", "text": "APPROVED", "color": "blue"})
        assert 'ac:name="colour">Blue</ac:parameter>' in result

    def test_status_lozenge_default_color(self):
        result = _render_cell_value({"type": "status", "text": "UNKNOWN"})
        assert 'ac:name="colour">Grey</ac:parameter>' in result

    def test_unknown_type_falls_back_to_string(self):
        result = _render_cell_value({"type": "unknown_widget", "foo": "bar"})
        assert "unknown_widget" in result


class TestBuildPagePropertiesHtml:
    def test_basic_text_rows(self):
        rows = [
            {"label": "Title", "value": "My RFC"},
            {"label": "Squad", "value": "Weight Loss"},
        ]
        result = build_page_properties_html(rows)
        assert 'ac:name="details"' in result
        assert "<ac:rich-text-body>" in result
        assert "<strong>Title</strong>" in result
        assert "My RFC" in result
        assert "<strong>Squad</strong>" in result
        assert "Weight Loss" in result

    def test_status_lozenge_row(self):
        rows = [
            {"label": "Status", "value": {"type": "status", "text": "APPROVED", "color": "blue"}},
        ]
        result = build_page_properties_html(rows)
        assert 'ac:name="details"' in result
        assert "<strong>Status</strong>" in result
        assert 'ac:name="status"' in result
        assert 'ac:name="title">APPROVED</ac:parameter>' in result

    def test_mixed_rows(self):
        rows = [
            {"label": "Title", "value": "My RFC"},
            {"label": "RFC Type", "value": {"type": "status", "text": "GLOBAL", "color": "green"}},
            {"label": "Status", "value": {"type": "status", "text": "DRAFT", "color": "yellow"}},
            {"label": "Squad", "value": "Weight Loss"},
            {"label": "Notes", "value": ""},
        ]
        result = build_page_properties_html(rows)
        assert result.count("<tr>") == 5
        assert "GLOBAL" in result
        assert "DRAFT" in result
        assert "Weight Loss" in result

    def test_row_order_preserved(self):
        rows = [
            {"label": "First", "value": "1"},
            {"label": "Second", "value": "2"},
            {"label": "Third", "value": "3"},
        ]
        result = build_page_properties_html(rows)
        assert result.index("First") < result.index("Second") < result.index("Third")

    def test_empty_rows(self):
        result = build_page_properties_html([])
        assert 'ac:name="details"' in result
        assert "<tbody></tbody>" in result


class TestParsePagePropertiesArg:
    def test_inline_json(self):
        raw = '[{"label": "Title", "value": "My RFC"}]'
        result = parse_page_properties_arg(raw)
        assert len(result) == 1
        assert result[0]["label"] == "Title"

    def test_file_reference(self):
        data = [{"label": "Squad", "value": "WL"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            result = parse_page_properties_arg(f"@{f.name}")
        os.unlink(f.name)
        assert len(result) == 1
        assert result[0]["value"] == "WL"

    def test_invalid_json_exits(self):
        import pytest
        with pytest.raises(SystemExit):
            parse_page_properties_arg("not json")

    def test_non_array_exits(self):
        import pytest
        with pytest.raises(SystemExit):
            parse_page_properties_arg('{"label": "Title"}')

    def test_missing_file_exits(self):
        import pytest
        with pytest.raises(SystemExit):
            parse_page_properties_arg("@/nonexistent/file.json")


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


class TestProcessMermaidImages:
    def _run(self, confluence, page_id, markdown, **kwargs):
        with patch("push_to_confluence.run_command"):
            with patch("push_to_confluence.os.path.exists", return_value=True):
                with patch("push_to_confluence.os.remove"):
                    return process_mermaid_images(confluence, page_id, markdown, **kwargs)

    def _resolve(self, content, placeholders):
        """Substitute placeholders to get the final HTML, matching main() logic."""
        for placeholder, real_html in placeholders.items():
            content = content.replace(f"<p>{placeholder}</p>", real_html)
            content = content.replace(placeholder, real_html)
        return content

    def test_returns_placeholder_and_dict(self):
        confluence = MagicMock()
        page_id = "12345"
        markdown = "```mermaid\ngraph TD\n    A --> B\n```"

        content, placeholders = self._run(confluence, page_id, markdown)

        # Content should have a placeholder, not raw XML
        assert "MERMAID_PLACEHOLDER_" in content
        assert "ac:image" not in content
        assert len(placeholders) == 1

        # The placeholder value should contain the image macro
        real_html = list(placeholders.values())[0]
        assert "ac:image" in real_html

    def test_default_no_source_block(self):
        """By default, only the image macro is emitted — no source code block."""
        confluence = MagicMock()
        page_id = "12345"
        markdown = "```mermaid\ngraph TD\n    A --> B\n```"

        content, placeholders = self._run(confluence, page_id, markdown)
        result = self._resolve(content, placeholders)

        assert "ac:image" in result
        assert 'ac:name="code"' not in result
        assert "CDATA" not in result

    def test_include_source_adds_collapsed_code_block(self):
        confluence = MagicMock()
        page_id = "12345"
        mermaid_src = "graph TD\n    A[Start] --> B[End]"
        markdown = f"```mermaid\n{mermaid_src}\n```"

        content, placeholders = self._run(confluence, page_id, markdown, include_source=True)
        result = self._resolve(content, placeholders)

        # Image macro present
        assert '<ac:image ac:width="100%">' in result
        assert "ri:attachment" in result

        # Source preserved in a collapsed code macro
        assert 'ac:name="code"' in result
        assert 'ac:name="language">mermaid</ac:parameter>' in result
        assert 'ac:name="collapse">true</ac:parameter>' in result
        assert "graph TD" in result
        assert "A[Start] --> B[End]" in result

    def test_include_source_arrows_no_escaping(self):
        """Mermaid arrows (-->) are preserved verbatim inside CDATA."""
        confluence = MagicMock()
        page_id = "12345"
        mermaid_src = "stateDiagram-v2\n    [*] --> Active"
        markdown = f"```mermaid\n{mermaid_src}\n```"

        content, placeholders = self._run(confluence, page_id, markdown, include_source=True)
        result = self._resolve(content, placeholders)

        assert "[*] --> Active" in result
        assert "&#45;" not in result
        assert "CDATA[" in result

    def test_mermaid_image_macro_present(self):
        confluence = MagicMock()
        page_id = "12345"
        markdown = "```mermaid\ngraph LR\n    A --> B\n```"

        content, placeholders = self._run(confluence, page_id, markdown)
        result = self._resolve(content, placeholders)

        assert '<ac:image ac:width="100%">' in result
        assert "ri:attachment" in result
        assert 'ri:filename="mermaid-' in result

    def test_multiple_diagrams_get_separate_placeholders(self):
        confluence = MagicMock()
        page_id = "12345"
        markdown = (
            "```mermaid\ngraph TD\n    A --> B\n```\n\n"
            "Some text between.\n\n"
            "```mermaid\ngraph LR\n    C --> D\n```"
        )

        content, placeholders = self._run(confluence, page_id, markdown)

        assert len(placeholders) == 2
        assert content.count("MERMAID_PLACEHOLDER_") == 2
        assert "Some text between." in content

    def test_no_mermaid_returns_empty_placeholders(self):
        confluence = MagicMock()
        page_id = "12345"
        markdown = "Just regular markdown with no mermaid."

        content, placeholders = self._run(confluence, page_id, markdown)

        assert content == markdown
        assert placeholders == {}

    def test_placeholder_unwrapped_from_p_tags(self):
        """Pandoc may wrap a standalone placeholder in <p> tags — substitution should handle that."""
        confluence = MagicMock()
        page_id = "12345"
        markdown = "```mermaid\ngraph TD\n    A --> B\n```"

        content, placeholders = self._run(confluence, page_id, markdown)

        # Simulate what Pandoc does: wrap placeholder in <p> tags
        wrapped = f"<p>{content}</p>"
        result = self._resolve(wrapped, placeholders)

        # Should have the real macros, not wrapped in <p>
        assert "ac:image" in result
        assert f"<p>MERMAID_PLACEHOLDER_" not in result
