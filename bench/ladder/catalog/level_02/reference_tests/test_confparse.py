import os, tempfile, unittest
import confparse
from confparse import ParseError, parse

def err(text):
    try: parse(text)
    except ParseError as e: return e
    raise AssertionError(f"no ParseError for {text!r}")

class TestStructure(unittest.TestCase):
    def test_r01_sections_and_keys(self):
        self.assertEqual(parse("[a]\nx = 1\ny = hello\n[b]\nz = no\n"), {"a": {"x": 1, "y": "hello"}, "b": {"z": False}})
    def test_r01_keys_before_any_section_go_to_the_empty_section(self): self.assertEqual(parse("x = 1\n[s]\ny = 2\n"), {"": {"x": 1}, "s": {"y": 2}})
    def test_r01_the_empty_section_only_exists_when_it_has_keys(self): self.assertEqual(parse("[s]\ny = 2\n"), {"s": {"y": 2}}); self.assertEqual(parse(""), {}); self.assertEqual(parse("# only a comment\n\n"), {})
    def test_r01_an_empty_section_is_an_empty_dict(self): self.assertEqual(parse("[a]\n[b]\nx=1\n"), {"a": {}, "b": {"x": 1}})
    def test_r01_whitespace_is_ignored_around_names_and_values(self): self.assertEqual(parse("  [ s ]  \n\t key\t=\t value with spaces \t\n"), {"s": {"key": "value with spaces"}})
    def test_r01_split_at_the_first_equals_sign(self): self.assertEqual(parse("url = a=b=c\n"), {"": {"url": "a=b=c"}})
    def test_r01_empty_value_is_the_empty_string(self): self.assertEqual(parse("k =\nj=   \n"), {"": {"k": "", "j": ""}})
    def test_r01_repeated_section_merges(self): self.assertEqual(parse("[a]\nx=1\n[b]\ny=2\n[a]\nz=3\n"), {"a": {"x": 1, "z": 3}, "b": {"y": 2}})
    def test_r01_name_characters(self): self.assertEqual(parse("[my.sec-1_x]\nk-1.a_b = 1\n"), {"my.sec-1_x": {"k-1.a_b": 1}})
    def test_r01_crlf_line_endings(self): self.assertEqual(parse("[a]\r\nx = 1\r\ny = two\r\n"), {"a": {"x": 1, "y": "two"}})
    def test_r01_no_trailing_newline(self): self.assertEqual(parse("[a]\nx = 1"), {"a": {"x": 1}})
    def test_r01_non_string_raises_type_error(self):
        for bad in (None, 5, b"x = 1", ["x"]):
            with self.assertRaises(TypeError): parse(bad)
    def test_r01_same_key_in_different_sections_is_fine(self): self.assertEqual(parse("[a]\nx=1\n[b]\nx=2\n"), {"a": {"x": 1}, "b": {"x": 2}})
    def test_r01_values_are_not_shared_between_calls(self):
        a = parse("[s]\nx=1\n"); a["s"]["x"] = 99; self.assertEqual(parse("[s]\nx=1\n"), {"s": {"x": 1}})

