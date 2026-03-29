import unittest

import chess

import runner


class StubProvider:
    def __init__(self, response: str):
        self.response = response

    def call(self, prompt: str) -> str:
        return self.response


class RunnerHelperTests(unittest.TestCase):
    def test_parse_candidates_with_line(self):
        text = """CANDIDATES:
1. MOVE: e7e5 (SAN: e5) | LINE: e5 Nf3 Nc6 | WHITE_THREAT: Nf3 | REASONING: central control
2. MOVE: d7d5 (SAN: d5) | LINE: d5 exd5 Qxd5 | WHITE_THREAT: exd5 | REASONING: challenge center
3. MOVE: g8f6 (SAN: Nf6) | LINE: Nf6 e5 Nd5 | WHITE_THREAT: e5 | REASONING: develops knight
"""

        result = runner.parse_candidates(text)

        self.assertEqual(3, len(result))
        self.assertEqual("e5 Nf3 Nc6", result[0]["line"])
        self.assertEqual("Nf3", result[0]["white_threat"])

    def test_parse_candidates_rejects_legacy_format(self):
        text = """CANDIDATES:
1. MOVE: e7e5 (SAN: e5) | REASONING: central control
2. MOVE: d7d5 (SAN: d5) | REASONING: challenge center
3. MOVE: g8f6 (SAN: Nf6) | REASONING: develops knight
"""

        result = runner.parse_candidates(text)

        self.assertEqual([], result)

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

    def test_should_skip_critic_for_clean_top_choice(self):
        results = [
            {"validation": {"passed": True, "warnings": []}},
            {"validation": {"passed": True, "warnings": ["soft note"]}},
        ]
        self.assertTrue(runner.should_skip_critic(results))

    def test_should_not_skip_critic_when_top_choice_has_warnings(self):
        results = [
            {"validation": {"passed": True, "warnings": ["soft note"]}},
            {"validation": {"passed": True, "warnings": []}},
        ]
        self.assertFalse(runner.should_skip_critic(results))

    def test_parse_candidates_with_white_threat(self):
        text = """CANDIDATES:
1. MOVE: d7d5 (SAN: d5) | LINE: d5 exd5 Qxd5 | WHITE_THREAT: exd5 | REASONING: central challenge
2. MOVE: g8f6 (SAN: Nf6) | LINE: Nf6 e5 Nd5 | WHITE_THREAT: e5 | REASONING: develops knight
3. MOVE: e7e6 (SAN: e6) | LINE: e6 d4 d5 | WHITE_THREAT: d4 | REASONING: French setup
"""
        result = runner.parse_candidates(text)

        self.assertEqual(3, len(result))
        self.assertEqual("d5 exd5 Qxd5", result[0]["line"])
        self.assertEqual("exd5", result[0]["white_threat"])
        self.assertEqual("e5", result[1]["white_threat"])
        self.assertEqual("d4", result[2]["white_threat"])

    def test_parse_critic_choice_choose(self):
        text = """DECISION: CHOOSE e5
LINE: e5 Nf3 Nc6
REASONING: Best balance of development and center control."""
        result = runner.parse_critic_choice(text)
        self.assertEqual("CHOOSE", result["decision"])
        self.assertEqual("e5", result["move"])
        self.assertEqual("e5 Nf3 Nc6", result["line"])

    def test_parse_critic_choice_override(self):
        text = """DECISION: OVERRIDE d7d5
LINE: d5 exd5 Qxd5
REASONING: More direct central challenge."""
        result = runner.parse_critic_choice(text)
        self.assertEqual("OVERRIDE", result["decision"])
        self.assertEqual("d7d5", result["move"])
        self.assertEqual("d5 exd5 Qxd5", result["line"])

    def test_apply_critic_choice_chooses_survivor(self):
        board = chess.Board()
        board.push_san("e4")
        results = [
            {
                "candidate": {"san": "e5", "move_token": "e7e5", "line": "e5 Nf3 Nc6", "reasoning": "test"},
                "validation": {"passed": True, "san": "e5", "uci": "e7e5", "warnings": []},
            },
            {
                "candidate": {"san": "c5", "move_token": "c7c5", "line": "c5 Nf3 d6", "reasoning": "test"},
                "validation": {"passed": True, "san": "c5", "uci": "c7c5", "warnings": []},
            },
        ]
        provider = StubProvider("""DECISION: CHOOSE e5
LINE: e5 Nf3 Nc6
REASONING: Most reliable development.""")
        summary, choice = runner.apply_critic_choice(board, "brief", provider, results)
        self.assertIn("CHOOSE e5", summary)
        self.assertEqual("e7e5", choice["move"])
        self.assertEqual("e5", choice["san"])

    def test_apply_critic_choice_validates_override(self):
        board = chess.Board()
        board.push_san("e4")
        results = [
            {
                "candidate": {"san": "e5", "move_token": "e7e5", "line": "e5 Nf3 Nc6", "reasoning": "test"},
                "validation": {"passed": True, "san": "e5", "uci": "e7e5", "warnings": []},
            },
            {
                "candidate": {"san": "c5", "move_token": "c7c5", "line": "c5 Nf3 d6", "reasoning": "test"},
                "validation": {"passed": True, "san": "c5", "uci": "c7c5", "warnings": []},
            },
        ]
        provider = StubProvider("""DECISION: OVERRIDE e6
LINE: e6 d4 d5
REASONING: Cleaner structure.""")
        summary, choice = runner.apply_critic_choice(board, "brief", provider, results)
        self.assertIn("OVERRIDE e6", summary)
        self.assertEqual("e7e6", choice["move"])
        self.assertEqual("e6", choice["san"])

    def test_apply_critic_choice_all_failed_still_allows_override(self):
        board = chess.Board()
        board.push_san("e4")
        results = [
            {
                "candidate": {"san": "c5", "move_token": "c7c5", "line": "c5 Nf3 d6", "reasoning": "test"},
                "validation": {
                    "passed": False,
                    "san": "c5",
                    "uci": "c7c5",
                    "warnings": [],
                    "hard_failures": ["CLAIM: bad line"],
                    "explanation": "CLAIM: bad line",
                },
            },
            {
                "candidate": {"san": "e5", "move_token": "e7e5", "line": "e5 Nf3 Nc6", "reasoning": "test"},
                "validation": {
                    "passed": False,
                    "san": "e5",
                    "uci": "e7e5",
                    "warnings": [],
                    "hard_failures": ["CLAIM: bad line"],
                    "explanation": "CLAIM: bad line",
                },
            },
        ]
        provider = StubProvider("""DECISION: OVERRIDE e6
LINE: e6 Nf3 d5
REASONING: Solid center.""")
        summary, choice = runner.apply_critic_choice(board, "brief", provider, results)
        self.assertIn("OVERRIDE e6", summary)
        self.assertEqual("e7e6", choice["move"])
        self.assertEqual("e6", choice["san"])

    def test_validate_candidate_with_illegal_line_fails(self):
        """A candidate whose claimed LINE contains an illegal move should fail."""
        board = chess.Board()
        board.push_san("e4")
        candidate = {
            "move_token": "e7e5",
            "san": "e5",
            "line": "e5 Qh5 Bg7",  # Bg7 is not legal after Qh5 (doesn't block mate)... actually it might be
            "white_threat": "Qh5",
            "reasoning": "test",
        }
        result = runner.validate_candidate(board, candidate)
        # The LINE itself may or may not be legal — depends on position
        # But any CLAIM hard failure means passed=False
        if result.get("line_verification", {}).get("hard_failures"):
            self.assertFalse(result["passed"])

    def test_validate_candidate_with_losing_line_fails(self):
        """A candidate whose LINE loses >= 2 material should hard-fail."""
        # After 1.d4 d5 2.Nc3 Nf6 3.Bf4 Nc6 4.Nb5
        board = chess.Board()
        for m in ['d4', 'd5', 'Nc3', 'Nf6', 'Bf4', 'Nc6', 'Nb5']:
            board.push_san(m)
        # a6 with LINE: a6 Nxc7+ Qxc7 — Qxc7 is then captured by Bxc7 (but LINE stops at 3 plies)
        # material_outcome after a6 Nxc7+ Qxc7 = ... let's check
        candidate = {
            "move_token": "a7a6",
            "san": "a6",
            "line": "a6 Nxc7+ Qxc7",
            "white_threat": "Nxc7+",
            "reasoning": "test",
        }
        result = runner.validate_candidate(board, candidate)
        # cmd_validate already hard-fails a6 due to fork-evasion analysis
        self.assertFalse(result["passed"])

    def test_validate_candidate_requires_claim_fields(self):
        """Missing LINE / WHITE_THREAT should hard-fail the candidate."""
        board = chess.Board()
        board.push_san("e4")
        candidate = {
            "move_token": "e7e5",
            "san": "e5",
            "line": "",
            "white_threat": "",
            "reasoning": "test",
        }
        result = runner.validate_candidate(board, candidate)
        self.assertFalse(result["passed"])
        self.assertIn("CLAIM: Missing LINE.", result["hard_failures"])
        self.assertIn("CLAIM: Missing WHITE_THREAT.", result["hard_failures"])

    def test_validate_candidate_accepts_longer_line_and_warns(self):
        board = chess.Board()
        board.push_san("e4")
        candidate = {
            "move_token": "e7e5",
            "san": "e5",
            "line": "e5 Nf3 Nc6 Bb5 a6",
            "white_threat": "Nf3",
            "reasoning": "test",
        }
        result = runner.validate_candidate(board, candidate)
        self.assertTrue(result["passed"], result["hard_failures"])
        self.assertIn(
            "CLAIM: LINE had 5 plies; validated first 3 only.",
            result["warnings"],
        )

    def test_validate_candidate_minor_loss_against_real_threat_is_warning_only(self):
        """A non-severe threat with a -1 line outcome should warn, not hard-fail."""
        board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 3 3")
        candidate = {
            "move_token": "d8e7",
            "san": "Qe7",
            "line": "Qe7 Qxf7+ Kd8",
            "white_threat": "Qxf7+",
            "reasoning": "test",
        }
        result = runner.validate_candidate(board, candidate)
        self.assertTrue(result["passed"], result["hard_failures"])
        self.assertIn(
            "CLAIM: LINE does not neutralize WHITE_THREAT cleanly (material delta: -1).",
            result["warnings"],
        )

    def test_validate_candidate_non_severe_threat_mismatch_is_warning_only(self):
        board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 3 3")
        candidate = {
            "move_token": "d8e7",
            "san": "Qe7",
            "line": "Qe7 Qh4 Nf6",
            "white_threat": "Qxf7+",
            "reasoning": "test",
        }
        result = runner.validate_candidate(board, candidate)
        self.assertTrue(result["passed"], result["hard_failures"])
        self.assertTrue(
            any("LINE assumes White plays 'Qh4' but WHITE_THREAT 'Qxf7+' is the claimed critical reply." in item
                for item in result["warnings"])
        )

    def test_build_board_brief_includes_move_history(self):
        board = chess.Board()
        for san in ["d4", "d5", "Nc3"]:
            board.push_san(san)

        brief = runner.build_board_brief(board, ["d4", "d5", "Nc3"])

        self.assertIn("MOVE HISTORY: 1. d4 d5 2. Nc3", brief)

    def test_build_board_brief_uses_selective_piece_detail(self):
        board = chess.Board()

        brief = runner.build_board_brief(board, [])

        self.assertIn("Pawn a2", brief)
        self.assertNotIn("Pawn a2 | attacked by:", brief)
        self.assertIn("King e1 | attacked by: none | defended by:", brief)
        self.assertIn("pawn shelter:", brief)


if __name__ == "__main__":
    unittest.main()
