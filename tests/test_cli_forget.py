"""The `feiyu-memgate forget ...` commands and the forgetting section of `check`, on temporary
configurations with sqlite stores and a fake clock (cli.CLOCK, a seam that is not a user option)."""
import calendar
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from feiyu_memgate import cli, config, registry
from feiyu_memgate.forgetting.engine import date_to_epoch
from feiyu_memgate.forgetting.ledger import Ledger

DAY = 86400.0
NOW = float(calendar.timegm((2026, 11, 14, 0, 0, 0)))
TOKEN = "tok-owner-fake-0123456789abcdef"

# Memory texts carry a marker word: it must never appear in output unless --show-text is given.
TEXTS = {
    "old": ("SECRET-OLD 很久以前的一件事", "2025-01-01"),       # never used, 682 days old: due
    "mid": ("SECRET-MID 几个月前的一件事", "2026-07-17"),       # 120 days: demoted (R = 0.25)
    "new": ("SECRET-NEW 上周的一件事", "2026-11-07"),           # 7 days: active
    "keep/profile": ("SECRET-KEEP 身份档案", "2025-01-01"),     # old but matches pin_refs
}


def run(*argv, now=NOW):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
            mock.patch.object(cli, "CLOCK", lambda: now), mock.patch.dict(os.environ, {"CLI_FORGET_TOKEN": TOKEN}):
        code = cli.main(list(argv))
    return code, out.getvalue()


def toml_path(path):
    return Path(path).as_posix()


class ForgetCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)

    def seed(self, name, texts):
        store = registry.create("backend", {"type": "sqlite", "path": str(self.dir / name)})
        for ref, (text, date) in texts.items():
            store.put(text, ref=ref, date=date)
        store.close()

    def manage(self, key, texts):
        """Put the memories in the ledger as if managed since each memory's own date (the lifetime lived)."""
        ledger = Ledger(str(self.dir / "ledger.db"), wall=lambda: NOW)
        try:
            for ref, (_, date) in texts.items():
                ledger.touch(key, [(ref, date_to_epoch(date))], date_to_epoch(date))
        finally:
            ledger.close()

    def refs(self, name):
        store = registry.create("backend", {"type": "sqlite", "path": str(self.dir / name)})
        try:
            return {ref for ref, _ in store.refs()}
        finally:
            store.close()

    def config(self, forgetting=True, extra="", stages='["min_score", "freshness"]'):
        text = """
[stores.private]
type = "sqlite"
path = "%s"

[clients.owner]
token_env = "CLI_FORGET_TOKEN"
recall = ["private"]
write = "private"

[recall]
stages = %s
""" % (toml_path(self.dir / "private.db"), stages if forgetting else '["min_score"]')
        if forgetting:
            text += """
[forgetting]
ledger = "%s"
archive = "%s"
stores = ["private"]
pin_refs = ["keep/*"]
%s
""" % (toml_path(self.dir / "ledger.db"), toml_path(self.dir / "forgotten.db"),
       ("" if "max_forget_fraction" in extra else "max_forget_fraction = 0.5\n")
       + ("" if "max_forget_min" in extra else "max_forget_min = 5\n") + extra)
            if "[forgetting.curve]" not in extra:  # the tests' numbers are for a 60-day half-life
                text += '\n[forgetting.curve]\ntype = "ebbinghaus"\nhalf_life_days = 60\n'
        path = self.dir / "gateway.toml"
        path.write_text(text, encoding="utf-8")
        return str(path)