class TestComments(unittest.TestCase):
    def test_r02_full_line_comments_and_blank_lines(self): self.assertEqual(parse("# c1\n; c2\n\n   # indented\n\t; tab\n[a]\nx=1\n"), {"a": {"x": 1}})
    def test_r02_inline_comment_needs_preceding_whitespace(self):
        self.assertEqual(parse("a = b # note\n")[""]["a"], "b"); self.assertEqual(parse("a = b ; note\n")[""]["a"], "b"); self.assertEqual(parse("a = b#c\n")[""]["a"], "b#c"); self.assertEqual(parse("a = b;c\n")[""]["a"], "b;c")
    def test_r02_value_that_is_only_a_comment_is_empty(self): self.assertEqual(parse("a = # only a comment\n")[""]["a"], "")
    def test_r02_a_hash_right_after_the_equals_sign_is_part_of_the_value(self): self.assertEqual(parse("a =# not a comment\n")[""]["a"], "# not a comment")
    def test_r02_comment_after_a_section_header(self): self.assertEqual(parse("[a] # why\nx=1\n"), {"a": {"x": 1}})
    def test_r02_comment_after_a_section_header_with_semicolon(self): self.assertEqual(parse("[a]  ; why\nx=1\n"), {"a": {"x": 1}})
    def test_r02_a_comment_hides_typing(self): self.assertEqual(parse("n = 5 # five\n")[""]["n"], 5)
    def test_r02_hash_inside_quotes_is_not_a_comment(self): self.assertEqual(parse('a = "x # y ; z"\n')[""]["a"], "x # y ; z")
    def test_r02_comment_after_a_quoted_value(self): self.assertEqual(parse('a = "x"   # c\n')[""]["a"], "x"); self.assertEqual(parse("a = 'x' ; c\n")[""]["a"], "x")
    def test_r02_comment_after_a_list(self): self.assertEqual(parse("a = [1, 2] # c\n")[""]["a"], [1, 2])
    def test_r02_equals_sign_inside_a_comment_line_is_ignored(self): self.assertEqual(parse("# a = 1\n[s]\nb=2\n"), {"s": {"b": 2}})
    def test_r02_inline_comment_may_contain_quotes_and_brackets(self): self.assertEqual(parse('a = 1 # "unterminated [ (\nb = 2\n'), {"": {"a": 1, "b": 2}})
    def test_r02_comment_only_file(self): self.assertEqual(parse("; a\n# b\n"), {})

class TestScalars(unittest.TestCase):
    def v(self, raw): return parse(f"k = {raw}\n")[""]["k"]
    def test_r03_booleans_in_any_case(self):
        for raw in ("true", "True", "TRUE", "yes", "Yes", "on", "ON"): self.assertIs(self.v(raw), True, raw)
        for raw in ("false", "False", "no", "NO", "off", "Off"): self.assertIs(self.v(raw), False, raw)
    def test_r03_null(self):
        for raw in ("null", "NULL", "Null"): self.assertIsNone(self.v(raw))
    def test_r03_integers(self):
        for raw, want in (("0", 0), ("42", 42), ("-5", -5), ("+3", 3), ("007", 7), ("1234567890123", 1234567890123)): self.assertEqual(self.v(raw), want); self.assertIs(type(self.v(raw)), int)
    def test_r03_floats(self):
        for raw, want in (("1.5", 1.5), ("-0.25", -0.25), ("1e3", 1000.0), ("2.5E-3", 0.0025), ("0.0", 0.0), ("3.0e+2", 300.0)): self.assertEqual(self.v(raw), want); self.assertIs(type(self.v(raw)), float)
    def test_r03_everything_else_stays_a_string(self):
        for raw in ("1.", ".5", "0x10", "1_000", "1,5", "hello", "truex", "nul", "1e", "e3", "--1", "1.2.3", "yes please", "12abc", "+", "-"): self.assertEqual(self.v(raw), raw, raw)
    def test_r03_surrounding_whitespace_is_trimmed_before_typing(self): self.assertIs(self.v("   true   "), True); self.assertEqual(self.v("\t 12 \t"), 12)
    def test_r03_an_integer_is_not_a_bool(self): self.assertIs(type(self.v("1")), int); self.assertIs(type(self.v("0")), int)
    def test_r03_words_that_look_like_booleans_but_are_not(self): self.assertEqual(self.v("y"), "y"); self.assertEqual(self.v("n"), "n"); self.assertEqual(self.v("t"), "t")

