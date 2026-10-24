#!/usr/bin/env python3
"""verify-handoff の補助スクリプト(標準ライブラリのみ)。

  vh.py detect  [--root DIR]            実行する検証コマンド一覧をJSONで表示(実行はしない)
  vh.py run     [--root DIR] [--only NAME ...] [--timeout SEC] [--out FILE]
                                        検証を順に実行し、結果をJSON+要約で記録
  vh.py handoff [--root DIR] [--results FILE] [--unresolved TXT ...] [--next TXT ...] [--out FILE]
                                        git状態+直近の検証結果から引き継ぎMarkdownを生成
  vh.py note    KIND [--text TXT] [--root DIR]
                                        手動介入(question=質問 / approval=承認待ち / redo=やり直し)を履歴へ1件記録
  vh.py history [--root DIR] [--by day|week|all] [--since-days N] [--json]
                                        検証と手動介入の履歴(.claude/handoff/history.jsonl)を期間ごとに集計

`run` は実行のたびに履歴(追記専用の JSON Lines。git管理外)へ1行追記する。履歴の書き込みに失敗しても検証は止めない。

プロジェクト固有の定義は <root>/.claude/verify.json が優先される:
  {"checks":[{"name":"lint","cwd":"sub","cmd":"ruff check .","env":{"K":"V"},"timeout":300,"optional":false}]}
無ければ package.json / pytest.ini / ruff.toml / Makefile から推定する(推定は保守的)。
"""
from __future__ import annotations

import argparse, json, os, re, shlex, subprocess, sys, time
from datetime import datetime
from pathlib import Path

ORDER = ["lint", "format", "typecheck", "type", "test", "build"]  # 軽い・速いものから


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def fingerprint(root):
    """HEAD + 未コミット差分 + verify.json の内容ハッシュ。検証結果が今の状態に対するものか判定する。"""
    import hashlib
    h = hashlib.sha256()
    h.update(git(root, "status", "--porcelain").encode())
    h.update(git(root, "diff", "HEAD").encode())
    cfg = root / ".claude" / "verify.json"
    if cfg.exists():
        h.update(cfg.read_bytes())
    return h.hexdigest()[:12]


def ignore_handoff(root):
    """出力先を .git/info/exclude に登録し、作業ツリーを汚さない(.gitignore は触らない)。"""
    ex = git(root, "rev-parse", "--git-path", "info/exclude")
    if not ex:
        return
    ex = Path(ex) if Path(ex).is_absolute() else root / ex
    try:
        cur = ex.read_text(encoding="utf-8") if ex.exists() else ""
        if ".claude/handoff/" not in cur:
            ex.parent.mkdir(parents=True, exist_ok=True)
            ex.write_text(cur + ("" if cur.endswith("\n") or not cur else "\n") + ".claude/handoff/\n", encoding="utf-8")
    except OSError:
        pass


def repo_root(arg):
    p = Path(arg or ".").resolve()
    top = git(p, "rev-parse", "--show-toplevel")
    return Path(top) if top else p


def detect(root: Path):
    cfg = root / ".claude" / "verify.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8"))["checks"], str(cfg.relative_to(root))
        except (ValueError, KeyError) as e:
            sys.exit(f"{cfg} が不正です(JSONで checks 配列が必要): {e}")
    checks = []
    for d in [root, *sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))]:
        rel = str(d.relative_to(root)) or "."
        pkg = d / "package.json"
        if pkg.exists() and d.name != "node_modules":
            try:
                scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
            except ValueError:
                scripts = {}
            for n in ("lint", "typecheck", "test", "build"):
                if isinstance(scripts.get(n), str) and "no test specified" not in scripts[n]:  # npm init の初期プレースホルダーは除外
                    checks.append({"name": f"{rel}:{n}", "cwd": rel, "cmd": f"npm run {n}"})
        pyproj = (d / "pyproject.toml").read_text(errors="ignore") if (d / "pyproject.toml").exists() else ""
        cfgtxt = pyproj + "".join((d / f).read_text(errors="ignore") for f in ("tox.ini", "setup.cfg") if (d / f).exists())
        if (d / "ruff.toml").exists() or (d / ".ruff.toml").exists() or "[tool.ruff" in pyproj:
            checks.append({"name": f"{rel}:lint", "cmd": "ruff check .", "cwd": rel})
        if "[tool.mypy" in pyproj or (d / "mypy.ini").exists():
            checks.append({"name": f"{rel}:typecheck", "cmd": "mypy .", "cwd": rel})
        has_tests = (d / "tests").is_dir() and any((d / "tests").glob("test_*.py"))
        if (d / "pytest.ini").exists() or any(m in cfgtxt for m in ("[tool.pytest", "[tool:pytest]", "[pytest]")) or has_tests:
            checks.append({"name": f"{rel}:test", "cmd": "python -m pytest -q", "cwd": rel})
        if (d / "Makefile").exists() and d == root:
            mk = (d / "Makefile").read_text(errors="ignore")
            have = {c["name"].split(":")[-1] for c in checks if c["cwd"] == rel}  # package.json/pytest で既に拾った種別は重複させない
            for n in ("lint", "test", "build"):
                if f"\n{n}:" in "\n" + mk and n not in have:
                    checks.append({"name": f"make:{n}", "cwd": rel, "cmd": f"make {n}"})
    seen, out = set(), []
    for c in checks:  # (cwd,cmd)重複除去
        k = (c["cwd"], c["cmd"])
        if k not in seen:
            seen.add(k); out.append(c)
    out.sort(key=lambda c: next((i for i, o in enumerate(ORDER) if o in c["name"].split(":")[-1]), 99))
    return out, "autodetect"