class NotConfiguredTests(ForgetCase):
    def test_every_subcommand_explains_how_to_enable_and_exits_1(self):
        self.seed("private.db", TEXTS)
        path = self.config(forgetting=False)
        for argv in (("status",), ("sweep",), ("sweep", "--dry-run"), ("archive",), ("restore", "private", "old"),
                     ("pin", "private", "old"), ("unpin", "private", "old"), ("explain", "private", "old")):
            with self.subTest(argv=argv):
                code, text = run("forget", argv[0], "--config", path, *argv[1:])
                self.assertEqual(code, 1)
                self.assertIn("[forgetting]", text)
                self.assertIn("ledger", text)
        self.assertEqual(self.refs("private.db"), set(TEXTS))  # nothing was touched

    def test_check_has_no_forgetting_section_without_it(self):
        code, text = run("check", "--config", self.config(forgetting=False))
        self.assertEqual(code, 0)
        self.assertNotIn("遗忘：", text)

    def test_a_broken_config_is_a_config_error(self):
        code, text = run("forget", "status", "--config", str(self.dir / "nope.toml"))
        self.assertEqual(code, 1)
        self.assertIn("配置有问题：", text)


class StatusAndSweepTests(ForgetCase):
    def setUp(self):
        super().setUp()
        self.seed("private.db", TEXTS)
        self.manage("private", TEXTS)
        self.path = self.config()

    def test_status_counts_each_state_and_shows_the_settings(self):
        code, text = run("forget", "status", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("private：共 4，钉住 1，正常 1，降权 1，到期 1", text)
        self.assertIn("存档共 0 条", text)
        self.assertIn("降权线 R<0.3（按排序用的保持率，只用于报告），遗忘线 R<0.05（按删除用的保持率），存档保留 90 天，每次每个存储最多忘 max(5, 50%×条数) 条", text)
        self.assertIn("基础半衰期 60 天", text)
        self.assertNotIn("SECRET", text)

    def test_dry_run_changes_nothing_anywhere(self):
        code, text = run("forget", "sweep", "--dry-run", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("演习", text)
        self.assertIn("到期（会被遗忘）1", text)
        self.assertEqual(self.refs("private.db"), set(TEXTS))
        _, archive = run("forget", "archive", "--config", self.path)
        self.assertIn("共 0 条", archive)
        # The ledger was not touched either: the dry run only reads (the four rows are the ones seeded).
        with contextlib.closing(sqlite3.connect(str(self.dir / "ledger.db"))) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0], 4)

    def test_sweep_archives_then_deletes_the_due_memory_and_prints_no_text(self):
        code, text = run("forget", "sweep", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("private：共 4，钉住 1，正常 1，降权 1，已遗忘 1，失败 0", text)
        self.assertNotIn("SECRET", text)
        self.assertEqual(self.refs("private.db"), {"mid", "new", "keep/profile"})
        _, status = run("forget", "status", "--config", self.path)
        self.assertIn("存档 1", status)
        self.assertIn("存档共 1 条", status)

    def test_a_second_sweep_has_nothing_left_to_forget(self):
        run("forget", "sweep", "--config", self.path)
        code, text = run("forget", "sweep", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("已遗忘 0", text)

    def test_archive_entries_expire_and_are_purged(self):
        run("forget", "sweep", "--config", self.path)
        code, text = run("forget", "sweep", "--config", self.path, now=NOW + 91 * DAY)
        self.assertEqual(code, 0)
        self.assertIn("过期并真正删掉的：1 条", text)


class ArchiveRestoreTests(ForgetCase):
    def setUp(self):
        super().setUp()
        self.seed("private.db", TEXTS)
        self.manage("private", TEXTS)
        self.path = self.config()
        run("forget", "sweep", "--config", self.path)

    def test_archive_lists_addresses_and_dates_but_not_text(self):
        code, text = run("forget", "archive", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("private  old  遗忘于 2026-11-14  到期 2027-02-12  用过 0 次", text)
        self.assertIn("共 1 条", text)
        self.assertNotIn("SECRET", text)

    def test_show_text_adds_the_text(self):
        code, text = run("forget", "archive", "--show-text", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("SECRET-OLD", text)

    def test_store_filter(self):
        _, text = run("forget", "archive", "--store", "private", "--config", self.path)
        self.assertIn("共 1 条", text)
        _, text = run("forget", "archive", "--store", "other", "--config", self.path)
        self.assertIn("共 0 条", text)

    def test_restore_writes_the_memory_back_and_is_not_forgotten_again(self):
        code, text = run("forget", "restore", "private", "old", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("已恢复 private old", text)
        self.assertNotIn("SECRET", text)
        self.assertIn("old", self.refs("private.db"))
        _, archive = run("forget", "archive", "--config", self.path)
        self.assertIn("共 0 条", archive)
        _, sweep = run("forget", "sweep", "--config", self.path)
        self.assertIn("已遗忘 0", sweep)
        self.assertIn("old", self.refs("private.db"))
        store = registry.create("backend", {"type": "sqlite", "path": str(self.dir / "private.db")})
        try:
            self.assertEqual(store.read("old"), TEXTS["old"][0])  # the full text and the address are back
        finally:
            store.close()

    def test_restore_of_something_not_archived_or_in_an_unmanaged_store(self):
        code, text = run("forget", "restore", "private", "never-forgotten", "--config", self.path)
        self.assertEqual(code, 1)
        self.assertIn("存档里没有", text)
        code, text = run("forget", "restore", "nowhere", "old", "--config", self.path)
        self.assertEqual(code, 1)
        self.assertIn("没有这个被管理的存储", text)


class PinTests(ForgetCase):
    def setUp(self):
        super().setUp()
        self.seed("private.db", TEXTS)
        self.manage("private", TEXTS)
        self.path = self.config()

    def test_a_pinned_memory_survives_a_sweep_and_unpin_lets_it_go(self):
        code, text = run("forget", "pin", "private", "old", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("已钉住 1 条", text)
        _, status = run("forget", "status", "--config", self.path)
        self.assertIn("钉住 2", status)
        run("forget", "sweep", "--config", self.path)
        self.assertIn("old", self.refs("private.db"))
        code, text = run("forget", "unpin", "private", "old", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("已取消钉住 1 条", text)
        run("forget", "sweep", "--config", self.path)
        self.assertNotIn("old", self.refs("private.db"))

    def test_pin_refuses_an_address_the_store_does_not_hold(self):
        code, text = run("forget", "pin", "private", "old", "ghost", "--config", self.path)
        self.assertEqual(code, 1)
        self.assertIn("不在存储里，没有钉：ghost", text)
        self.assertIn("已钉住 1 条", text)

    def test_unpin_says_when_there_was_nothing_to_unpin_and_when_a_rule_still_holds(self):
        code, text = run("forget", "unpin", "private", "mid", "keep/profile", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("本来就没有手动钉住：mid", text)
        self.assertIn("仍然匹配 pin_refs 规则", text)
        self.assertIn("已取消钉住 0 条", text)

    def test_unknown_store(self):
        for action in ("pin", "unpin"):
            code, text = run("forget", action, "nowhere", "old", "--config", self.path)
            self.assertEqual(code, 1)
            self.assertIn("没有这个被管理的存储", text)


class ExplainTests(ForgetCase):
    def setUp(self):
        super().setUp()
        self.seed("private.db", TEXTS)
        self.manage("private", TEXTS)
        self.path = self.config()

    def test_active_memory_shows_when_it_will_cross_the_lines(self):
        code, text = run("forget", "explain", "private", "new", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("状态：正常", text)
        self.assertIn("排序用的保持率：0.922（距最近用到或记忆日期 7.0 天）", text)
        self.assertIn("删除用的保持率：0.922（距最近用到或开始被管理 7.0 天）", text)
        # age 7 days, half-life 60: R < 0.3 at age 104.2 days, i.e. 98 days from now
        self.assertIn("越过降权线（排序 R<0.3）：98 天后（2027-02-20）", text)
        # R < 0.05 at age 259.3 days, i.e. 253 days from now
        self.assertIn("最早可以被删除（删除 R<0.05）：253 天后（2027-07-25）", text)
        self.assertNotIn("SECRET", text)

    def test_demoted_memory_has_crossed_one_line_only(self):
        _, text = run("forget", "explain", "private", "mid", "--config", self.path)
        self.assertIn("状态：降权", text)
        self.assertIn("越过降权线（排序 R<0.3）：已经越过", text)
        self.assertIn("最早可以被删除（删除 R<0.05）：140 天后", text)

    def test_due_and_pinned(self):
        _, text = run("forget", "explain", "private", "old", "--config", self.path)
        self.assertIn("状态：到期", text)
        _, text = run("forget", "explain", "private", "keep/profile", "--config", self.path)
        self.assertIn("钉住（不降权、不遗忘）", text)
        self.assertNotIn("如果以后再没被用到", text)

    def test_what_the_ledger_knows(self):
        run("forget", "sweep", "--config", self.path)  # fills the ledger from the store's own dates
        run("forget", "pin", "private", "new", "--config", self.path)
        _, text = run("forget", "explain", "private", "new", "--config", self.path)
        self.assertIn("账本：记忆日期起点 2026-11-07，开始被管理 2026-11-07，最近用到 从没用到过，用过 0 次，手动钉住 是", text)

    def test_an_archived_memory_and_an_unknown_one(self):
        run("forget", "sweep", "--config", self.path)
        code, text = run("forget", "explain", "private", "old", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("已被遗忘，在存档里", text)
        self.assertNotIn("SECRET", text)
        code, text = run("forget", "explain", "private", "ghost", "--config", self.path)
        self.assertEqual(code, 1)
        self.assertIn("都没有", text)


class UsesAreCountedTests(ForgetCase):
    def test_a_recalled_memory_shows_its_use_in_explain(self):
        self.seed("private.db", {"new": TEXTS["new"]})
        path = self.config()
        gateway = config.build_gateway(config.load(path), env={"CLI_FORGET_TOKEN": TOKEN}, wall=lambda: NOW,
                                       log=lambda line: None)
        try:
            reply = gateway.recall("owner", {"query": TEXTS["new"][0]})
            self.assertEqual(len(reply.body["results"]), 1)
        finally:
            gateway.close()
        _, text = run("forget", "explain", "private", "new", "--config", path)
        self.assertIn("最近用到 2026-11-14，用过 1 次", text)
        self.assertNotIn("SECRET", text)


class TenantTests(ForgetCase):
    def setUp(self):
        super().setUp()
        self.seed("private.db", {"new": TEXTS["new"]})
        (self.dir / "groups").mkdir()
        for tenant in ("100", "200"):
            self.seed("groups/%s.db" % tenant, {"old": TEXTS["old"], "new": TEXTS["new"]})
            self.manage("group:%s" % tenant, {"old": TEXTS["old"], "new": TEXTS["new"]})
        text = """
[stores.private]
type = "sqlite"
path = "%s"

[tenants.group]
type = "directory"
path = "%s"

[clients.bot]
token_env = "CLI_FORGET_TOKEN"
recall = ["private"]
write = "@tenant"
tenants = "group"

[recall]
stages = ["min_score", "freshness"]

[forgetting]
ledger = "%s"
archive = "%s"
stores = ["private", "group"]
max_forget_fraction = 1.0

[forgetting.curve]
type = "ebbinghaus"
half_life_days = 60
""" % (toml_path(self.dir / "private.db"), toml_path(self.dir / "groups"),
       toml_path(self.dir / "ledger.db"), toml_path(self.dir / "forgotten.db"))
        self.path = str(self.dir / "tenants.toml")
        Path(self.path).write_text(text, encoding="utf-8")

    def test_status_and_sweep_cover_every_tenant(self):
        _, text = run("forget", "status", "--config", self.path)
        for key in ("private", "group:100", "group:200"):
            self.assertIn("  %s：共" % key, text)
        _, text = run("forget", "sweep", "--config", self.path)
        self.assertEqual(text.count("已遗忘 1"), 2)
        self.assertNotIn("SECRET", text)

    def test_restore_and_pin_with_a_tenant_key(self):
        run("forget", "sweep", "--config", self.path)
        _, text = run("forget", "archive", "--store", "group:100", "--config", self.path)
        self.assertIn("group:100  old", text)
        code, _ = run("forget", "restore", "group:100", "old", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertEqual(self.refs("groups/100.db"), {"old", "new"})
        self.assertEqual(self.refs("groups/200.db"), {"new"})
        code, text = run("forget", "pin", "group:200", "new", "--config", self.path)
        self.assertEqual((code, "已钉住 1 条" in text), (0, True))
        code, _ = run("forget", "restore", "group:999", "old", "--config", self.path)
        self.assertEqual(code, 1)

    def test_check_names_the_tenants_provider(self):
        code, text = run("check", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("group（租户来源）：能列出租户", text)


class CheckSectionTests(ForgetCase):
    def test_check_lists_paths_thresholds_curve_and_capabilities(self):
        self.seed("private.db", TEXTS)
        path = self.config(extra='demote_below = 0.4\n[forgetting.curve]\ntype = "ebbinghaus"\n'
                                 'half_life_days = 90\nper_store = { private = 180 }')
        code, text = run("check", "--config", path)
        self.assertEqual(code, 0)
        self.assertIn("遗忘：", text)
        self.assertIn("ledger.db", text)
        self.assertIn("forgotten.db", text)
        self.assertIn("降权线 R<0.4，遗忘线 R<0.05，存档保留 90 天", text)
        self.assertIn("基础半衰期 90 天；按来源：private=180", text)
        self.assertIn("private（共享存储）：refs、delete、put 齐全；receipt_refs：有", text)
        self.assertIn("keep/*", text)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("提醒", text)  # freshness is in the stage list

    def test_check_warns_when_freshness_is_not_in_the_recall_stages(self):
        self.seed("private.db", TEXTS)
        path = self.config(stages='["min_score"]')
        _, text = run("check", "--config", path)
        self.assertIn("没有 freshness", text)

    def test_a_store_without_receipt_refs_is_marked(self):
        class Plain:  # has the three capabilities but not receipt_refs
            def refs(self):
                return iter(())

            def delete(self, ref):
                return True

            def put(self, text, ref=None, date=None):
                return ref

        gateway = mock.Mock()
        gateway.forgetting = mock.Mock(stores=("plain",), demote_below=0.3, forget_below=0.05, archive_days=90,
                                       pin_refs=(), curve=object(), max_forget_fraction=0.02, max_forget_min=5)
        gateway.sweep_every_hours = 0
        gateway.stages = []
        gateway.tenants = {}
        gateway.stores = {"plain": Plain()}
        text = "\n".join(cli.describe_forgetting(gateway, {"forgetting": {"ledger": "l.db", "archive": "a.db"}}))
        self.assertIn("receipt_refs：没有", text)
        self.assertIn("不定时（只手动）", text)


class CurveOfYourOwnTests(ForgetCase):
    def test_a_custom_curve_is_described_and_explained_by_bisection(self):
        class Linear:
            """Retention falls by 1 per `days` days."""

            def __init__(self, days=100):
                self.days = days

            def retention(self, age_days, uses, source):
                return max(1.0 - age_days / self.days, 0.0)

        registry.register("forgetting_curve", "test_linear_cli")(Linear)
        self.addCleanup(registry._pieces["forgetting_curve"].pop, "test_linear_cli", None)
        self.seed("private.db", {"new": TEXTS["new"]})
        path = self.config(extra='[forgetting.curve]\ntype = "test_linear_cli"\ndays = 100')
        _, text = run("forget", "status", "--config", path)
        self.assertIn("曲线：test_linear_cli", text)
        self.assertIn("days = 100", text)
        _, text = run("forget", "explain", "private", "new", "--config", path)
        # ranking age 7: R < 0.3 once age > 70 (63 more days; the line is crossed just after it, so 64).
        # The ledger does not know the memory yet, so its deletion age is 0: R < 0.05 once age > 95 (96 days)
        self.assertIn("64 天后", text)
        self.assertIn("96 天后", text)


class FirstDayTests(ForgetCase):
    """Turning forgetting on for an existing store: the first sweep deletes nothing."""

    def setUp(self):
        super().setUp()
        self.seed("private.db", TEXTS)  # no ledger yet: the ledger meets these memories now
        self.path = self.config()

    def test_the_first_sweep_forgets_nothing_and_the_old_ones_are_demoted(self):
        code, text = run("forget", "sweep", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("已遗忘 0", text)
        self.assertEqual(self.refs("private.db"), set(TEXTS))
        _, status = run("forget", "status", "--config", self.path)
        self.assertIn("private：共 4，钉住 1，正常 1，降权 2，到期 0", status)

    def test_explain_shows_both_ages_and_the_earliest_deletion(self):
        run("forget", "sweep", "--config", self.path)  # day 0 of management
        _, text = run("forget", "explain", "private", "old", "--config", self.path)
        self.assertIn("排序用的保持率：0.000（距最近用到或记忆日期 682.0 天）；状态：降权", text)
        self.assertIn("删除用的保持率：1.000（距最近用到或开始被管理 0.0 天）", text)
        # half-life 60: R < 0.05 after 259.3 days under management: 260 days from the sweep day
        self.assertIn("最早可以被删除（删除 R<0.05）：260 天后（2027-08-01）", text)
        _, later = run("forget", "explain", "private", "old", "--config", self.path, now=NOW + 100 * DAY)
        self.assertIn("距最近用到或开始被管理 100.0 天", later)
        self.assertIn("160 天后", later)

    def test_deleted_only_after_the_whole_lifetime_under_management(self):
        path = self.config(extra="max_forget_fraction = 1.0")
        run("forget", "sweep", "--config", path)
        _, text = run("forget", "sweep", "--config", path, now=NOW + 259 * DAY)
        self.assertIn("已遗忘 0", text)
        self.assertEqual(self.refs("private.db"), set(TEXTS))
        _, text = run("forget", "sweep", "--config", path, now=NOW + 261 * DAY)
        self.assertIn("已遗忘 3", text)  # all three were met on the same day: the same lifetime ends for all
        self.assertEqual(self.refs("private.db"), {"keep/profile"})


class RateLimitCommandTests(ForgetCase):
    def setUp(self):
        super().setUp()
        self.seed("private.db", TEXTS)
        self.manage("private", TEXTS)  # "old" is due: 1 of 4 = 25%
        self.path = self.config(extra="max_forget_fraction = 0.2\nmax_forget_min = 0")  # limit: 0.2 * 4 = 0

    def test_a_sweep_over_the_limit_holds_the_rest_and_says_so(self):
        code, text = run("forget", "sweep", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("已遗忘 0", text)
        self.assertIn("留下 1 条到下一次清理（限速 max(0, 20%×条数)", text)
        self.assertIn("--force", text)
        self.assertEqual(self.refs("private.db"), set(TEXTS))
        _, archive = run("forget", "archive", "--config", self.path)
        self.assertIn("共 0 条", archive)

    def test_dry_run_and_status_show_what_would_be_held(self):
        _, text = run("forget", "sweep", "--dry-run", "--config", self.path)
        self.assertIn("到期（会被遗忘）1", text)
        self.assertIn("真正清理时会留下 1 条到下一次清理", text)
        _, status = run("forget", "status", "--config", self.path)
        self.assertIn("限速：到期的有 1 条超过每次的上限，真正清理时会留下 1 条", status)
        self.assertIn("每次每个存储最多忘 max(0, 20%×条数) 条", status)

    def test_the_limit_lets_the_minimum_through(self):
        path = self.config(extra="max_forget_fraction = 0.2\nmax_forget_min = 1")
        _, text = run("forget", "sweep", "--config", path)
        self.assertIn("已遗忘 1", text)
        self.assertNotIn("留下", text)

    def test_force_lifts_the_limit_for_one_run(self):
        code, text = run("forget", "sweep", "--force", "--config", self.path)
        self.assertEqual(code, 0)
        self.assertIn("已遗忘 1", text)
        self.assertNotIn("留下", text)
        self.assertEqual(self.refs("private.db"), {"mid", "new", "keep/profile"})
        self.assertNotIn("SECRET", text)

    def test_check_shows_the_limit(self):
        _, text = run("check", "--config", self.path)
        self.assertIn("限速：每个存储每次清理最多忘 max(0, 20%×该存储条数) 条", text)


if __name__ == "__main__":
    unittest.main()
