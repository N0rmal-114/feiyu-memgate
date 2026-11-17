"""The `feiyu-memgate` command line: run the gateway, inspect a configuration, list the
pieces, back memories up, evaluate recall, manage tenant keys and mint tenant-bound tokens.

Secrets never travel through arguments or output: tokens, keys and minting secrets are read
from environment variables named on the command line or in the configuration, and nothing
here prints them. The one exception is deliberate: `tenants token` prints the token it mints
(never the secret it mints from), because handing that token out is the point.

Standard library only.
"""
import argparse
import fnmatch
import inspect
import json
import math
import os
import sys
import time
from pathlib import Path

from . import config, registry
from .errors import ConfigError
from .forgetting.engine import DAY, REQUIRED as FORGETTING_REQUIRED, date_to_epoch, supports

# The clock of the forgetting commands. Tests replace it; it is not a user option.
CLOCK = time.time


def _wall():
    return CLOCK()


KIND_TITLES = {
    "backend": "存储（backend）", "recall_stage": "召回阶段（recall_stage）", "write_guard": "写入关卡（write_guard）",
    "redactor": "脱敏（redactor）", "tenants": "租户（tenants）",
    "forgetting_curve": "遗忘曲线（forgetting_curve）",
}


# ---------------------------------------------------------------- serve / check

def _load_gateway(path):
    cfg = config.load(path)
    return cfg, config.build_gateway(cfg)


def cmd_serve(args):
    from .gateway import server
    cfg, gateway = _load_gateway(args.config)
    host, port, extra = config.server_options(cfg)
    server.serve(gateway, args.host or host, args.port if args.port is not None else port, **extra)
    return 0


def _piece_name(kind, obj):
    """The registered name of a piece, or its class name when it came from a dotted path."""
    for name, factory in registry.available(kind).items():
        if type(obj) is factory:
            return name
    return type(obj).__name__


def describe(gateway):
    """The wiring of a gateway as lines of text. Holds no secret: only whether a token exists."""
    lines = ["存储："]
    for name, store in gateway.stores.items():
        lines.append("  %s: %s，%s" % (name, _piece_name("backend", store), "可写" if getattr(store, "writable", False) else "只读"))
    if not gateway.stores:
        lines.append("  （没有）")
    lines.append("租户来源：")
    for name, provider in gateway.tenants.items():
        lines.append("  %s: %s" % (name, _piece_name("tenants", provider)))
    if not gateway.tenants:
        lines.append("  （没有）")
    lines.append("客户端：")
    warnings = []
    for name, policy in gateway.clients.items():
        recall = "不可召回" if policy.recall is None else (", ".join(policy.recall) or "（空）")
        if policy.token_bound:
            token = "有可用的铸造密钥" if gateway.has_token(name) else "没有可用的铸造密钥"
        else:
            token = "有可用令牌" if gateway.has_token(name) else "没有可用令牌"
        line = "  %s: 召回 %s；写入 %s；租户 %s；公开 %s；每日写入上限 %s；令牌 %s" % (
            name, recall, policy.write or "无", policy.tenants or "无", "是" if policy.public else "否",
            policy.daily_writes if policy.daily_writes is not None else "无", token)
        if policy.token_bound:
            line += "；租户绑定 token（每个令牌只能用于铸造时绑定的租户，第 %d 代）" % policy.tenant_token_generation
            if gateway.weak_minting_secret(name):  # yes or no only: never the secret or its length
                warnings.append("  提醒：%s 的铸造密钥看起来不是随机生成的（太短或用到的字符太少）：拿到一个令牌的人能离线猜它；"
                                "请用 python -c \"import secrets; print(secrets.token_urlsafe(32))\" 重新生成" % name)
            if policy.daily_writes_total is not None and policy.daily_writes is None:
                warnings.append("  提醒：%s 设了 daily_writes_total 却没设 daily_writes：一个租户就能用完所有租户合计的"
                                "写入次数；租户之间互不信任时，给每个租户也设上 daily_writes" % name)
        elif policy.tenants is not None:
            line += "；租户绑定 request（租户号由请求自报）"
            warnings.append("  提醒：%s 的租户号由调用方自报：令牌只交给一个可信的进程用没问题；"
                            "令牌要交给别的程序时，改用 tenant_binding = \"token\"" % name)
        lines.append(line)
    lines.extend(warnings)
    lines.append("召回阶段（按顺序）：" + (" -> ".join(_piece_name("recall_stage", s) for s in gateway.stages) or "（没有）"))
    profiles = ["%s %s" % (_piece_name("recall_stage", s), s.profile) for s in gateway.stages
                if isinstance(getattr(s, "profile", None), str)]
    lines.append("分数线档位：" + ("、".join(profiles) or "（没有 min_score 或 relative_score）"))
    lines.append("写入关卡（按顺序）：" + (" -> ".join(_piece_name("write_guard", g) for g in gateway.guards) or "（没有）"))
    return lines