def classify_failure(text, root: Path, cwd="."):
    """失敗出力を "env"(依存・コマンド未導入=環境起因。失敗扱いは維持)か "fail" に分類する。

    ModuleNotFoundError は、不足モジュールがリポジトリ内に存在する名前(自分のコードの import ミス)なら fail。
    外部パッケージ名だけが不足しているときに env とする。"""
    mods = re.findall(r"No module named '?([\w.]+)'?", text)  # `python -m pytest` 未導入は引用符なしで出る
    if mods:
        base = root / cwd

        def is_local(top):
            for d in (base, root, base / "src", root / "src"):
                if (d / f"{top}.py").exists():
                    return True
                pkg = d / top  # 同名のただのディレクトリ(docs/ build/ 等)は自コードとみなさない
                if pkg.is_dir() and ((pkg / "__init__.py").exists() or any(pkg.glob("*.py"))):
                    return True
            return False

        return "fail" if any(is_local(m.split(".")[0]) for m in mods) else "env"
    if any(m in text for m in ("ModuleNotFoundError", "command not found", ": not found", "No such file or directory: 'npm'")):
        return "env"
    return "fail"


HISTORY_NAME = "history.jsonl"
INTERVENTION_KINDS = ("question", "approval", "redo")


def history_path(root: Path) -> Path:
    return root / ".claude" / "handoff" / HISTORY_NAME


