"""codediff.py : le code découpé en symboles, différences symbole à symbole (mise en
page ignorée) et fusion différence par différence."""

from tests import _env  # noqa: F401  (données temporaires)

import unittest

import codediff

REFORMAT_L = """bool Client::connect(const std::string& host, int port) {
    if (!socket_.open(host, port, 3000)) { log("Connexion impossible"); return false; }
    return true;
}
"""
REFORMAT_R = """bool Client::connect(const std::string &host, int port)
{
    if (!socket_.open(host, port, 5000)) {
        log("Connexion impossible");
        return false;
    }
    return true;
}
"""


def keys(text, lang="cpp", comments=True):
    return [t[3] for t in codediff.tokenize(text, lang) if comments or t[0] != "comment"]


def changes(left, right, lang="cpp", **options):
    return [(h["tag"], h["left"], h["right"]) for h in codediff.diff(left, right, lang, options)["hunks"]]


def merged(left, right, choice, lang="cpp", **options):
    hunks = codediff.diff(left, right, lang, options)["hunks"]
    return codediff.merge(left, hunks, [{"choice": choice} if isinstance(choice, str) else choice] * len(hunks))[0]


class TokenizeTest(unittest.TestCase):
    def test_cpp_symbols(self):
        self.assertEqual(keys('auto s = R"x(a "b" c)x"; n = 1\'000\'000; p->*m; a <=> b; x <<= 2; // fin'),
                         ["auto", "s", "=", 'R"x(a "b" c)x"', ";", "n", "=", "1'000'000", ";", "p", "->*", "m", ";",
                          "a", "<=>", "b", ";", "x", "<<=", "2", ";", "// fin"])
        self.assertEqual([t[0] for t in codediff.tokenize("int x = 1.5e-3;", "cpp")],
                         ["keyword", "identifier", "punct", "number", "punct"])

    def test_other_languages(self):
        self.assertEqual(keys('$x = "C:\\temp\\" # note\n$y = $#a', "powershell"),
                         ["$x", "=", '"C:\\temp\\"', "# note", "$y", "=", "$", "#", "a"])
        self.assertEqual(keys("SELECT [Nom], 'l''été' FROM t -- c", "sql"),
                         ["SELECT", "[Nom]", ",", "'l''été'", "FROM", "t", "-- c"])
        self.assertEqual(keys("<a href=\"x\">l'été</a>", "xml"), ["<", "a", "href", "=", '"x"', ">", "l", "'", "été", "</", "a", ">"])

    def test_python_indentation_is_a_symbol(self):
        toks = codediff.tokenize("if a:\n    b(1,\n      2)\nc()\n", "python")
        self.assertEqual([t[3] for t in toks if t[0] == "indent"], ["\x000", "\x001", "\x000"])

    def test_language(self):
        self.assertEqual(codediff.language(r"C:\src\client.cpp"), "cpp")
        self.assertEqual(codediff.language("CMakeLists.txt"), "cmake")
        self.assertEqual(codediff.language("notes.txt"), "")
        self.assertEqual(codediff.label("cpp"), "C++")
        self.assertEqual(codediff.guess("def f(x):\n    return x\n"), "python")
        self.assertEqual(codediff.guess("int main() {\n  return 0;\n}\n"), "code")
        self.assertEqual(codediff.guess("Bonjour,\nComment ça va ?\n"), "")


