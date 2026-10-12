"""Show everything behind one answer of the app: python view.py <id>

The app (harness.py) prints an id under every answer ("id a26bce") and saves what happened
as traces/<id>.json: the question, the reply, the source chain and its vote, and every model
call in order (prompt + output). This script turns that file into a page and opens it in the
browser. Without an id it lists the most recent answers.
"""

import html
import json
import sys
import webbrowser
from pathlib import Path

TRACE_DIR = Path(__file__).parent / "traces"

STYLE = """
:root { --bg: #ffffff; --fg: #1d1d1f; --dim: #6e6e73; --box: #f5f5f7; --line: #d2d2d7;
        --ok: #1a7f37; --bad: #c62828; --accent: #0b62d6; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161618; --fg: #ececf0; --dim: #9a9aa2; --box: #222226; --line: #3a3a40;
          --ok: #4cc26a; --bad: #ef6b6b; --accent: #6aa8ff; }
}
body { background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, sans-serif;
       max-width: 1000px; margin: 0 auto; padding: 16px; }
h1 { font-size: 20px; margin: 8px 0; } h2 { font-size: 17px; margin: 28px 0 8px; }
.box { background: var(--box); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px;
       margin: 8px 0; overflow-wrap: anywhere; }
.dim { color: var(--dim); } .ok { color: var(--ok); } .bad { color: var(--bad); }
.kind { display: inline-block; min-width: 70px; font-weight: 600; color: var(--accent); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.45 ui-monospace, Consolas, monospace;
      margin: 6px 0; }
summary { cursor: pointer; color: var(--dim); }
table { border-collapse: collapse; } td { padding: 2px 12px 2px 0; vertical-align: top; }
.answer { font-size: 17px; font-weight: 600; }
"""


def esc(value):
    return html.escape(str(value))


def details(label, text):
    """A collapsed block with the full text inside."""
    return f"<details><summary>{esc(label)}</summary><pre>{esc(text)}</pre></details>"


def summary_section(t):
    if t.get("reply") is not None:
        answer = f'<span class="answer">{esc(t["reply"])}</span>'
    elif t.get("unchecked"):
        answer = (f'<span class="answer">{esc(t["unchecked"])}</span> '
                  f'<span class="bad">unchecked - every attempt failed (last: {esc(t["unchecked_reason"])})</span>')
    elif t.get("error"):
        answer = f'<span class="bad">look-up failed: {esc(t["error"])}</span>'
    else:
        answer = '<span class="bad">gave up</span>'
    route = ("looked up, answered from: " + esc(t.get("answered_from"))) if t.get("looked_up") else (
        "no look-up (best strategy answered)" if t.get("search_on") else "search off (best strategy answered)")
    rows = [("Question", esc(t["prompt"])), ("Answer", answer), ("Route", route),
            ("Attempts", esc(t.get("attempts", "-"))), ("Time", esc(t["time"])),
            ("Model calls", esc(len(t.get("calls") or [])))]
    body = "".join(f"<tr><td class='dim'>{k}</td><td>{v}</td></tr>" for k, v in rows)
    return f"<div class='box'><table>{body}</table></div>"


def chain_section(chain):
    """The source chain, one box per step: what each source returned and what came of it."""
    if not chain:
        return ""
    parts = ["<h2>Source chain</h2>"]
    for step in chain:
        lines = [f"<b>{esc(step.get('source'))}</b>"]
        if step.get("url"):
            lines.append(f" <span class='dim'>{esc(step['url'])}</span>")
        if step.get("query"):
            lines.append(f"<br>query: {esc(step['query'])}")
        if step.get("phrases"):
            lines.append(f"<br>search phrases: {esc(' | '.join(step['phrases']))}")
        if step.get("need"):
            lines.append(f"<br>what has to be found: <b>{esc(step['need'])}</b>")
        if step.get("titles"):
            lines.append(f"<br>titles: {esc(' | '.join(step['titles']))}<br>picked: <b>{esc(step.get('picked'))}</b>")
        if "public" in step:
            lines.append(f"<br>public sources can know this: {step['public']:.2f}")
        if step.get("text"):
            lines.append(details(f"text ({len(step['text'])} characters)", step["text"]))
        if "evidence" in step:
            evidence = step["evidence"]
            lines.append(f"evidence: <b>{esc(evidence)}</b>" if evidence else "evidence: <span class='dim'>NONE</span>")
        if step.get("votes"):
            votes = "".join(f"<tr><td class='dim'>{esc(v)}</td><td>{esc(r)}</td></tr>" for v, r in step["votes"])
            groups = "".join(f"<li>{len(g)} vote(s): {esc(' / '.join(g))}</li>" for g in step["groups"])
            lines.append(f"<table>{votes}</table>groups (same claim, judged by the model):<ul>{groups}</ul>")
        if step.get("reply"):
            lines.append(f"<br>reply: <b>{esc(step['reply'])}</b>")
        if step.get("guess"):
            lines.append(f"<br>the model's own guess (shown as unverified): {esc(step['guess'])}")
        if step.get("used"):
            lines.append(" <span class='ok'>&larr; used</span>")
        parts.append(f"<div class='box'>{''.join(lines)}</div>")
    return "".join(parts)


