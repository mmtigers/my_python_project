#!/usr/bin/env python3
"""verify-handoff の補助スクリプト(標準ライブラリのみ)。

  vh.py detect  [--root DIR]            実行する検証コマンド一覧をJSONで表示(実行はしない)
  vh.py run     [--root DIR] [--only NAME ...] [--timeout SEC] [--out FILE]
                                        検証を順に実行し、結果をJSON+要約で記録
  vh.py handoff [--root DIR] [--results FILE] [--unresolved TXT ...] [--next TXT ...] [--out FILE]
                                        git状態+直近の検証結果から引き継ぎMarkdownを生成

プロジェクト固有の定義は <root>/.claude/verify.json が優先される:
  {"checks":[{"name":"lint","cwd":"sub","cmd":"ruff check .","env":{"K":"V"},"timeout":300,"optional":false}]}
無ければ package.json / pytest.ini / ruff.toml / Makefile から推定する(推定は保守的)。
"""
from __future__ import annotations

import argparse, json, os, shlex, subprocess, sys, time
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
                if n in scripts:
                    checks.append({"name": f"{rel}:{n}", "cwd": rel, "cmd": f"npm run {n}"})
        pyproj = (d / "pyproject.toml").read_text(errors="ignore") if (d / "pyproject.toml").exists() else ""
        cfgtxt = pyproj + "".join((d / f).read_text(errors="ignore") for f in ("tox.ini", "setup.cfg") if (d / f).exists())
        if (d / "ruff.toml").exists() or (d / ".ruff.toml").exists() or "[tool.ruff" in pyproj:
            checks.append({"name": f"{rel}:lint", "cmd": "ruff check .", "cwd": rel})
        if "[tool.mypy" in pyproj or (d / "mypy.ini").exists():
            checks.append({"name": f"{rel}:typecheck", "cmd": "mypy .", "cwd": rel})
        has_tests = (d / "tests").is_dir() and any((d / "tests").glob("test_*.py"))
        if (d / "pytest.ini").exists() or "pytest" in cfgtxt or has_tests:
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
            # 依存/コマンド未導入はコード不具合ではなく環境起因: 区別して報告(失敗扱いは維持)
            if status == "fail" and any(m in text for m in ("ModuleNotFoundError", "command not found", ": not found", "No such file or directory: 'npm'")):
                status = "env"
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
    L += ["", "## 未解決事項"] + ([f"- {u}" for u in unresolved] or ["- なし"])
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
    else:
        handoff(root, a.results, a.unresolved, a.next, a.out)


if __name__ == "__main__":
    main()