def stage_reminders(gateway):
    """One reminder line per recall stage that stands before a stage it must come after
    (core.stages.misplaced; the stage's `after` names those stages)."""
    from .core.stages import misplaced
    rules = {}
    for stage in gateway.stages:
        rule = getattr(stage, "after", None)
        if isinstance(rule, tuple):
            rules.setdefault(getattr(stage, "name", None) or type(stage).__name__, rule)
    return ["  提醒：%s 要放在 %s 后面" % (name, " 和 ".join(rules.get(name, ())) or "规定的步骤")
            for name in misplaced(gateway.stages)]


def cmd_check(args):
    try:
        cfg, gateway = _load_gateway(args.config)
    except ConfigError as error:
        print("配置有问题：%s" % error)
        return 1
    try:
        for line in describe(gateway) + stage_reminders(gateway):
            print(line)
        for line in describe_forgetting(gateway, cfg):
            print(line)
    finally:
        gateway.close()
    return 0


# ---------------------------------------------------------------- pieces

def _first_line(factory):
    doc = inspect.getdoc(factory) or ""
    return doc.splitlines()[0] if doc else ""


def cmd_pieces(args):
    for kind in registry.KINDS:
        print("%s：" % KIND_TITLES.get(kind, kind))  # a kind without a title still lists
        for name, factory in sorted(registry.available(kind).items()):
            print("  %s  %s" % (name, _first_line(factory)))
        extra = registry.installed(kind)
        if extra:
            print("  通过已安装的包提供：" + ", ".join(extra))
    return 0


# ---------------------------------------------------------------- backup

def _backup_module():
    from .tools import backup  # lazy: needs an optional dependency and is only used here
    return backup


def cmd_backup(args):
    from .tools.crypto import KeyFileError  # stdlib only at import time; the cryptography library loads lazily
    try:
        backup = _backup_module()
        if args.action == "seal":
            dirs = {}
            for directory in args.dirs:
                path = Path(directory)
                if path.name in dirs:
                    print("错误：目录名重复：%s" % path.name)
                    return 1
                dirs[path.name] = path
            result = backup.seal(dirs, args.out, args.key)
            print({"written": "已写入加密备份", "unchanged": "没有变化，没有写新备份",
                   "nothing": "没有可备份的内容"}.get(result, result))
            return 0
        if args.action == "open":
            count = backup.open_bundle(args.bundle, args.outdir, args.key)
            print("已解开 %s 个文件" % count)
            return 0
        api_key = os.environ.get(args.key_env, "")
        if not api_key:
            print("错误：环境变量 %s 没有设置" % args.key_env)
            return 1
        count = backup.export_openviking(args.url, api_key, args.dest, tmp_root=args.tmp)
        print("已导出 %s 个文件" % count)
        return 0
    except KeyFileError as error:  # its message names the file the user passed and is meant to be shown
        print("错误：%s" % error)
        return 1
    except OSError as error:
        print("错误：%s" % type(error).__name__)
        return 1
    except (RuntimeError, ValueError) as error:
        # RuntimeError: CryptoMissing and export failures; ValueError: DecryptError (wrong key, not a bundle).
        print("错误：%s" % error)
        return 1


# ---------------------------------------------------------------- eval

def _option(text):
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError("选项要写成 名字=值")
    try:
        value = json.loads(value)  # numbers and true/false keep their type, anything else stays text
    except ValueError:
        pass
    return key, value


