import unittest

from feiyu_memgate import registry
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.errors import ConfigError


class PathPiece:
    """Loaded by dotted path in the tests below: nothing registers it."""

    def __init__(self, size=1):
        self.size = size


class RegistryTests(unittest.TestCase):
    def register(self, kind, name, factory):
        registry.register(kind, name)(factory)
        self.addCleanup(lambda: registry._pieces[kind].pop(name, None))
        return factory

    def test_register_then_resolve_and_create(self):
        class Mine:
            def __init__(self, level=1):
                self.level = level

        self.register("backend", "test-mine", Mine)
        self.assertIs(registry.resolve("backend", "test-mine"), Mine)
        self.assertEqual(registry.create("backend", "test-mine").level, 1)
        self.assertEqual(registry.create("backend", {"type": "test-mine", "level": 4}).level, 4)

    def test_register_works_as_a_decorator_and_returns_the_class(self):
        @registry.register("write_guard", "test-deco")
        class Guard:
            pass

        self.addCleanup(lambda: registry._pieces["write_guard"].pop("test-deco", None))
        self.assertTrue(isinstance(Guard(), Guard))
        self.assertIs(registry.resolve("write_guard", "test-deco"), Guard)

    def test_pieces_of_different_kinds_do_not_clash(self):
        self.register("backend", "test-same", dict)
        self.register("recall_stage", "test-same", list)
        self.assertIs(registry.resolve("backend", "test-same"), dict)
        self.assertIs(registry.resolve("recall_stage", "test-same"), list)

    def test_extra_options_win_over_the_spec(self):
        self.register("backend", "test-extra", lambda level=1: level)
        self.assertEqual(registry.create("backend", {"type": "test-extra", "level": 1}, level=9), 9)

    def test_dotted_path_piece(self):
        spec = {"type": __name__ + ":PathPiece", "size": 3}
        piece = registry.create("backend", spec)
        self.assertIsInstance(piece, PathPiece)
        self.assertEqual(piece.size, 3)
        self.assertIs(registry.resolve("recall_stage", __name__ + ":PathPiece"), PathPiece)

    def test_dotted_path_errors_are_config_errors(self):
        with self.assertRaises(ConfigError) as ctx:
            registry.resolve("backend", "no_such_module_feiyu:Thing")
        self.assertIn("no_such_module_feiyu", str(ctx.exception))
        with self.assertRaises(ConfigError) as ctx:
            registry.resolve("backend", __name__ + ":Missing")
        self.assertIn("Missing", str(ctx.exception))

    def test_unknown_kind_lists_the_known_kinds(self):
        for call in (lambda: registry.resolve("nonsense", "x"), lambda: registry.register("nonsense", "x"),
                     lambda: registry.available("nonsense")):
            with self.assertRaises(ConfigError) as ctx:
                call()
            message = str(ctx.exception)
            self.assertIn("nonsense", message)
            for kind in registry.KINDS:
                self.assertIn(kind, message)

    def test_unknown_name_lists_the_known_names(self):
        with self.assertRaises(ConfigError) as ctx:
            registry.create("backend", "no-such-backend")
        message = str(ctx.exception)
        self.assertIn("no-such-backend", message)
        self.assertIn("inmemory", message)
        self.assertIn("openviking", message)

    def test_missing_type_is_a_config_error(self):
        for spec in ({}, {"type": ""}, {"type": 5}):
            with self.assertRaises(ConfigError):
                registry.create("backend", spec)
        with self.assertRaises(ConfigError):
            registry.create("backend", 42)

    def test_bad_names_cannot_be_registered(self):
        for name in ("", "a:b", None, 3):
            with self.assertRaises(ConfigError):
                registry.register("backend", name)

    def test_a_name_taken_by_another_factory_is_refused(self):
        class First:
            pass

        class Second:
            pass

        self.register("recall_stage", "test-taken", First)
        registry.register("recall_stage", "test-taken")(First)  # the same factory again: harmless
        with self.assertRaises(ConfigError) as ctx:
            registry.register("recall_stage", "test-taken")(Second)
        self.assertIn("test-taken", str(ctx.exception))
        self.assertIs(registry.resolve("recall_stage", "test-taken"), First)
        with self.assertRaises(ConfigError):
            registry.register("backend", "inmemory")(Second)  # a built-in cannot be replaced either
        self.assertIs(registry.resolve("backend", "inmemory"), InMemoryBackend)

    def test_create_fills_in_the_registered_name_of_stages_and_guards(self):
        self.assertEqual(registry.create("recall_stage", "dedupe").name, "dedupe")
        self.assertEqual(registry.create("write_guard", {"type": "daily_cap"}).name, "daily_cap")
        by_path = registry.create("recall_stage", "feiyu_memgate.core.stages:Dedupe")
        self.assertIsNone(by_path.name)  # a dotted path: the class name is used where a name is shown
        self.assertFalse(hasattr(registry.create("backend", "inmemory"), "name"))

    def test_the_pieces_module_offers_everything_a_piece_author_needs(self):
        from feiyu_memgate import pieces
        names = ("MemoryBackend", "RecallStage", "WriteGuard", "TenantProvider", "ForgettingCurve", "KEEP", "DROP",
                 "DEFER", "RecallContext", "WriteContext", "Hit", "Candidate", "ClientPolicy", "BackendError",
                 "ConfigError", "WriteRejected", "register", "NULL_CTL")
        for name in names:
            self.assertTrue(hasattr(pieces, name), name)
        self.assertEqual(set(pieces.__all__), set(names))
        self.assertIs(pieces.register, registry.register)
        self.assertFalse(pieces.NULL_CTL.cancelled)

    def test_options_type_error_becomes_a_config_error_without_values(self):
        with self.assertRaises(ConfigError) as ctx:
            registry.create("backend", {"type": "inmemory", "bogus": "VALUE-THAT-MUST-NOT-LEAK"})
        message = str(ctx.exception)
        self.assertIn("bogus", message)
        self.assertNotIn("VALUE-THAT-MUST-NOT-LEAK", message)
        with self.assertRaises(ConfigError) as ctx:
            registry.create("backend", {"type": "openviking", "url": "http://127.0.0.1:1"})  # key is missing
        self.assertIn("key", str(ctx.exception))

    def test_a_factory_that_raises_something_else_is_not_swallowed(self):
        with self.assertRaises(ValueError):
            registry.create("backend", {"type": "openviking", "url": "ftp://x", "key": "k"})

    def test_available_lists_the_builtins(self):
        self.assertIn("inmemory", registry.available("backend"))
        self.assertIn("openviking", registry.available("backend"))
        self.assertIs(registry.available("backend")["inmemory"], InMemoryBackend)
        stages = set(registry.available("recall_stage"))
        self.assertTrue({"min_score", "source_weight", "drop_marker", "fill_text", "drop_oversize", "redact",
                         "dedupe", "budget"} <= stages)
        self.assertTrue({"redact", "daily_cap"} <= set(registry.available("write_guard")))
        self.assertTrue({"secrets", "public", "none"} <= set(registry.available("redactor")))
        self.assertIn("keyfile", registry.available("tenants"))

    def test_available_is_a_copy(self):
        registry.available("backend").clear()
        self.assertIn("inmemory", registry.available("backend"))

    def test_installed_returns_a_list_of_names(self):
        self.assertIsInstance(registry.installed("backend"), list)


if __name__ == "__main__":
    unittest.main()
