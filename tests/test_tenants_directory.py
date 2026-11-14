import os
import tempfile
import unittest
from unittest import mock

from feiyu_memgate import config, registry
from feiyu_memgate.tenants.directory import DirectoryTenants


class DirectoryTenantsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "tenants")

    def make(self, **options):
        provider = DirectoryTenants(self.dir, **options)
        self.addCleanup(provider.close)  # before the temp dir goes (LIFO)
        return provider

    def test_valid_and_invalid_ids(self):
        p = self.make()
        for good in ("g1", "12345", "A_b-c", "x" * 64):
            self.assertTrue(p.valid_id(good), good)
        bad = ["", "x" * 65, "..", ".", ".hidden", "a/b", "a\\b", "../x", "..\\x", "a b", "a\n", "a\x00b",
               "con", "CON", "nul", "Aux", "com1", "LPT9", "a.", "a ", None, 5, b"abc", "a:b"]
        for item in bad:
            self.assertFalse(p.valid_id(item), repr(item))

    def test_strict_even_with_permissive_pattern(self):
        p = self.make(id_pattern=r".*")
        for item in ("..", "../x", "a/b", "a\\b", ".x", "a\x00", "NUL", "com2", "con.txt", "trail.", "trail ", "a:b"):
            self.assertFalse(p.valid_id(item), repr(item))
            self.assertIsNone(p.store(item))
        self.assertTrue(p.valid_id("ok.name"))

    def test_custom_pattern_matches_whole(self):
        p = self.make(id_pattern=r"[0-9]{5,12}")
        self.assertTrue(p.valid_id("123456"))
        self.assertFalse(p.valid_id("1234"))
        self.assertFalse(p.valid_id("123456a"))
        self.assertFalse(p.valid_id("123456\n"))

    def test_create_off_and_on(self):
        off = self.make()
        self.assertIsNone(off.store("g1"))
        self.assertFalse(os.path.exists(self.dir))
        on = self.make(create=True)
        self.assertIsNotNone(on.store("g1"))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "g1.db")))
        self.assertIsNone(on.store("../escape"))
        self.assertEqual(os.listdir(self._tmp.name), ["tenants"])

    def test_existing_file_is_served_without_create(self):
        first = self.make()
        first.add("g1")
        first.close()
        fresh = self.make()
        self.assertIsNotNone(fresh.store("g1"))
        self.assertIsNone(fresh.store("g2"))

    def test_caching(self):
        p = self.make(create=True)
        self.assertIs(p.store("g1"), p.store("g1"))
        self.assertIsNot(p.store("g1"), p.store("g2"))

    def test_isolation(self):
        p = self.make(create=True)
        p.store("a").put("secret of tenant a about pineapples")
        p.store("b").put("note of tenant b about walnuts")
        self.assertEqual(p.store("b").find("pineapples", 5), [])
        self.assertEqual(len(p.store("a").find("pineapples", 5)), 1)

    def test_case_variant_cannot_open_other_tenant(self):
        p = self.make(create=True)
        p.store("abc").put("private")
        self.assertIsNone(p.store("ABC"))

    def test_add_remove_list(self):
        p = self.make()
        self.assertEqual(p.list(), [])
        p.add("b").put("one")
        p.add("a")
        self.assertEqual(p.list(), ["a", "b"])
        with self.assertRaises(ValueError):
            p.add("../x")
        self.assertTrue(p.remove("b"))
        self.assertFalse(p.remove("b"))
        self.assertFalse(p.remove("../x"))
        self.assertEqual(p.list(), ["a"])
        self.assertFalse([n for n in os.listdir(self.dir) if n.startswith("b.")])
        self.assertIsNone(p.store("b"))

    def test_list_ignores_foreign_files(self):
        p = self.make()
        p.add("a")
        for name in ("notes.txt", ".hidden.db", "bad name.db"):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write("x")
        self.assertEqual(p.list(), ["a"])

    def test_store_options(self):
        p = self.make(create=True, store={"roles": ["user", "assistant"], "path": "ignored.db"})
        be = p.store("g1")
        be.remember([{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}])
        self.assertEqual(be.count(), 2)
        self.assertFalse(os.path.exists("ignored.db"))

    def test_registry(self):
        p = registry.create("tenants", {"type": "directory", "path": self.dir, "create": True})
        self.addCleanup(p.close)
        self.assertIsInstance(p, DirectoryTenants)

    def test_end_to_end_through_gateway(self):
        cfg = {
            "tenants": {"group": {"type": "directory", "path": self.dir}},
            "clients": {"bot": {"token_env": "T", "tenants": "group", "write": "@tenant", "recall": ["@tenant"]}},
            "recall": {"stages": [{"type": "min_score", "value": 0.4}]},
        }
        gw = config.build_gateway(cfg, env={}, log=lambda line: None)
        self.addCleanup(lambda: [t.close() for t in gw.tenants.values()])
        provider = gw.tenants["group"]
        provider.add("g1")
        provider.add("g2")
        msg = {"messages": [{"role": "user", "content": "Our meetup is on Thursday evening"}]}
        self.assertEqual(gw.remember("bot", dict(msg, tenant_id="g1")).status, 200)
        mine = gw.recall("bot", {"query": "when is the meetup", "tenant_id": "g1"})
        self.assertEqual(mine.status, 200, mine.body)
        self.assertEqual([i["text"] for i in mine.body["results"]], ["Our meetup is on Thursday evening"])
        other = gw.recall("bot", {"query": "when is the meetup", "tenant_id": "g2"})
        self.assertEqual(other.status, 200)
        self.assertEqual(other.body["results"], [])
        for bad, status in (("unknown", 403), ("../g1", 400), ("g1/..", 400)):  # unknown_tenant / bad_tenant
            self.assertEqual(gw.recall("bot", {"query": "meetup", "tenant_id": bad}).status, status, bad)
            self.assertEqual(gw.remember("bot", dict(msg, tenant_id=bad)).status, status, bad)


class DirectoryTenantsBoundsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "tenants")

    def make(self, **options):
        provider = DirectoryTenants(self.dir, **options)
        self.addCleanup(provider.close)
        return provider

    def test_open_stores_are_bounded_and_evicted_ones_reopen(self):
        p = self.make(create=True, max_open=3)
        for i in range(12):
            p.store("t%d" % i).put("memory of tenant %d about quince" % i, ref="r")
            self.assertLessEqual(len(p._backends), 3)
        self.assertEqual(len(p._backends), 3)
        evicted = p.store("t0")  # closed earlier, comes back with its data
        self.assertEqual(evicted.read("r"), "memory of tenant 0 about quince")
        self.assertEqual(len(p._backends), 3)
        self.assertEqual(len(p.list()), 12)

    def test_least_recently_used_is_the_one_closed(self):
        p = self.make(create=True, max_open=2)
        a, b = p.store("a"), p.store("b")
        p.store("a")  # a is now the most recent
        p.store("c")  # evicts b
        self.assertIs(p.store("a"), a)
        self.assertIsNot(p.store("b"), b)
        self.assertFalse(b.health())  # b was closed

    def test_a_flood_of_rotating_ids_opens_at_most_max_open_connections(self):
        p = self.make(create=True, max_open=8, max_tenants=10000)
        opened = []
        real_open = DirectoryTenants._open

        def counting(provider, tenant_id):
            backend = real_open(provider, tenant_id)
            opened.append(backend)
            return backend

        with mock.patch.object(DirectoryTenants, "_open", counting):
            for i in range(300):
                self.assertIsNotNone(p.store("flood%d" % i))
        live = [b for b in opened if b.health()]
        self.assertLessEqual(len(live), 8)

    def test_max_tenants_stops_creation(self):
        p = self.make(create=True, max_tenants=3)
        for i in range(3):
            self.assertIsNotNone(p.store("t%d" % i))
        self.assertIsNone(p.store("t3"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "t3.db")))
        self.assertIsNotNone(p.store("t1"))  # the existing ones are still served
        fresh = self.make(create=True, max_tenants=3)  # the limit counts files, not open stores
        self.assertIsNone(fresh.store("t9"))
        self.assertIsNotNone(fresh.store("t2"))

    def test_no_directory_listing_per_request(self):
        p = self.make(create=True)
        p.store("known")
        listed = []
        real = os.listdir
        with mock.patch("feiyu_memgate.tenants.directory.os.listdir", lambda *a: listed.append(a) or real(*a)):
            for i in range(200):
                p.store("known")
            off = self.make()  # create off: unknown ids are a stat each
            for i in range(200):
                self.assertIsNone(off.store("nobody%d" % i))
            for i in range(200):
                p.store("fresh%d" % i)  # creating: the names are cached and kept current
            for i in range(50):
                self.assertIsNone(p.store("KNOWN"))  # a case variant: refused without listing
        self.assertLessEqual(len(listed), 5)

    def test_case_variant_stays_refused_after_the_store_was_evicted(self):
        p = self.make(create=True, max_open=1)
        p.store("abc").put("private")
        p.store("other")  # evicts abc
        self.assertIsNone(p.store("ABC"))
        self.assertIsNone(p.store("Abc"))
        self.assertEqual(p.store("abc").read(next(iter(p.store("abc").refs()))[0]), "private")

    def test_a_file_made_by_someone_else_is_found(self):
        p = self.make()
        self.assertIsNone(p.store("late"))
        other = self.make()
        other.add("late")
        other.close()
        self.assertIsNotNone(p.store("late"))

    def test_remove_keeps_the_cache_right(self):
        p = self.make(create=True, max_tenants=2)
        p.store("a")
        p.store("b")
        self.assertIsNone(p.store("c"))
        self.assertTrue(p.remove("a"))
        self.assertIsNotNone(p.store("c"))  # room again

    def test_store_type_other_than_sqlite_is_an_error(self):
        with self.assertRaises(ValueError):
            DirectoryTenants(self.dir, store={"type": "openviking", "url": "http://x"})
        with self.assertRaises(ValueError):
            DirectoryTenants(self.dir, store={"type": "inmemory"})
        with self.assertRaises(ValueError):
            DirectoryTenants(self.dir, store="inmemory")
        self.make(store={"type": "sqlite", "roles": ["user"]})  # fine
        self.make(store={"roles": ["user"]})  # absent type is sqlite
        with self.assertRaises(ValueError):
            registry.create("tenants", {"type": "directory", "path": self.dir, "store": {"type": "nope"}})


if __name__ == "__main__":
    unittest.main()