def _thresholds(text):
    """"0.1,0.2,0.3" -> (0.1, 0.2, 0.3); every value must be within 0..1."""
    try:
        values = tuple(float(part) for part in text.split(",") if part.strip())
    except ValueError:
        raise argparse.ArgumentTypeError("分数线要写成逗号分隔的小数，例如 0.1,0.2,0.3") from None
    if not values or any(not 0 <= value <= 1 for value in values):
        raise argparse.ArgumentTypeError("分数线要在 0 到 1 之间")
    return values


def _read_json(path):
    with open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def cmd_eval(args):
    from .tools import evaluate
    try:
        memories, queries = _read_json(args.memories), _read_json(args.queries)
        spec = dict(args.store_option, type=args.store_type)
        backend = registry.create("backend", spec)
        try:
            options = {"thresholds": args.thresholds} if args.thresholds else {}
            evaluate.run_eval(backend, memories, queries, seed=not args.no_seed, **options)
        finally:
            backend.close()
    except ConfigError as error:
        print("配置有问题：%s" % error)
        return 1
    except (OSError, ValueError) as error:
        print("错误：%s" % (error if isinstance(error, ValueError) else type(error).__name__))
        return 1
    return 0


# ---------------------------------------------------------------- tenants

def _tenants_token(args):
    """Print one tenant-bound token on stdout and nothing else; every refusal goes to stderr, so
    `TOKEN=$(feiyu-memgate tenants token ...)` never captures a message as a token."""
    from .tools import tenants

    def refuse(text):
        sys.stderr.write("错误：%s\n" % text)
        return 1

    if not args.config or not args.client or args.tenant is None:
        return refuse("token 需要 --config、--client 和 --tenant")
    try:
        token = tenants.mint_token(config.load(args.config), args.client, args.tenant)
    except ConfigError as error:  # names the setting, never a value
        return refuse("配置有问题：%s" % error)
    except ValueError as error:  # its messages hold no secret
        return refuse(str(error))
    except OSError as error:
        return refuse(type(error).__name__)
    print(token)
    return 0


def cmd_tenants(args):
    from .tools import tenants
    if args.action == "token":
        return _tenants_token(args)
    if not args.keys:
        print("错误：%s 需要 --keys 指定密钥文件" % args.action)
        return 1
    try:
        if args.action == "list":
            ids = tenants.list_tenants(args.keys)
            for tenant_id in ids:
                print(tenant_id)
            print("共 %d 个租户" % len(ids))
        elif args.action == "add":
            if not args.id:
                print("错误：add 需要租户编号")
                return 1
            key = os.environ.get(args.key_env or "", "")
            if not key:
                print("错误：请用 --key-env 指定一个已设置的环境变量来提供密钥")
                return 1
            tenants.add_tenant(args.keys, args.id, key, id_pattern=args.id_pattern)
            print("已添加租户 %s" % args.id)
        else:
            if not args.id:
                print("错误：remove 需要租户编号")
                return 1
            if tenants.remove_tenant(args.keys, args.id):
                print("已删除租户 %s" % args.id)
            else:
                print("没有这个租户：%s" % args.id)
                return 1
    except ValueError as error:
        print("错误：%s" % error)
        return 1
    except OSError as error:
        print("错误：%s" % type(error).__name__)
        return 1
    return 0


# ---------------------------------------------------------------- forgetting

NOT_CONFIGURED = (
    "没有配置遗忘。在配置文件里加一节 [forgetting] 就能打开，最小的写法：\n"
    "  [forgetting]\n"
    "  ledger = \"data/ledger.db\"\n"
    "  archive = \"data/forgotten.db\"\n"
    "  stores = [\"private\"]\n"
    "并在 [recall] stages 里加上 \"freshness\"。详见 docs/forgetting.md。"
)


def _day(epoch):
    """A unix time as a UTC date, "-" when there is none."""
    if epoch is None:
        return "-"
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