class TestQuotes(unittest.TestCase):
    def v(self, raw): return parse(f"k = {raw}\n")[""]["k"]
    def test_r04_quoted_values_are_never_typed(self):
        for raw, want in (('"42"', "42"), ("'true'", "true"), ('"null"', "null"), ('"1.5"', "1.5"), ("'on'", "on")): self.assertEqual(self.v(raw), want)
    def test_r04_inner_whitespace_and_special_characters_are_kept(self): self.assertEqual(self.v('"  a  b  "'), "  a  b  "); self.assertEqual(self.v("'a=b # c ; d'"), "a=b # c ; d")
    def test_r04_double_quoted_escapes(self): self.assertEqual(self.v(r'"a\nb\tc\rd\\e\"f"'), 'a\nb\tc\rd\\e"f')
    def test_r04_single_quoted_has_no_escapes(self): self.assertEqual(self.v(r"'a\nb\\c'"), r"a\nb\\c")
    def test_r04_a_double_quote_inside_single_quotes(self): self.assertEqual(self.v("""'say "hi"'"""), 'say "hi"')
    def test_r04_a_single_quote_inside_double_quotes(self): self.assertEqual(self.v('''"it's"'''), "it's")
    def test_r04_empty_quoted_strings(self): self.assertEqual(self.v('""'), ""); self.assertEqual(self.v("''"), "")
    def test_r04_a_quoted_value_ends_at_its_closing_quote(self): self.assertEqual(self.v('"a" # c'), "a"); self.assertEqual(self.v("'a'   "), "a")
    def test_r04_a_trailing_backslash_inside_double_quotes_before_the_quote(self): self.assertEqual(self.v(r'"C:\\"'), "C:\\")
    def test_r04_escaped_quote_does_not_end_the_string(self): self.assertEqual(self.v(r'"a\"b"'), 'a"b')
    def test_r04_unknown_escapes_are_errors(self):
        for esc in (r"\q", r"\x41", r"\u0041", r"\0", r"\ "): self.assertIn("unknown escape", err(f'k = "{esc}"\n').message.lower())
    def test_r04_text_after_the_closing_quote_is_an_error(self):
        self.assertIn("unexpected text after", err('k = "a" b\n').message.lower()); self.assertIn("unexpected text after", err("k = 'a'b\n").message.lower())
    def test_r04_unterminated_strings(self):
        self.assertIn("unterminated string", err('k = "abc\n').message.lower()); self.assertIn("unterminated string", err("k = 'abc\n").message.lower()); self.assertIn("unterminated string", err('k = "abc\\"\n').message.lower())
    def test_r04_unicode_is_preserved(self): self.assertEqual(self.v('"héllo ✓"'), "héllo ✓")

class TestLists(unittest.TestCase):
    def v(self, raw): return parse(f"k = {raw}\n")[""]["k"]
    def test_r05_example_from_the_spec(self): self.assertEqual(self.v('[a, "b, c", 3, true]'), ["a", "b, c", 3, True])
    def test_r05_empty_list(self): self.assertEqual(self.v("[]"), []); self.assertEqual(self.v("[   ]"), [])
    def test_r05_trailing_comma(self): self.assertEqual(self.v("[1, 2,]"), [1, 2]); self.assertEqual(self.v("[1, 2, ]"), [1, 2]); self.assertEqual(self.v("[1,]"), [1])
    def test_r05_typed_elements(self): self.assertEqual(self.v("[1, 2.5, no, null, x]"), [1, 2.5, False, None, "x"])
    def test_r05_quoted_elements_are_strings(self): self.assertEqual(self.v("""["1", '2', "true"]"""), ["1", "2", "true"])
    def test_r05_whitespace_around_elements_is_trimmed(self): self.assertEqual(self.v("[  a ,b,   c  ]"), ["a", "b", "c"])
    def test_r05_quoted_element_with_brackets_and_commas(self): self.assertEqual(self.v('["[x]", "a,b"]'), ["[x]", "a,b"])
    def test_r05_nested_lists_are_an_error(self): self.assertIn("nested list", err("k = [1, [2]]\n").message.lower()); self.assertIn("nested list", err("k = [[1]]\n").message.lower())
    def test_r05_unterminated_list(self): self.assertIn("unterminated list", err("k = [1, 2\n").message.lower()); self.assertIn("unterminated list", err("k = [\n").message.lower())
    def test_r05_empty_elements_are_errors(self):
        for raw in ("[1,,2]", "[,]", "[,1]", "[1, , 2]"): self.assertIn("empty list element", err(f"k = {raw}\n").message.lower(), raw)
    def test_r05_text_after_the_closing_bracket_is_an_error(self): self.assertIn("unexpected text after", err("k = [1] x\n").message.lower())
    def test_r05_a_value_that_merely_contains_a_bracket_is_a_string(self): self.assertEqual(self.v("a[1]"), "a[1]"); self.assertEqual(self.v("x ]"), "x ]")
    def test_r05_a_quoted_bracket_is_not_a_list(self): self.assertEqual(self.v('"[1, 2]"'), "[1, 2]")
    def test_r05_list_in_a_section(self): self.assertEqual(parse("[s]\ntags = [a, b]\n"), {"s": {"tags": ["a", "b"]}})
    def test_r05_quoted_element_followed_by_junk_is_an_error(self): self.assertIn("unexpected text after", err('k = ["a" b]\n').message.lower())

