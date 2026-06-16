from types import SimpleNamespace

from django.test import SimpleTestCase

from appointments.services.intake_service import order_questions_with_subquestions


def _q(qid):
    """Lightweight stand-in for a DoctorIntakeQuestion (only .id is used)."""
    return SimpleNamespace(id=qid)


def _rule(source, target, action="SHOW"):
    return {
        "source_question_id": source,
        "target_question_id": target,
        "action": action,
        "operator": "EQUALS",
        "expected_value": "نعم",
    }


class OrderQuestionsWithSubquestionsTests(SimpleTestCase):
    """The display reorder that pulls SHOW-rule sub-questions under their parent."""

    def _ids(self, questions, rules):
        return [q.id for q in order_questions_with_subquestions(questions, rules)]

    def test_subquestion_at_end_moves_under_parent(self):
        # Q1 reveals Q3, but Q3's `order` puts it last. It should follow Q1.
        questions = [_q(1), _q(2), _q(3)]
        rules = [_rule(1, 3)]
        self.assertEqual(self._ids(questions, rules), [1, 3, 2])

    def test_no_rules_preserves_original_order(self):
        questions = [_q(1), _q(2), _q(3)]
        self.assertEqual(self._ids(questions, []), [1, 2, 3])

    def test_hide_rules_do_not_reparent(self):
        questions = [_q(1), _q(2), _q(3)]
        rules = [_rule(1, 3, action="HIDE")]
        self.assertEqual(self._ids(questions, rules), [1, 2, 3])

    def test_multiple_children_keep_sibling_order(self):
        # Q1 reveals both Q3 and Q4 (Q4 listed before Q3 in original order).
        questions = [_q(1), _q(2), _q(3), _q(4)]
        rules = [_rule(1, 4), _rule(1, 3)]
        self.assertEqual(self._ids(questions, rules), [1, 4, 3, 2])

    def test_nested_subquestions_follow_depth_first(self):
        # Q1 -> Q2 -> Q3 chain. Q3 starts last; whole chain should be contiguous.
        questions = [_q(1), _q(2), _q(3), _q(4)]
        rules = [_rule(1, 2), _rule(2, 3)]
        self.assertEqual(self._ids(questions, rules), [1, 2, 3, 4])

    def test_every_question_emitted_exactly_once(self):
        questions = [_q(1), _q(2), _q(3)]
        rules = [_rule(1, 2), _rule(1, 3)]
        result = order_questions_with_subquestions(questions, rules)
        self.assertCountEqual([q.id for q in result], [1, 2, 3])

    def test_cycle_is_guarded(self):
        # Q1 -> Q2 and Q2 -> Q1 (a cycle). Must terminate and emit each once.
        questions = [_q(1), _q(2)]
        rules = [_rule(1, 2), _rule(2, 1)]
        result = self._ids(questions, rules)
        self.assertCountEqual(result, [1, 2])

    def test_dangling_target_is_still_emitted(self):
        # Rule references a target id not in the question list; original questions intact.
        questions = [_q(1), _q(2)]
        rules = [_rule(1, 99)]
        self.assertEqual(self._ids(questions, rules), [1, 2])
