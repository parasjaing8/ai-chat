"""
Unit tests for pure / near-pure functions in server.py.

Covered:
  - slugify()
  - extract_files_from_response()  (all four parsing patterns + fallback)
  - build_claude_messages()
  - parse_mentions()
"""

import os
import sys
import importlib
from unittest.mock import patch

import pytest

# ── Import target module ──────────────────────────────────────────────────────
# server.py lives one directory above this tests/ folder (added to sys.path by
# conftest.py).  Import it once; the per-test helpers grab names from it.
import server as srv


# ─────────────────────────────────────────────────────────────────────────────
# slugify
# ─────────────────────────────────────────────────────────────────────────────

class TestSlugify:
    def test_basic_spaces(self):
        assert srv.slugify("Hello World") == "hello-world"

    def test_uppercase_lowercased(self):
        assert srv.slugify("MY PROJECT") == "my-project"

    def test_special_chars_replaced(self):
        assert srv.slugify("foo!@#$bar") == "foo-bar"

    def test_multiple_special_chars_become_single_dash(self):
        assert srv.slugify("foo   ---   bar") == "foo-bar"

    def test_leading_trailing_dashes_stripped(self):
        assert srv.slugify("---hello---") == "hello"

    def test_numbers_preserved(self):
        assert srv.slugify("project 42") == "project-42"

    def test_empty_string_returns_project(self):
        assert srv.slugify("") == "project"

    def test_only_specials_returns_project(self):
        assert srv.slugify("!!! @@@") == "project"

    def test_already_valid_slug_unchanged(self):
        assert srv.slugify("my-project") == "my-project"

    def test_unicode_stripped(self):
        # Non-ASCII characters should be treated as special chars → stripped
        assert srv.slugify("café project") == "caf-project"


