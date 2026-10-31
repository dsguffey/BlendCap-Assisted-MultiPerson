import unittest
from blendcap_vta_marks import CandidateMarks, ASSIGN_ICON, SUPPRESS_ICON


class CandidateMarksTests(unittest.TestCase):
    def setUp(self):
        self.marks = CandidateMarks()
        self.scope = ('scene', 'project', 'source')

    def mark(self, kind, frame, observation):
        return self.marks.toggle(self.scope, kind, frame, observation, 'revision', 'a'*64)

    def test_assignment_replaces_same_frame_and_collects_other_frames(self):
        self.mark('assign', 1, 'a'); self.mark('assign', 2, 'b'); self.mark('assign', 1, 'c')
        self.assertEqual([v['observation_id'] for v in self.marks.items(self.scope, 'assign')], ['c', 'b'])

    def test_suppression_allows_many_on_same_frame(self):
        self.mark('suppress', 1, 'a'); self.mark('suppress', 1, 'b')
        self.assertEqual(len(self.marks.items(self.scope, 'suppress')), 2)
        self.assertNotEqual(ASSIGN_ICON, SUPPRESS_ICON)

    def test_toggle_and_cross_intent_are_unambiguous(self):
        self.mark('assign', 1, 'a'); self.mark('suppress', 1, 'a')
        self.assertFalse(self.marks.items(self.scope, 'assign'))
        self.assertFalse(self.mark('suppress', 1, 'a'))

    def test_clear_scope_and_success_preserve_unhandled_marks(self):
        self.mark('assign', 1, 'a'); submitted=self.marks.items(self.scope, 'assign')
        self.mark('assign', 1, 'b'); self.marks.handled(self.scope, 'assign', submitted)
        self.assertEqual(self.marks.items(self.scope, 'assign')[0]['observation_id'], 'b')
        self.assertFalse(self.marks.items(('other-scene',), 'assign'))
        self.marks.clear(); self.assertFalse(self.marks.items(self.scope, 'assign'))