def _curve_lines(curve):
    """The curve's name and its simple settings, as lines. Works for any curve piece."""
    name = _piece_name("forgetting_curve", curve)
    settings = {k: v for k, v in sorted(getattr(curve, "__dict__", {}).items())
                if not k.startswith("_") and isinstance(v, (int, float, str, bool, dict, list, tuple))}
    lines = ["曲线：%s" % name]
    if "half_life_days" in settings:  # the built-in one, spelled out
        lines.append("  基础半衰期 %s 天；按来源：%s；巩固系数 %s；次数上限 %s" % (
            settings["half_life_days"],
            "，".join("%s=%s" % item for item in sorted(settings.get("per_store", {}).items())) or "（没有）",
            settings.get("reinforce"), settings.get("max_uses")))
    else:
        for key, value in settings.items():
            lines.append("  %s = %s" % (key, value))
    return lines


def describe_forgetting(gateway, cfg):
    """The forgetting part of `check` as lines of text; nothing when it is not configured.
    Holds paths, names and numbers, never memory text."""
    f = gateway.forgetting
    if f is None:
        return []
    spec = cfg.get("forgetting", {})
    lines = ["遗忘：",
             "  账本：%s（只有地址、时间、次数，不含正文）" % spec.get("ledger"),
             "  存档：%s（含记忆全文，要像记忆本身一样保护）" % spec.get("archive"),
             "  降权线 R<%s，遗忘线 R<%s，存档保留 %s 天；定时清理：%s" % (
                 f.demote_below, f.forget_below, f.archive_days,
                 "每 %s 小时" % gateway.sweep_every_hours if gateway.sweep_every_hours else "不定时（只手动）"),
             "  钉住规则 pin_refs：%s" % (", ".join(f.pin_refs) or "（没有）"),
             "  限速：每个存储每次清理最多忘 max(%d, %s×该存储条数) 条，先忘保持率最低的，其余留到下次（held）" % (
                 f.max_forget_min, _percent(f.max_forget_fraction))]
    lines += ["  " + line for line in _curve_lines(f.curve)]
    lines.append("  管理的存储：")
    for name in f.stores:
        if name in gateway.stores:
            store = gateway.stores[name]
            missing = [c for c in FORGETTING_REQUIRED if not callable(getattr(store, c, None))]
            pin = "有" if callable(getattr(store, "receipt_refs", None)) else "没有（pin_writes 对它不起作用）"
            lines.append("    %s（共享存储）：%s；receipt_refs：%s" % (
                name, "refs、delete、put 齐全" if not missing else "缺少 " + "、".join(missing), pin))
        else:
            provider = gateway.tenants.get(name)
            can_list = callable(getattr(provider, "list", None))
            lines.append("    %s（租户来源）：%s" % (
                name, "能列出租户，每个租户一份账" if can_list else "不能列出租户，清理会跳过它"))
    stages = [_piece_name("recall_stage", s) for s in gateway.stages]
    if "freshness" not in stages:
        lines.append("  提醒：recall stages 里没有 freshness，召回不会按新旧排序（清理照常进行）")
    return lines


def _resolve_store(gateway, key):
    """The store behind a ledger key ("name" or "provider:tenant"), or None."""
    f = gateway.forgetting
    if key in gateway.stores:
        return gateway.stores[key] if f.manages(key) else None
    provider_name, sep, tenant_id = key.partition(":")
    provider = gateway.tenants.get(provider_name)
    if not sep or provider is None or not f.manages(provider_name):
        return None
    if not provider.valid_id(tenant_id):
        return None
    return provider.store(tenant_id)


def _forget_status(gateway, args):
    f = gateway.forgetting
    report = gateway.sweep(dry_run=True)
    records = f.archive.list()
    archived = {}
    for record in records:
        archived[record.store] = archived.get(record.store, 0) + 1
    print("遗忘状态（日期按 UTC）：")
    keys = [k for k, v in report.items() if isinstance(v, dict)]
    for key in keys:
        row = report[key]
        print("  %s：共 %d，钉住 %d，正常 %d，降权 %d，到期 %d，读取失败 %d；存档 %d" % (
            key, row["total"], row["pinned"], row["active"], row["demoted"], row["due"], row["failed"],
            archived.get(key, 0)))
        if row.get("held"):
            print("    限速：到期的有 %d 条超过每次的上限，真正清理时会留下 %d 条到下一次（forget sweep --force 一次全删）" % (
                row["due"], row["held"]))
    for key in report["unsupported"]:
        print("  %s：存储缺少 refs/delete/put，管理不了" % key)
    for name in f.stores:
        provider = gateway.tenants.get(name)
        if provider is not None and not callable(getattr(provider, "list", None)):
            print("  %s：租户来源不能列出租户，清理会跳过它" % name)
    if not keys:
        print("  （没有可管理的存储或租户）")
    print("存档共 %d 条，其中已过期、等下次清理才删的 %d 条" % (
        len(records), sum(1 for r in records if r.expires_at <= f.now())))
    print("降权线 R<%s（按排序用的保持率，只用于报告），遗忘线 R<%s（按删除用的保持率），存档保留 %s 天，"
          "每次每个存储最多忘 max(%d, %s×条数) 条" % (
              f.demote_below, f.forget_below, f.archive_days, f.max_forget_min, _percent(f.max_forget_fraction)))
    for line in _curve_lines(f.curve):
        print(line)
    return 0