# ─────────────────────────────────────────────────────────────────────────────
# extract_files_from_response
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractFilesFromResponse:

    # ── pattern S3: FILE: comment marker BEFORE a fenced block ───────────────

    def test_s3_html_comment_before_block(self):
        content = "<!-- FILE: index.html -->\n```html\n<html></html>\n```"
        files = srv.extract_files_from_response(content)
        assert len(files) == 1
        assert files[0]["filename"] == "index.html"
        assert "<html></html>" in files[0]["content"]

    def test_s3_js_slash_comment(self):
        content = "// FILE: js/app.js\n```javascript\nconst x = 1;\n```"
        files = srv.extract_files_from_response(content)
        assert len(files) == 1
        assert files[0]["filename"] == "js/app.js"
        assert "const x = 1;" in files[0]["content"]

    def test_s3_hash_comment(self):
        content = "# FILE: main.py\n```python\nprint('hello')\n```"
        files = srv.extract_files_from_response(content)
        assert len(files) == 1
        assert files[0]["filename"] == "main.py"

    def test_s3_src_prefix_stripped(self):
        content = "// FILE: src/index.html\n```html\n<html/>\n```"
        files = srv.extract_files_from_response(content)
        assert files[0]["filename"] == "index.html"

    def test_s3_multiple_files(self):
        content = (
            "// FILE: index.html\n```html\n<html/>\n```\n\n"
            "// FILE: js/app.js\n```javascript\nvar x;\n```"
        )
        files = srv.extract_files_from_response(content)
        names = [f["filename"] for f in files]
        assert "index.html" in names
        assert "js/app.js" in names

    # ── pattern S4: FILE: marker then raw code (no fences) ───────────────────

    def test_s4_raw_code_no_fences(self):
        content = "// FILE: config.json\n{\"key\": \"value\"}\n// FILE: notes.txt\nhello\n"
        files = srv.extract_files_from_response(content)
        names = [f["filename"] for f in files]
        assert "config.json" in names
        assert "notes.txt" in names

    # ── pattern S1: **`path`** heading before fenced block ───────────────────

    def test_s1_bold_backtick_heading(self):
        content = "**`index.html`**\n```html\n<html/>\n```"
        files = srv.extract_files_from_response(content)
        assert len(files) == 1
        assert files[0]["filename"] == "index.html"

    def test_s1_h3_backtick_heading(self):
        content = "### `style.css`\n```css\nbody{}\n```"
        files = srv.extract_files_from_response(content)
        assert len(files) == 1
        assert files[0]["filename"] == "style.css"

    # ── pattern S2: FILE: as first line INSIDE a fenced block ────────────────

    def test_s2_file_marker_first_line_in_block(self):
        content = "```javascript\n// FILE: js/main.js\nconsole.log(1);\n```"
        files = srv.extract_files_from_response(content)
        assert len(files) == 1
        assert files[0]["filename"] == "js/main.js"
        assert "console.log(1);" in files[0]["content"]

    def test_s2_html_comment_marker_in_block(self):
        content = "```html\n<!-- FILE: index.html -->\n<html/>\n```"
        files = srv.extract_files_from_response(content)
        assert len(files) == 1
        assert files[0]["filename"] == "index.html"

    # ── fallback: bare ```html / ```javascript block ──────────────────────────

    def test_fallback_bare_html_block(self):
        content = "```html\n<html><body>Hello</body></html>\n```"
        files = srv.extract_files_from_response(content)
        assert len(files) == 1
        assert files[0]["filename"] == "index.html"
        assert "Hello" in files[0]["content"]

    def test_fallback_bare_js_block(self):
        content = "```javascript\nvar x = 1;\n```"
        files = srv.extract_files_from_response(content)
        assert len(files) == 1
        assert files[0]["filename"] == "js/main.js"

    # ── deduplication ─────────────────────────────────────────────────────────

    def test_duplicate_filename_takes_first(self):
        content = (
            "// FILE: index.html\n```html\n<html>first</html>\n```\n\n"
            "// FILE: index.html\n```html\n<html>second</html>\n```"
        )
        files = srv.extract_files_from_response(content)
        matching = [f for f in files if f["filename"] == "index.html"]
        assert len(matching) == 1
        assert "first" in matching[0]["content"]

    # ── empty / no match ──────────────────────────────────────────────────────

    def test_no_code_blocks_returns_empty(self):
        content = "Just some plain text with no code blocks."
        files = srv.extract_files_from_response(content)
        assert files == []

    def test_code_block_without_file_marker_and_unknown_lang_returns_empty(self):
        content = "```python\nprint('hello')\n```"
        files = srv.extract_files_from_response(content)
        assert files == []


# ─────────────────────────────────────────────────────────────────────────────
# build_claude_messages
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildClaudeMessages:

    def test_empty_history_returns_empty(self):
        assert srv.build_claude_messages([]) == []

    def test_last_message_not_user_returns_empty(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "claude", "content": "hello"},
        ]
        assert srv.build_claude_messages(history) == []

    def test_single_user_message(self):
        history = [{"role": "user", "content": "hi"}]
        msgs = srv.build_claude_messages(history)
        assert msgs == [{"role": "user", "content": "hi"}]

    def test_basic_exchange(self):
        history = [
            {"role": "user",   "content": "hello"},
            {"role": "claude", "content": "hi there"},
            {"role": "user",   "content": "how are you?"},
        ]
        msgs = srv.build_claude_messages(history)
        assert len(msgs) == 3
        assert msgs[0] == {"role": "user",      "content": "hello"}
        assert msgs[1] == {"role": "assistant",  "content": "[Claude]: hi there"}
        assert msgs[2] == {"role": "user",       "content": "how are you?"}

    def test_consecutive_user_messages_coalesced(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "world"},
        ]
        msgs = srv.build_claude_messages(history)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert "hello" in msgs[0]["content"]
        assert "world" in msgs[0]["content"]

    def test_consecutive_ai_messages_coalesced(self):
        history = [
            {"role": "user",     "content": "hi"},
            {"role": "claude",   "content": "hello"},
            {"role": "deepseek", "content": "hey"},
            {"role": "user",     "content": "thanks"},
        ]
        msgs = srv.build_claude_messages(history)
        # AI messages should be coalesced into one assistant turn
        assert msgs[1]["role"] == "assistant"
        assert "[Claude]: hello" in msgs[1]["content"]
        assert "[DeepSeek]: hey" in msgs[1]["content"]

    def test_agent_labels_applied(self):
        history = [
            {"role": "user",     "content": "who are you?"},
            {"role": "deepseek", "content": "I am DeepSeek"},
            {"role": "user",     "content": "ok"},
        ]
        msgs = srv.build_claude_messages(history)
        assert "[DeepSeek]: I am DeepSeek" in msgs[1]["content"]

    def test_unknown_agent_uses_role_as_label(self):
        history = [
            {"role": "user",      "content": "hi"},
            {"role": "mystery",   "content": "boo"},
            {"role": "user",      "content": "ok"},
        ]
        msgs = srv.build_claude_messages(history)
        assert "[mystery]: boo" in msgs[1]["content"]


