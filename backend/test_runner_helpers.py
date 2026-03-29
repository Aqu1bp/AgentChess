import unittest

import chess

import runner


class RunnerHelperTests(unittest.TestCase):
    def test_parse_candidates_with_line(self):
        text = """CANDIDATES:
1. MOVE: e7e5 (SAN: e5) | LINE: e5 Nf3 Nc6 | REASONING: central control
2. MOVE: d7d5 (SAN: d5) | LINE: d5 exd5 Qxd5 | REASONING: challenge center
3. MOVE: g8f6 (SAN: Nf6) | LINE: Nf6 e5 Nd5 | REASONING: develops knight
"""

        result = runner.parse_candidates(text)

        self.assertEqual(3, len(result))
        self.assertEqual("e5 Nf3 Nc6", result[0]["line"])

    def test_parse_candidates_falls_back_without_line(self):
        text = """CANDIDATES:
1. MOVE: e7e5 (SAN: e5) | REASONING: central control
2. MOVE: d7d5 (SAN: d5) | REASONING: challenge center
3. MOVE: g8f6 (SAN: Nf6) | REASONING: develops knight
"""

        result = runner.parse_candidates(text)

        self.assertEqual(3, len(result))
        self.assertEqual("", result[0]["line"])

    def test_rank_passing_moves_demotes_heavy_warnings(self):
        results = [
            {
                "candidate": {"san": "a6"},
                "validation": {"passed": True, "warnings": ["a", "b", "c"]},
            },
            {
                "candidate": {"san": "e6"},
                "validation": {"passed": True, "warnings": []},
            },
            {
                "candidate": {"san": "g6"},
                "validation": {"passed": False, "warnings": []},
            },
        ]

        ranked = runner.rank_passing_moves(results)

        self.assertEqual(2, len(ranked))
        self.assertEqual("e6", ranked[0]["candidate"]["san"])
        self.assertEqual("a6", ranked[1]["candidate"]["san"])

    def test_build_board_brief_includes_move_history(self):
        board = chess.Board()
        for san in ["d4", "d5", "Nc3"]:
            board.push_san(san)

        brief = runner.build_board_brief(board, ["d4", "d5", "Nc3"])

        self.assertIn("MOVE HISTORY: 1. d4 d5 2. Nc3", brief)


if __name__ == "__main__":
    unittest.main()