def calls_section(calls):
    """Every model call in order: what the prompt asked (its first line), the output, the full prompt."""
    if not calls:
        return ""
    parts = ["<h2>Model calls, in order</h2>"]
    for i, call in enumerate(calls, 1):
        kind, prompt, out = call["kind"], call["prompt"], call["output"]
        if kind == "check":
            verdict = "<span class='ok'>accepted</span>" if out["ok"] else f"<span class='bad'>rejected: {esc(out['reason'])}</span>"
            parts.append(f"<div class='box'><span class='kind'>{i}. check</span> "
                         f"reply <b>{esc(out['reply'])}</b> &rarr; {verdict}</div>")
            continue
        if kind == "route":  # which of the strategy's routes answers (strat2.py records it)
            parts.append(f"<div class='box'><span class='kind'>{i}. route</span> <b>{esc(out['route'])}</b> "
                         f"<span class='dim'>(long-text router {out['long_score']:.2f})</span></div>")
            continue
        first_line = next((line for line in prompt.splitlines() if line.strip()), "")
        if kind == "yes/no":
            shown = ", ".join(f"{esc(t.strip() or repr(t))} {p:.2f}" for t, p in out.items())
        else:
            shown = f"<b>{esc(out['text'])}</b> <span class='dim'>({out['tokens']} tokens, stopped by " \
                    f"{esc(out['stopped_by'])}, temperature {out['temperature']})</span>"
        parts.append(f"<div class='box'><span class='kind'>{i}. {esc(kind)}</span> "
                     f"<span class='dim'>{esc(first_line[:140])}</span><br>&rarr; {shown}"
                     f"{details('full prompt', prompt)}</div>")
    return "".join(parts)


def page(t):
    memory = ""
    if t.get("memory"):
        memory = "<h2>Memory at the time</h2>" + details(
            f"{len(t['memory'])} exchanges", "\n".join(f"User: {e['user']}\nAI: {e['ai']}" for e in t["memory"]))
    sources = ""
    if t.get("sources"):
        sources = "<h2>Sources the answer was written from</h2>" + "".join(
            f"<div class='box'><span class='dim'>{esc(s['url'])}</span><br>{esc(s['text'])}</div>" for s in t["sources"])
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width, initial-scale=1'><title>Answer {esc(t['id'])}</title>"
            f"<style>{STYLE}</style></head><body><h1>Answer {esc(t['id'])}</h1>"
            f"{summary_section(t)}{sources}{chain_section(t.get('chain'))}{memory}"
            f"{calls_section(t.get('calls'))}</body></html>")


def list_recent(count=15):
    traces = sorted(TRACE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:count]
    if not traces:
        print("No traces yet: chat in the app (python harness.py); every answer gets an id.")
    for path in traces:
        t = json.loads(path.read_text(encoding="utf-8"))
        reply = t.get("reply") or t.get("unchecked") or t.get("error") or "<gave up>"
        print(f"{t['id']}  {t['time']}  {t['prompt'][:45]!r:48} -> {reply[:40]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("Usage: python view.py <id>   (the id printed under each answer). Recent answers:")
        list_recent()
        sys.exit()
    path = TRACE_DIR / f"{sys.argv[1]}.json"
    if not path.exists():
        sys.exit(f"No trace {sys.argv[1]!r} in {TRACE_DIR}. Run without an id to list recent ones.")
    out = path.with_suffix(".html")
    out.write_text(page(json.loads(path.read_text(encoding="utf-8"))), encoding="utf-8")
    print(f"Opening {out}")
    webbrowser.open(out.as_uri())