class TestContinuation(unittest.TestCase):
    def test_r06_simple_continuation(self): self.assertEqual(parse("a = hello \\\n    world\n"), {"": {"a": "hello world"}})
    def test_r06_the_backslash_is_removed_and_leading_whitespace_of_the_next_line_dropped(self): self.assertEqual(parse("a = x\\\n   y\n")[""]["a"], "xy")
    def test_r06_repeated_continuation(self): self.assertEqual(parse("a = 1\\\n2\\\n3\n")[""]["a"], 123)
    def test_r06_inside_a_quoted_string(self): self.assertEqual(parse('a = "x \\\n   y"\n')[""]["a"], "x y")
    def test_r06_inside_a_list(self): self.assertEqual(parse("a = [1, \\\n   2, \\\n   3]\n")[""]["a"], [1, 2, 3])
    def test_r06_the_joined_text_is_parsed_as_one_line(self): self.assertIs(parse("a = tr\\\nue\n")[""]["a"], True)
    def test_r06_continuation_of_the_key_part(self): self.assertEqual(parse("long\\\nname = 1\n"), {"": {"longname": 1}})
    def test_r06_comment_lines_are_never_continued(self): self.assertEqual(parse("# comment \\\nb = 2\n"), {"": {"b": 2}})
    def test_r06_section_headers_are_never_continued(self): self.assertIn("unexpected text after", err("[a] \\\nb = 2\n").message.lower())
    def test_r06_a_backslash_before_trailing_whitespace_is_not_a_continuation(self): self.assertEqual(parse("a = x\\ \nb = 2\n"), {"": {"a": "x\\", "b": 2}})
    def test_r06_the_end_of_file_after_a_continuation_is_an_error(self):
        for text in ("a = b\\", "a = b\\\n"): self.assertIn("unexpected end of file", err(text).message.lower())
    def test_r06_error_line_numbers_use_the_first_physical_line(self): self.assertEqual(err("x = 1\ny = \"abc \\\n def\nz = 3\n").line, 2)
    def test_r06_lines_after_a_continuation_keep_counting(self): self.assertEqual(err("a = 1 \\\n+ 2\nbad line\n").line, 3)
    def test_r06_continuation_then_comment_at_the_end(self): self.assertEqual(parse("a = x \\\n y # c\n")[""]["a"], "x y")