def _percent(fraction):
    return "%g%%" % round(fraction * 100, 4)


def _forget_sweep(gateway, args):
    f = gateway.forgetting
    # Not gateway.sweep(): that one is the background sweep, which never lifts the limit.
    report = f.sweep(gateway.sweep_targets(), dry_run=args.dry_run, force=args.force)
    print("演习：什么都没有改" if args.dry_run else "清理完成")
    for key, row in report.items():
        if not isinstance(row, dict):
            continue
        if args.dry_run:
            print("  %s：共 %d，钉住 %d，正常 %d，降权 %d，到期（会被遗忘）%d，读取失败 %d" % (
                key, row["total"], row["pinned"], row["active"], row["demoted"], row["due"], row["failed"]))
        else:
            print("  %s：共 %d，钉住 %d，正常 %d，降权 %d，已遗忘 %d，失败 %d" % (
                key, row["total"], row["pinned"], row["active"], row["demoted"], row["forgotten"], row["failed"]))
        if row.get("held"):
            print("    %s：%s留下 %d 条到下一次清理（限速 max(%d, %s×条数)；一次全删，加 --force）" % (
                key, "真正清理时会" if args.dry_run else "", row["held"], f.max_forget_min,
                _percent(f.max_forget_fraction)))
    for key in report["unsupported"]:
        print("  %s：存储缺少 refs/delete/put，已跳过" % key)
    if not args.dry_run:
        print("存档里过期并真正删掉的：%d 条" % report["purged"])
    return 0


def _forget_archive(gateway, args):
    f = gateway.forgetting
    records = f.archive.list(args.store)
    for record in records:
        print("%s  %s  遗忘于 %s  到期 %s  用过 %d 次" % (
            record.store, record.ref, _day(record.forgotten_at), _day(record.expires_at), record.uses))
        if args.show_text:
            full = f.archive.get(record.store, record.ref)
            print("    " + (full.text if full else "").replace("\n", "\n    "))
    print("共 %d 条" % len(records))
    return 0


def _forget_restore(gateway, args):
    f = gateway.forgetting
    store = _resolve_store(gateway, args.store)
    if store is None:
        print("错误：没有这个被管理的存储：%s" % args.store)
        return 1
    if not supports(store):
        print("错误：这个存储缺少 refs/delete/put，不能恢复")
        return 1
    if not f.restore(args.store, args.ref, store):
        print("错误：存档里没有 %s %s（可能已过期删掉）" % (args.store, args.ref))
        return 1
    print("已恢复 %s %s：写回原地址，最近用到设为现在" % (args.store, args.ref))
    return 0


def _forget_pin(gateway, args):
    f = gateway.forgetting
    store = _resolve_store(gateway, args.store)
    if store is None:
        print("错误：没有这个被管理的存储：%s" % args.store)
        return 1
    if args.action == "pin":
        # A row for an address the store does not hold would be dropped by the next sweep.
        present = {ref for ref, _ in store.refs()} if callable(getattr(store, "refs", None)) else None
        unknown = [r for r in args.refs if present is not None and r not in present]
        known = [r for r in args.refs if r not in unknown]
        if known:
            # Start the row from the memory's own date: a row made by pin() alone would start its age
            # at "now", and unpinning later would give the memory a fresh life.
            dates = dict(store.refs())
            f.ledger.touch(args.store, [(r, date_to_epoch(dates.get(r))) for r in known], f.now())
            f.pin(args.store, known)
        for ref in unknown:
            print("不在存储里，没有钉：%s" % ref)
        print("已钉住 %d 条" % len(known))
        return 1 if unknown else 0
    entries = f.ledger.get_many(args.store, args.refs)
    unpinned = []
    for ref in args.refs:
        entry = entries.get(ref)
        if entry is None or not entry.pinned:
            print("本来就没有手动钉住：%s" % ref)
        else:
            unpinned.append(ref)
    if unpinned:
        f.unpin(args.store, unpinned)
    print("已取消钉住 %d 条" % len(unpinned))
    for ref in args.refs:
        if any(fnmatch.fnmatchcase(ref, p) for p in f.pin_refs):
            print("注意：%s 仍然匹配 pin_refs 规则，还是不会被遗忘" % ref)
    return 0