def append_history(root: Path, record: dict) -> None:
    """履歴へ1行追記する(追記専用)。失敗しても呼び出し元(検証)を止めない。"""
    try:
        dest = history_path(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        ignore_handoff(root)
        with dest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[warn] 履歴を書き込めませんでした: {e}", file=sys.stderr)


def load_history(root: Path) -> list:
    """履歴を読む。壊れた行(書き込み中の中断など)は読み飛ばす。"""
    path = history_path(root)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("at"):
            out.append(rec)
    return out


def _period(at: str, by: str) -> str:
    d = datetime.fromisoformat(at)
    if by == "day":
        return d.strftime("%Y-%m-%d")
    if by == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    return "all"


def summarize(records: list, by: str = "all", since_days=None, now=None) -> dict:
    """履歴を期間(day/week/all)ごとに集計する。

    runs: 検証の実行回数(部分実行を含む) / full_runs: 全チェックを実行した回数 / failed_runs: optional 以外が非passだった回数
    checks[name]: 実行回数・pass率・平均/最大秒数 / interventions[kind]: 手動介入の回数
    """
    if since_days is not None:
        cutoff = (now or datetime.now().astimezone()).timestamp() - since_days * 86400
        records = [r for r in records if datetime.fromisoformat(r["at"]).timestamp() >= cutoff]
    periods: dict = {}
    for r in records:
        b = periods.setdefault(_period(r["at"], by), {"runs": 0, "full_runs": 0, "failed_runs": 0,
                                                      "total_seconds": 0.0, "checks": {}, "interventions": {}})
        if r.get("type") == "intervention":
            b["interventions"][r.get("kind", "?")] = b["interventions"].get(r.get("kind", "?"), 0) + 1
            continue
        if r.get("type") != "run":
            continue
        b["runs"] += 1
        b["total_seconds"] = round(b["total_seconds"] + float(r.get("total_seconds", 0)), 1)
        if not r.get("partial"):
            b["full_runs"] += 1
        if any(c["status"] != "pass" and not c.get("optional") for c in r.get("checks", [])):
            b["failed_runs"] += 1
        for c in r.get("checks", []):
            k = b["checks"].setdefault(c["name"], {"runs": 0, "pass": 0, "seconds": []})
            k["runs"] += 1
            k["pass"] += c["status"] == "pass"
            k["seconds"].append(float(c.get("seconds", 0)))
    for b in periods.values():
        for k in b["checks"].values():
            secs = k.pop("seconds")
            k["pass_rate"] = round(k["pass"] / k["runs"], 3)
            k["avg_seconds"] = round(sum(secs) / len(secs), 1)
            k["max_seconds"] = round(max(secs), 1)
    return dict(sorted(periods.items()))


def print_summary(summary: dict) -> None:
    if not summary:
        print("履歴がありません(`vh.py run` で記録されます)")
        return
    for name, b in summary.items():
        iv = ", ".join(f"{k}={v}" for k, v in sorted(b["interventions"].items())) or "なし"
        print(f"== {name}: 実行 {b['runs']} 回(全チェック {b['full_runs']} 回 / 失敗あり {b['failed_runs']} 回 / 計 {b['total_seconds']}s)  手動介入: {iv}")
        for cn, k in b["checks"].items():
            print(f"   {cn:28} {k['runs']:>3}回  pass率 {k['pass_rate']*100:>5.1f}%  平均 {k['avg_seconds']}s  最大 {k['max_seconds']}s")


def run(root: Path, only, timeout, out):
    checks, src = detect(root)
    results = []
    for c in checks:
        if only and c["name"] not in only:
            continue
        if "name" not in c or "cmd" not in c:
            results.append({"name": c.get("name", "?"), "cmd": c.get("cmd", ""), "cwd": c.get("cwd", "."), "status": "error",
                            "exit": None, "seconds": 0, "optional": False, "tail": "name/cmd が未定義"})
            print(f"[error  ] {c.get('name','?')}: name/cmd が未定義", flush=True)
            continue
        env = {**os.environ, **c.get("env", {})}
        t0 = time.time()
        try:
            p = subprocess.run(c["cmd"], shell=True, cwd=root / c.get("cwd", "."), env=env,
                               capture_output=True, text=True, timeout=c.get("timeout", timeout))
            text = p.stdout + p.stderr
            status, code, tail = ("pass" if p.returncode == 0 else "fail"), p.returncode, text[-1500:]
            if status == "fail":
                status = classify_failure(text, root, c.get("cwd", "."))
        except OSError as e:  # cwd が無い等。1件の異常で全体を落とさない
            status, code, tail = "error", None, str(e)
        except subprocess.TimeoutExpired as e:
            status, code, tail = "timeout", None, f"{c.get('timeout', timeout)}秒でタイムアウト"
        results.append({"name": c["name"], "cmd": c["cmd"], "cwd": c.get("cwd", "."), "status": status,
                        "exit": code, "seconds": round(time.time() - t0, 1),
                        "optional": c.get("optional", False), "tail": "" if status == "pass" else tail})
        print(f"[{status:7}] {c['name']:28} {results[-1]['seconds']:>6}s  ({c['cmd']})", flush=True)
    rep = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "source": src,
           "head": git(root, "rev-parse", "--short", "HEAD"), "state": fingerprint(root),
           "partial": bool(only), "all_checks": [c.get("name", "?") for c in checks], "results": results}
    dest = Path(out) if out else root / ".claude" / "handoff" / "last_verify.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    ignore_handoff(root)
    # 履歴には今回実行した分だけを記録する(下の部分実行マージ前の results)。
    # 実行したチェックが0件(--only の名前違い等)は検証ではないので記録しない(集計を汚さない)。
    if results:
        append_history(root, {
            "type": "run", "at": rep["at"], "head": rep["head"], "branch": git(root, "branch", "--show-current") or "detached",
            "dirty": bool(git(root, "status", "--porcelain")), "partial": rep["partial"], "only": sorted(only) if only else None,
            "total_seconds": round(sum(r["seconds"] for r in results), 1),
            "checks": [{"name": r["name"], "status": r["status"], "seconds": r["seconds"], "optional": r["optional"]} for r in results]})
    if only and dest.exists():  # 部分実行は「同一HEAD+同一作業ツリー状態」の既存結果へだけ名前単位でマージ
        try:
            old = json.loads(dest.read_text(encoding="utf-8"))
            if old.get("head") == rep["head"] and old.get("state") == rep["state"]:
                names = {r["name"] for r in results}
                rep["results"] = [r for r in old["results"] if r["name"] not in names] + results
                rep["partial"] = old.get("partial", False)
        except ValueError:
            pass
    dest.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    results = rep["results"]
    bad = [r for r in results if r["status"] != "pass" and not r["optional"]]
    print(f"\n{len(results)-len(bad)}/{len(results)} 合格 -> {dest}")
    for r in bad:
        print(f"--- {r['name']} ({r['status']}) 末尾:\n{r['tail']}")
    if not results:
        print("実行対象の検証が見つかりません(.claude/verify.json を作成してください)", file=sys.stderr)
    return 1 if bad or not results else 0