class DiffTest(unittest.TestCase):
    def test_layout_ignored(self):
        self.assertEqual(changes(REFORMAT_L, REFORMAT_R), [("replace", "3000", "5000")])
        self.assertEqual(changes("x = f(a,\n      b)\n", "x = f(a, b)\n", "python"), [])

    def test_independent_changes_on_one_line(self):
        self.assertEqual(changes("for (int i = 0; i <= n; ++i)\n", "for (size_t i = 0; i < n; ++i)\n"),
                         [("replace", "int", "size_t"), ("replace", "<=", "<")])

    def test_whole_operators_and_numbers(self):
        self.assertEqual(changes("flags <<= 2;\nv = 1.5e-3;\n", "flags << 2;\nv = 1.5e+3;\n"),
                         [("replace", "<<=", "<<"), ("replace", "1.5e-3", "1.5e+3")])

    def test_comments_ignored_on_request(self):
        left = "// Calcule (TODO)\nint d(int p) { return p / 10; }  // 10 %\n"
        right = "// Calcule.\nint d(int p) { return p / 20; }  /* 20 % */\n"
        self.assertEqual(changes(left, right, comments=True), [("replace", "10", "20")])
        self.assertEqual(len(changes(left, right)), 2)

    def test_rewritten_passage_is_not_confetti(self):
        left = "int f(v, key) {\n    for (size_t i = 0; i < v.size(); ++i)\n        if (v[i] == key) return int(i);\n    return -1;\n}\n"
        right = "int f(v, key) {\n    auto it = std::find(v.begin(), v.end(), key);\n    return it == v.end() ? -1 : int(it - v.begin());\n}\n"
        self.assertEqual(len(changes(left, right)), 1)

    def test_changed_line_and_added_line_stay_apart(self):
        left = "// pool.cpp — version 1\n#include <chrono>\n"
        right = "// pool.cpp — version 2\n#include <algorithm>\n#include <chrono>\n"
        self.assertEqual(changes(left, right), [("replace", "// pool.cpp — version 1", "// pool.cpp — version 2"),
                                                ("insert", "", "#include <algorithm>")])
        self.assertEqual(changes("a = 1\n", "a = 2\nb = 3\n", "python"), [("replace", "1", "2"), ("insert", "", "\nb = 3")])

    def test_changes_on_two_lines_stay_apart(self):
        left = "if (n) v = 1.5e-3;\nlog(\"a\");\n"
        right = "if (n) v = 1.5e+3;\ntrace(\"b\");\n"
        self.assertEqual(changes(left, right), [("replace", "1.5e-3", "1.5e+3"), ("replace", 'log("a"', 'trace("b"')])

    def test_python_indentation_change_stays_visible(self):
        left = "if ok:\n    s += qty\n    log(s)\n"
        right = "if ok:\n    s += quantity\nlog(s)\n"
        self.assertEqual(changes(left, right, "python"), [("replace", "qty", "quantity"), ("replace", "\n    ", "\n")])
        self.assertEqual(merged(left, right, "right", "python"), right)

    def test_renames_share_a_key(self):
        res = codediff.diff("int acc = 0;\nacc += x;\nreturn acc;\n", "int total = 0;\ntotal += x;\nreturn total;\n", "cpp")
        self.assertEqual(len(res["hunks"]), 3)
        self.assertEqual(len({h["same_key"] for h in res["hunks"]}), 1)
        self.assertEqual(len({h["sig"] for h in res["hunks"]}), 3)

    def test_big_changed_regions(self):
        # Un nom changé sur chaque ligne : une différence par ligne (et vite : découpage aux symboles uniques)
        left = "\n".join(f"x{i} = f(a{i}, b{i});" for i in range(3000))
        right = "\n".join(f"x{i} = f(a{i}, c{i});" for i in range(3000))
        self.assertEqual(len(codediff.diff(left, right, "cpp")["hunks"]), 3000)
        # Réécrit : une seule différence
        right = "\n".join(f"print_value_{i}++;" for i in range(3000))
        self.assertEqual(len(codediff.diff(left, right, "cpp")["hunks"]), 1)

    def test_lines_and_marks(self):
        [hunk] = codediff.diff(REFORMAT_L, REFORMAT_R, "cpp")["hunks"]
        self.assertEqual((hunk["a0"], hunk["a1"], hunk["b0"], hunk["b1"]), (1, 2, 2, 3))
        self.assertEqual(hunk["lmarks"], [[1, 34, 38]])
        # Ajout de lignes entières : entre deux lignes à gauche, pas de repère dans une ligne
        [hunk] = codediff.diff("a = 1;\nb = 2;\n", "a = 1;\nx++;\nb = 2;\n", "cpp")["hunks"]
        self.assertEqual((hunk["tag"], hunk["a0"], hunk["a1"], hunk["lmarks"]), ("insert", 1, 1, []))
        # Ajout au milieu d'une ligne : un repère à l'endroit
        [hunk] = codediff.diff("return;\n", "return x;\n", "cpp")["hunks"]
        self.assertEqual(hunk["lmarks"], [[0, 6, 6]])


