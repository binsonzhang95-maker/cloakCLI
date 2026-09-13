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
        self.assertEqual(a.screenshot_id, "obs-001")

    def test_click_coords_require_screenshot_id(self):
        with self.assertRaises(ActionError) as ctx:
            validate_action({"action": "click", "x": 10, "y": 20})
        self.assertIn("screenshot_id", str(ctx.exception))

    def test_click_coords_blank_screenshot_id_rejected(self):
        with self.assertRaises(ActionError):
            validate_action({"action": "click", "x": 10, "y": 20, "screenshot_id": "  "})

    def test_fill_kept_on_whitelist(self):
        a = validate_action({"action": "fill", "css": "#user", "text": "alice"})
        self.assertEqual(a.type, "fill")
        self.assertEqual(a.text, "alice")

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

    def test_press_and_select_on_form_whitelist(self):
        p = validate_action({"action": "press", "key": "Enter"})
        self.assertEqual(p.type, "press")
        self.assertEqual(p.key, "Enter")
        s = validate_action({"action": "select", "css": "select#n", "value": "CA"})
        self.assertEqual(s.type, "select")
        self.assertEqual(s.value, "CA")

    def test_press_rejects_unknown_key(self):
        with self.assertRaises(ActionError):
            validate_action({"action": "press", "key": "Meta+Alt+F4"})

    def test_large_scroll_rejected(self):
        with self.assertRaises(ActionError):
            validate_action({"action": "scroll", "delta_y": 20000})