def handoff(root: Path, results, unresolved, nxt, out):
    branch = git(root, "branch", "--show-current") or "detached"
    rp = Path(results) if results else root / ".claude" / "handoff" / "last_verify.json"
    rep = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None
    status = git(root, "status", "--short")
    L = [f"# 引き継ぎ: {branch}", "", f"- 生成: {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}",
         f"- HEAD: {git(root,'log','-1','--format=%h %s')}",
         f"- ベースとの差分: {(git(root,'diff','--shortstat','origin/HEAD...HEAD') or git(root,'diff','--shortstat','origin/master...HEAD') or '(取得不可/差分なし)')}", "",
         "## 作業ツリー(未コミット)", "```", status or "(クリーン)", "```", "", "## 検証結果"]
    if rep:
        stale = "" if rep["head"] == git(root, "rev-parse", "--short", "HEAD") and rep.get("state") == fingerprint(root) else " ※検証後にHEAD/作業ツリー/verify.jsonが変わっている(再実行が必要)"
        L.append(f"実行 {rep['at']} / HEAD {rep['head']}{stale}")
        done = {r["name"] for r in rep["results"]}
        missing = [n for n in rep.get("all_checks", []) if n not in done]
        if rep.get("partial") or missing:
            L.append(f"⚠ 部分実行の結果。未実行: {', '.join(missing) or '(不明)'}")
        L += [f"- {'✅' if r['status']=='pass' else '❌'} `{r['name']}` {r['status']} {r['seconds']}s" for r in rep["results"]]
    else:
        L.append("未実行")
    auto = [f"[自動] 検証 `{r['name']}` が {r['status']}" for r in (rep or {}).get("results", [])
            if r["status"] != "pass" and not r.get("optional")]
    if rep is None:
        auto.append("[自動] 検証が未実行")
    else:
        if missing:
            auto.append(f"[自動] 未実行の検証: {', '.join(missing)}")
        if stale:
            auto.append("[自動] 検証後にHEAD/作業ツリー/verify.jsonが変わっている(再実行が必要)")
    L += ["", "## 未解決事項"] + ([f"- {u}" for u in auto + unresolved] or ["- なし"])
    L += ["", "## 次の作業"] + ([f"- {n}" for n in nxt] or ["- なし"])
    fname = branch.replace("/", "__") if branch != "detached" else "detached-" + (git(root, "rev-parse", "--short", "HEAD") or "nogit")
    dest = Path(out) if out else root / ".claude" / "handoff" / f"{fname}.md"
    ignore_handoff(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(dest)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sn = sp.add_parser("note"); sn.add_argument("kind", choices=INTERVENTION_KINDS); sn.add_argument("--text", default=""); sn.add_argument("--root")
    sh = sp.add_parser("history"); sh.add_argument("--root"); sh.add_argument("--by", choices=("day", "week", "all"), default="all")
    sh.add_argument("--since-days", type=int); sh.add_argument("--json", action="store_true")
    for n in ("detect", "run", "handoff"):
        s = sp.add_parser(n); s.add_argument("--root"); s.add_argument("--out")
        if n == "run":
            s.add_argument("--only", nargs="*"); s.add_argument("--timeout", type=int, default=900)
        if n == "handoff":
            s.add_argument("--results"); s.add_argument("--unresolved", action="append", default=[]); s.add_argument("--next", action="append", default=[])
    a = ap.parse_args(); root = repo_root(a.root)
    if a.cmd == "detect":
        c, src = detect(root); print(json.dumps({"source": src, "checks": c}, ensure_ascii=False, indent=1))
    elif a.cmd == "run":
        sys.exit(run(root, a.only, a.timeout, a.out))
    elif a.cmd == "note":
        append_history(root, {"type": "intervention", "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                              "kind": a.kind, "text": a.text, "head": git(root, "rev-parse", "--short", "HEAD"),
                              "branch": git(root, "branch", "--show-current") or "detached"})
        print(f"記録しました: {a.kind}")
    elif a.cmd == "history":
        summary = summarize(load_history(root), a.by, a.since_days)
        if a.json:
            print(json.dumps(summary, ensure_ascii=False, indent=1))
        else:
            print_summary(summary)
    else:
        handoff(root, a.results, a.unresolved, a.next, a.out)


if __name__ == "__main__":
    main()
