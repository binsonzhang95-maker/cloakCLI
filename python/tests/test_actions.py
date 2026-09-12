import unittest

from cloakcli_worker.recover.actions import (
    ActionError,
    parse_model_output,
    validate_action,
)


class ActionSchemaTests(unittest.TestCase):
    def test_click_css_ok(self):
        a = validate_action({"schema_version": 1, "action": "click", "css": "a"})
        self.assertEqual(a.type, "click")
        self.assertEqual(a.css, "a")

    def test_click_coords_ok(self):
        a = validate_action({"action": "click", "x": 10, "y": 20, "screenshot_id": "obs-001"})
        self.assertEqual((a.x, a.y), (10, 20))

    def test_unknown_action_rejected(self):
        with self.assertRaises(ActionError):
            validate_action({"action": "explode"})

    def test_forbidden_shell_and_file(self):
        for name in ("shell", "exec", "read_file", "evaluate", "python", "bash"):
            with self.assertRaises(ActionError, msg=name):
                validate_action({"action": name, "cmd": "id"})

    def test_huge_text_rejected(self):
        with self.assertRaises(ActionError):
            validate_action({"action": "type", "css": "input", "text": "x" * 5000})

    def test_parse_json_and_fence(self):
        r = parse_model_output('```json\n{"action":"done","reason":"ok"}\n```')
        self.assertEqual(len(r.actions), 1)
        self.assertEqual(r.actions[0].type, "done")

    def test_parse_actions_array(self):
        r = parse_model_output(
            '{"schema_version":1,"actions":[{"action":"wait","ms":10},{"action":"done","reason":"x"}]}'
        )
        self.assertEqual([a.type for a in r.actions], ["wait", "done"])

    def test_click_requires_target(self):
        with self.assertRaises(ActionError):
            validate_action({"action": "click"})