class MergeTest(unittest.TestCase):
    CASES = [
        (REFORMAT_L, REFORMAT_R, "cpp"),
        ("a = 1; // note\n    b = 2;\n", "a = 1; // note\n    x++;\n    b = 2;\n", "cpp"),
        ("int a;\n", "x++;\nint a;\n", "cpp"),
        ("a;\n", "a;\nb;\n", "cpp"),
        ("f(a)\n", "f(a, b)\n", "cpp"),
        ("a = 1;\n    x++;\n    b = 2;\n", "a = 1;\n    b = 2;\n", "cpp"),
        ("x++;\nint a;\n", "int a;\n", "cpp"),
        ("", "int a;\n", "cpp"),
        ("if a:\n    b()\n    c()\n", "if a:\n    b()\nc()\n", "python"),
        ("a = 1\nb = 2\n", "a = 1; b = 2\n", "python"),
        ("a = 1; b = 2\n", "a = 1\nb = 2\n", "python"),
        ("a\nb\nc\n", "a\nc\n", "python"),
    ]

    def test_left_gives_left_and_right_gives_right_symbols(self):
        for left, right, lang in self.CASES:
            with self.subTest(left=left, right=right):
                self.assertEqual(merged(left, right, "left", lang), left)
                self.assertEqual(keys(merged(left, right, "right", lang), lang), keys(right, lang))

    def test_spaces_follow_added_and_removed_lines(self):
        self.assertEqual(merged("a = 1; // note\n    b = 2;\n", "a = 1; // note\n    x++;\n    b = 2;\n", "right"),
                         "a = 1; // note\n    x++;\n    b = 2;\n")
        self.assertEqual(merged("a = 1;\n    x++;\n    b = 2;\n", "a = 1;\n    b = 2;\n", "right"), "a = 1;\n    b = 2;\n")
        self.assertEqual(merged("return;\n", "return x;\n", "right"), "return x;\n")
        # Python : lignes jointes ou coupées, et indentation
        self.assertEqual(merged("a = 1\nb = 2\n", "a = 1; b = 2\n", "right", "python"), "a = 1; b = 2\n")
        self.assertEqual(merged("a = 1; b = 2\n", "a = 1\nb = 2\n", "right", "python"), "a = 1\nb = 2\n")
        self.assertEqual(merged("if a:\n    b()\n    c()\n", "if a:\n    b()\nc()\n", "right", "python"), "if a:\n    b()\nc()\n")

    def test_independent_choices_on_one_line(self):
        left, right = "for (int i = 0; i <= n; ++i)\n", "for (size_t i = 0; i < n; ++i)\n"
        hunks = codediff.diff(left, right, "cpp")["hunks"]
        text, unresolved = codediff.merge(left, hunks, [{"choice": "right"}, {"choice": "left"}])
        self.assertEqual((text, unresolved), ("for (size_t i = 0; i <= n; ++i)\n", 0))
        text, unresolved = codediff.merge(left, hunks, [None, {"choice": "text", "text": "!="}], "right")
        self.assertEqual((text, unresolved), ("for (size_t i = 0; i != n; ++i)\n", 1))

    def test_both_keeps_whole_lines_when_alone_on_them(self):
        left, right = "{\n    log(a);\n}\n", "{\n    trace(b);\n}\n"
        [hunk] = codediff.diff(left, right, "cpp")["hunks"]
        self.assertTrue(hunk.get("both"))
        self.assertEqual(merged(left, right, "left_right"), "{\n    log(a);\n    trace(b);\n}\n")
        self.assertEqual(merged(left, right, "right_left"), "{\n    trace(b);\n    log(a);\n}\n")
        # Deux différences sur une ligne : pas de « les deux »
        hunks = codediff.diff("for (int i = 0; i <= n; ++i)\n", "for (size_t i = 0; i < n; ++i)\n", "cpp")["hunks"]
        self.assertFalse(any(h.get("both") for h in hunks))

    def test_written_text_keeps_surrounding_spaces(self):
        left, right = "a = 1;\n    x++;\n    b = 2;\n", "a = 1;\n    b = 2;\n"
        self.assertEqual(merged(left, right, {"choice": "text", "text": "y--;"}), "a = 1;\n    y--;\n    b = 2;\n")
        self.assertEqual(merged(left, right, {"choice": "text", "text": ""}), "a = 1;\n    b = 2;\n")


if __name__ == "__main__":
    unittest.main()