class TestErrors(unittest.TestCase):
    def test_r07_parse_error_is_an_exception_with_line_and_message(self):
        e = err("a = 1\nnonsense\n"); self.assertIsInstance(e, Exception); self.assertEqual(e.line, 2); self.assertEqual(str(e), f"line 2: {e.message}"); self.assertIn("missing '='", e.message.lower())
    def test_r07_missing_equals(self): self.assertIn("missing '='", err("just words\n").message.lower())
    def test_r07_invalid_keys(self):
        for k in ("1a", "a b", "-x", "a$", "", "ключ"): self.assertIn("invalid key", err(f"{k} = 1\n").message.lower(), repr(k))
    def test_r07_invalid_section_names(self):
        for n in ("", "a b", "1x", "a/b", "-x"): self.assertIn("invalid section name", err(f"[{n}]\n").message.lower(), repr(n))
    def test_r07_unterminated_section_header(self): self.assertIn("unterminated section header", err("[abc\n").message.lower())
    def test_r07_text_after_a_section_header(self): self.assertIn("unexpected text after", err("[a] b\n").message.lower())
    def test_r07_duplicate_key(self): e = err("[s]\nport = 1\nport = 2\n"); self.assertIn("duplicate key 'port'", e.message.lower()); self.assertEqual(e.line, 3)
    def test_r07_duplicate_key_in_the_global_section(self): self.assertIn("duplicate key 'x'", err("x = 1\nx = 2\n").message.lower())
    def test_r07_duplicate_key_across_a_merged_section(self): self.assertIn("duplicate key 'a'", err("[s]\na=1\n[t]\n[s]\na=2\n").message.lower())
    def test_r07_the_first_error_in_file_order_wins(self): e = err("ok = 1\nbad one\nalso bad = [1,,2]\n"); self.assertEqual(e.line, 2)
    def test_r07_line_numbers_count_blank_and_comment_lines(self): self.assertEqual(err("\n# c\n\nbroken\n").line, 4)
    def test_r07_line_number_with_crlf(self): self.assertEqual(err("a = 1\r\nb = 2\r\noops\r\n").line, 3)
    def test_r07_message_phrases_are_case_insensitive(self): self.assertEqual(err("x\n").message.lower().count("missing '='"), 1)
    def test_r07_an_error_is_raised_not_returned_for_every_bad_input(self):
        for bad in ("x", "[a", "a=\"", "a=[", "a=[[1]]", "a=[1,,2]", "a=b\\", "a='x'y"): err(bad + "\n" if not bad.endswith("\\") else bad)
    def test_r07_valid_files_never_raise(self):
        parse("[a]\nx = 1\n# fine\n[b]\ny = [1, 2]\nz = 'q' # c\n")

class TestParseFile(unittest.TestCase):
    def write(self, data, mode="wb"):
        fd, path = tempfile.mkstemp(suffix=".conf"); os.close(fd); self.addCleanup(os.unlink, path)
        with open(path, mode) as f: f.write(data)
        return path
    def test_r08_reads_and_parses(self): self.assertEqual(confparse.parse_file(self.write(b"[a]\nx = 1\n")), {"a": {"x": 1}})
    def test_r08_utf8_content(self): self.assertEqual(confparse.parse_file(self.write("[a]\nn = \"héllo ✓\"\n".encode("utf-8"))), {"a": {"n": "héllo ✓"}})
    def test_r08_a_byte_order_mark_is_ignored(self): self.assertEqual(confparse.parse_file(self.write(b"\xef\xbb\xbf[a]\nx = 1\n")), {"a": {"x": 1}})
    def test_r08_a_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError): confparse.parse_file("/no/such/dir/x.conf")
    def test_r08_parse_errors_propagate(self):
        with self.assertRaises(ParseError) as cm: confparse.parse_file(self.write(b"a = 1\nbroken\n"))
        self.assertEqual(cm.exception.line, 2)
    def test_r08_crlf_file(self): self.assertEqual(confparse.parse_file(self.write(b"a = 1\r\nb = 2\r\n")), {"": {"a": 1, "b": 2}})
    def test_r08_an_empty_file(self): self.assertEqual(confparse.parse_file(self.write(b"")), {})