def _crossing(f, key, age, uses, line):
    """Days from now until retention falls under `line` if the memory is never used again, given the
    age (days) on the clock in question: 0 when it already is, None when it never does.
    Works for any curve (bisection)."""
    source = key.partition(":")[0]

    def value(extra):
        return f.curve.retention(age + extra, uses, source)

    if value(0.0) < line:
        return 0.0
    high = 1.0
    while value(high) >= line:
        high *= 2
        if high > 365000.0:  # a thousand years: treat as never
            return None
    low = 0.0
    for _ in range(60):
        middle = (low + high) / 2
        if value(middle) < line:
            high = middle
        else:
            low = middle
    return high


def _age_days(start, now):
    return 0.0 if start is None else max(now - start, 0.0) / DAY


def _when(f, key, age, uses, line, now):
    days = _crossing(f, key, age, uses, line)
    if days is None:
        return "不会（曲线在一千年内不会降到这条线以下）"
    if days == 0.0:
        return "已经越过"
    whole = math.ceil(days)
    return "%d 天后（%s）" % (whole, _day(now + whole * DAY))


def _forget_explain(gateway, args):
    f = gateway.forgetting
    store = _resolve_store(gateway, args.store)
    if store is None:
        print("错误：没有这个被管理的存储：%s" % args.store)
        return 1
    dates = dict(store.refs()) if callable(getattr(store, "refs", None)) else {}
    entry = f.ledger.get_many(args.store, [args.ref]).get(args.ref)
    if args.ref not in dates and entry is None:
        record = f.archive.get(args.store, args.ref)
        if record is not None:
            print("%s %s 已被遗忘，在存档里：遗忘于 %s，到期 %s，用过 %d 次。可以用 forget restore 恢复。" % (
                args.store, args.ref, _day(record.forgotten_at), _day(record.expires_at), record.uses))
            return 0
        print("错误：存储、账本和存档里都没有 %s %s" % (args.store, args.ref))
        return 1
    now = f.now()
    date = dates.get(args.ref)
    print("%s %s" % (args.store, args.ref))
    print("  在存储里：%s；记忆日期：%s" % ("是" if args.ref in dates else "否（账本里还有一行，下次清理会去掉）", date or "-"))
    if entry is None:
        print("  账本：还没有记录（没被用到过，清理时会补上）")
    else:
        print("  账本：记忆日期起点 %s，开始被管理 %s，最近用到 %s，用过 %d 次，手动钉住 %s" % (
            _day(entry.first_seen), _day(entry.managed_since),
            _day(entry.last_used) if entry.last_used is not None else "从没用到过",
            entry.uses, "是" if entry.pinned else "否"))
    status = f.status(args.store, args.ref, entry, date, now)
    names = {"pinned": "钉住（不降权、不遗忘）", "due": "到期（下一次清理会存档并删除，除非被每次的上限留到下一次）",
             "demoted": "降权（排序保持率低于降权线：很快到新鲜度下限，以后是被删的候选；召回排序不因此改变）", "active": "正常"}
    uses = f.uses_of(entry)
    rank_age = _age_days(f.ranking_start(entry, date), now)
    delete_start = f.deletion_start(entry, now)
    delete_age = _age_days(delete_start, now)
    print("  排序用的保持率：%.3f（距最近用到或记忆日期 %.1f 天）；状态：%s" % (
        f.ranking_retention(args.store, entry, date, now), rank_age, names[status]))
    print("  删除用的保持率：%.3f（距最近用到或开始被管理 %.1f 天）" % (
        f.deletion_retention(args.store, entry, now), delete_age))
    if status == "pinned":
        return 0
    if entry is None or entry.managed_since is None:
        print("  还没有开始被管理：删除的计时从第一次清理（或第一次被用到）开始")
    print("  如果以后再没被用到：")
    print("    越过降权线（排序 R<%s）：%s" % (f.demote_below, _when(f, args.store, rank_age, uses, f.demote_below, now)))
    print("    最早可以被删除（删除 R<%s）：%s" % (
        f.forget_below, _when(f, args.store, delete_age, uses, f.forget_below, now)))
    print("    （每次清理每个存储最多忘 max(%d, %s×条数) 条，先忘保持率最低的，所以实际可能更晚；forget sweep --force 取消限速）" % (
        f.max_forget_min, _percent(f.max_forget_fraction)))
    return 0