# ─────────────────────────────────────────────────────────────────────────────
# parse_mentions
# ─────────────────────────────────────────────────────────────────────────────

class TestParseMentions:
    """
    parse_mentions depends on is_claude_available() and get_master_model() which
    read _config and ANTHROPIC_API_KEY.  We patch at the server module level.
    """

    def _with_claude(self):
        """Context: API key present, Claude enabled."""
        return patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"})

    def _without_claude(self):
        """Context: no API key → Claude unavailable."""
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)
        return patch.dict(os.environ, env, clear=False)

    # ── @all ──────────────────────────────────────────────────────────────────

    def test_at_all_with_claude_available(self):
        with self._with_claude():
            srv._config["claude_enabled"] = True
            result = srv.parse_mentions("@all please help", claude_online=True)
        assert "claude" in result
        assert "deepseek" in result
        assert "qwen" in result

    def test_at_all_without_claude(self):
        with self._without_claude():
            srv._config["claude_enabled"] = True   # enabled but no key
            result = srv.parse_mentions("@all please help", claude_online=True)
        assert "claude" not in result
        assert "deepseek" in result
        assert "qwen" in result

    def test_at_all_claude_disabled(self):
        with self._with_claude():
            srv._config["claude_enabled"] = False
            result = srv.parse_mentions("@all please help", claude_online=True)
        srv._config["claude_enabled"] = True   # restore
        assert "claude" not in result

    # ── @claude ───────────────────────────────────────────────────────────────

    def test_at_claude_when_available(self):
        with self._with_claude():
            srv._config["claude_enabled"] = True
            result = srv.parse_mentions("@claude explain this", claude_online=True)
        assert result == ["claude"]

    def test_at_claude_when_unavailable(self):
        with self._without_claude():
            result = srv.parse_mentions("@claude explain this", claude_online=False)
        assert "claude" not in result

    # ── individual model mentions ─────────────────────────────────────────────

    def test_at_deepseek(self):
        result = srv.parse_mentions("@deepseek write the code", claude_online=False)
        assert result == ["deepseek"]

    def test_at_qwen(self):
        result = srv.parse_mentions("@qwen analyse this", claude_online=False)
        assert result == ["qwen"]

    def test_multiple_mentions(self):
        result = srv.parse_mentions("@deepseek and @qwen both respond", claude_online=False)
        assert "deepseek" in result
        assert "qwen" in result

    # ── no mention → default to master model ──────────────────────────────────

    def test_no_mention_returns_master(self):
        with self._without_claude():
            srv._config["master_model"] = "qwen"
            srv._config["claude_enabled"] = True
            result = srv.parse_mentions("just a plain message", claude_online=False)
        assert "qwen" in result

    # ── case-insensitive ──────────────────────────────────────────────────────

    def test_uppercase_mention_matched(self):
        result = srv.parse_mentions("@DeepSeek do it", claude_online=False)
        assert "deepseek" in result