FORGET_ACTIONS = {"status": _forget_status, "sweep": _forget_sweep, "archive": _forget_archive,
                  "restore": _forget_restore, "pin": _forget_pin, "unpin": _forget_pin, "explain": _forget_explain}


def cmd_forget(args):
    """Build the gateway objects from the configuration without serving, run one sub-command, close them."""
    cfg = config.load(args.config)
    gateway = config.build_gateway(cfg, wall=_wall, log=lambda line: None)
    try:
        if gateway.forgetting is None:
            print(NOT_CONFIGURED)
            return 1
        try:
            return FORGET_ACTIONS[args.action](gateway, args)
        except Exception as error:  # noqa: BLE001 - a store or file failure is a message, never a traceback or memory text
            print("错误：%s" % type(error).__name__)
            return 1
    finally:
        gateway.close()


# ---------------------------------------------------------------- parser

def build_parser():
    from .tenants.keyfile import DEFAULT_ID_PATTERN
    parser = argparse.ArgumentParser(prog="feiyu-memgate", description="可拼装的记忆网关")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="按配置文件启动网关")
    p.add_argument("--config", required=True, metavar="FILE", help="TOML 配置文件")
    p.add_argument("--host", help="监听地址（覆盖配置）")
    p.add_argument("--port", type=int, help="监听端口（覆盖配置）")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("check", help="检查配置并显示各部件怎么拼起来的（不显示令牌）")
    p.add_argument("--config", required=True, metavar="FILE", help="TOML 配置文件")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("pieces", help="列出所有可用的部件")
    p.set_defaults(func=cmd_pieces)

    p = sub.add_parser("backup", help="加密备份与导出")
    bsub = p.add_subparsers(dest="action", required=True)
    s = bsub.add_parser("seal", help="把目录加密打包成一个备份文件")
    s.add_argument("--key", required=True, metavar="KEYFILE", help="备份密钥文件")
    s.add_argument("--out", required=True, metavar="BUNDLE", help="备份文件")
    s.add_argument("dirs", nargs="+", metavar="DIR", help="要备份的目录")
    s = bsub.add_parser("open", help="解开备份文件")
    s.add_argument("--key", required=True, metavar="KEYFILE", help="备份密钥文件")
    s.add_argument("bundle", metavar="BUNDLE", help="备份文件")
    s.add_argument("outdir", metavar="OUTDIR", help="解开到哪个目录")
    s = bsub.add_parser("export-openviking", help="把 OpenViking 里的记忆导出成文件")
    s.add_argument("--url", required=True, help="OpenViking 地址")
    s.add_argument("--key-env", required=True, metavar="ENVVAR", help="存放访问密钥的环境变量名")
    s.add_argument("--dest", required=True, metavar="DIR", help="导出到的目录")
    s.add_argument("--tmp", required=True, metavar="DIR", help="临时目录")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("eval", help="用示例记忆评测召回质量，帮助选分数线")
    p.add_argument("--store-type", default="inmemory", help="存储类型（默认 inmemory）")
    p.add_argument("--store-option", action="append", default=[], type=_option, metavar="k=v", help="存储选项，可重复")
    p.add_argument("--memories", required=True, metavar="FILE", help="示例记忆 JSON")
    p.add_argument("--queries", required=True, metavar="FILE", help="示例问题 JSON")
    p.add_argument("--no-seed", action="store_true", help="不写入示例记忆（存储里已经有了）")
    p.add_argument("--thresholds", type=_thresholds, metavar="A,B,...",
                   help="要比较的分数线，逗号分隔（默认 0.35 到 0.90；关键词后端建议从 0.10 起）")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("tenants", help="管理 keyfile 租户的密钥文件；给绑定租户的客户端铸造令牌（token）")
    p.add_argument("action", choices=("add", "remove", "list", "token"))
    p.add_argument("id", nargs="?", help="租户编号（add、remove 用）")
    p.add_argument("--keys", metavar="FILE", help="密钥文件（add、remove、list 用）")
    p.add_argument("--key-env", metavar="ENVVAR", help="add 时存放密钥的环境变量名")
    p.add_argument("--id-pattern", default=DEFAULT_ID_PATTERN, help="租户编号的正则（add 用）")
    p.add_argument("--config", metavar="FILE", help="TOML 配置文件（token 用）")
    p.add_argument("--client", metavar="NAME", help="tenant_binding = \"token\" 的客户端名字（token 用）")
    p.add_argument("--tenant", metavar="ID",
                   help="租户编号（token 用）：只在标准输出打印一个绑定这个租户的令牌，铸造密钥从客户端的 token_env 读")
    p.set_defaults(func=cmd_tenants)

    p = sub.add_parser("forget", help="遗忘曲线：查看状态、清理、存档、恢复、钉住（输出只有数字和地址，不显示记忆正文）")
    fsub = p.add_subparsers(dest="action", required=True)

    def forget_parser(name, help_text):
        s = fsub.add_parser(name, help=help_text)
        s.add_argument("--config", required=True, metavar="FILE", help="TOML 配置文件")
        s.set_defaults(func=cmd_forget)
        return s

    forget_parser("status", "每个存储的条数（钉住、正常、降权、到期）、存档大小、线和半衰期")
    s = forget_parser("sweep", "清理一次：到期的先存档再删除")
    s.add_argument("--dry-run", action="store_true", help="只算条数，哪里都不改")
    s.add_argument("--force", action="store_true",
                   help="这一次不受每次清理的条数上限（max_forget_fraction、max_forget_min）限制；网关的定时清理从不这样")
    s = forget_parser("archive", "列出存档里的记忆（存储、地址、遗忘日期、到期日期、用过几次）")
    s.add_argument("--store", metavar="KEY", help="只看这个存储（共享存储的名字，或 提供方:租户编号）")
    s.add_argument("--show-text", action="store_true", help="同时显示记忆正文（默认不显示）")
    s = forget_parser("restore", "把存档里的一条写回原来的存储和地址")
    s.add_argument("store", metavar="STORE", help="共享存储的名字，或 提供方:租户编号")
    s.add_argument("ref", metavar="REF", help="记忆的地址")
    s = forget_parser("pin", "钉住：这些记忆永远不降权、不遗忘")
    s.add_argument("store", metavar="STORE", help="共享存储的名字，或 提供方:租户编号")
    s.add_argument("refs", nargs="+", metavar="REF", help="记忆的地址")
    s = forget_parser("unpin", "取消手动钉住")
    s.add_argument("store", metavar="STORE", help="共享存储的名字，或 提供方:租户编号")
    s.add_argument("refs", nargs="+", metavar="REF", help="记忆的地址")
    s = forget_parser("explain", "账本记着这条记忆什么、两个保持率各多少、什么时候越过降权线、最早什么时候可以被删")
    s.add_argument("store", metavar="STORE", help="共享存储的名字，或 提供方:租户编号")
    s.add_argument("ref", metavar="REF", help="记忆的地址")
    return parser


def _lenient_output():
    """Let a message that the console cannot write come out with replacement marks.

    The messages are Chinese. On a console or a pipe in another encoding (cp1252 on a Windows set to a
    Western language) print() raised UnicodeEncodeError, and a command that had done its work ended in
    a traceback with exit code 1. The encoding itself is left alone: setting PYTHONUTF8=1 is the way to
    get readable text there. Streams that cannot be reconfigured (a test's StringIO) are left as they are.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None):
    _lenient_output()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as error:
        print("配置有问题：%s" % error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
