"""Rich Claude Code prompt parsing, ported from the reference workflow-viz.

Handles permission prompts, plan approval, the folder-trust check and
AskUserQuestion: single or multi select, several questions as tabs, options with
preview boxes, wrapped labels and a Submit row. Pure functions over screen
lines, so they are easy to test.

Measured on Claude Code 2.1.28x.
"""
from __future__ import annotations

import re

OPTION = re.compile(r"^\s*(\u276f)?\s*(\d{1,2})\.\s+(.+?)\s*$")
CARET_ROW = re.compile(r"^\s*\u276f\s+(\S.*?)\s*$")
CHECKBOX = re.compile(r"^\[([ \u2714\u2713xX])\]\s*")
DIVIDER = re.compile(r"^\s*[\u2500\u2501\u254c]{6,}")
TABS = re.compile(r"([\u2610\u2611\u2612])\s+(.+?)(?=\s{2,}|\s*[\u2714\u2192]|$)")
BOX_CUT = re.compile(r"\s{3,}[\u250c\u2502\u2514].*$")
FOOTER = re.compile(r"(Enter to (select|confirm)|Esc to cancel)")


def _box_text(lines):
    """The inside of a preview box drawn beside the options, borders stripped."""
    out = []
    for l in lines:
        m = re.search(r"[\u2502](.*)[\u2502]\s*$", l)
        if m:
            out.append(m.group(1).rstrip())
    return "\n".join(out) if out else None


def parse_ask(lines):
    """A Claude Code choice on screen: permission prompts, plan approval, the folder-trust check and AskUserQuestion
    (single or multi select, several questions as tabs, options with preview boxes). Returns the options in on-screen
    order with their digit (None when the row has none), whether the caret is on them, tick state for multi select,
    and the lines above the question (the command, diff or plan being approved)."""
    lines = lines[-60:]
    last = next((i for i in range(len(lines) - 1, -1, -1) if OPTION.match(lines[i])), None)
    # numbered lines above the divider that opens the live panel are the agent's earlier reply ("2. Run it: done"), not choices
    foot0 = next((i for i in range(len(lines) - 1, -1, -1) if FOOTER.search(lines[i])), None)
    if last is not None and foot0 is not None:
        top = next((i for i in range(foot0 - 1, -1, -1) if DIVIDER.match(lines[i])), None)
        panel = range(top + 1, foot0) if top is not None else range(0)
        if top is not None and last < top and any(CARET_ROW.match(lines[i]) for i in panel) and not any(OPTION.match(lines[i]) for i in panel):
            last = None
    rows = []
    if last is not None:
        first, i = last, last - 1
        while i >= 0 and (OPTION.match(lines[i]) or DIVIDER.match(lines[i]) or (lines[i].startswith("    ") and lines[i].strip())):
            if OPTION.match(lines[i]):
                first = i
            i -= 1
        end = last
        while end + 1 < len(lines) and (DIVIDER.match(lines[end + 1]) or (lines[end + 1].startswith("  ") and not FOOTER.search(lines[end + 1]))):
            end += 1
        while end > last and DIVIDER.match(lines[end]):
            end -= 1
        rows = lines[first:end + 1]
    else:
        # unnumbered list with a caret, like the folder-trust check: rows share the caret row's text column
        foot = next((i for i in range(len(lines) - 1, -1, -1) if FOOTER.search(lines[i])), None)
        cr = next((i for i in range(foot - 1, max(-1, foot - 12), -1) if CARET_ROW.match(lines[i])), None) if foot else None
        if cr is None:
            return None
        col = len(lines[cr]) - len(CARET_ROW.match(lines[cr]).group(1))
        first = cr
        while first > 0 and len(lines[first - 1]) - len(lines[first - 1].lstrip()) == col:
            first -= 1
        end = cr
        while end + 1 < foot and len(lines[end + 1]) - len(lines[end + 1].lstrip()) == col:
            end += 1
        rows = lines[first:end + 1]
    width = next((len(lines[i]) for i in range(first - 1, -1, -1) if DIVIDER.match(lines[i])), 0)
    opts, submit, prev, box = [], None, "", []
    for raw in rows:
        if DIVIDER.match(raw):
            prev = ""
            continue
        if BOX_CUT.search(raw) or re.match(r"^\s{12,}[\u2502\u2514\u250c]", raw):
            box.append(raw)
        line = BOX_CUT.sub("", raw) if not re.match(r"^\s{12,}[\u2502\u2514\u250c]", raw) else ""
        m = OPTION.match(line)
        caret = "\u276f" in line
        text = line.replace("\u276f", " ").strip()
        if m:
            opts.append({"n": m.group(2), "label": m.group(3), "selected": bool(m.group(1)), "description": ""})
        elif text == "Submit":
            submit = {"pos": len(opts), "selected": caret}
        elif text == "Chat about this" or (last is None and text):
            opts.append({"n": None, "label": text, "selected": caret, "description": ""})
        elif text and opts and not text.startswith("Notes: press"):
            o = opts[-1]
            word = text.split()[0]
            wrapped = len(prev) + 1 + len(word) > width - 1 if width else text[:1].islower()
            if wrapped and not o["description"]:
                o["label"] += ("" if width and len(prev) >= width - 1 else " ") + text  # a full line was cut mid-word
            else:
                o["description"] = (o["description"] + " " + text).strip()
        prev = raw if not BOX_CUT.search(raw) else ""
    numbered = [o["n"] for o in opts if o["n"]]
    if len(opts) < 2 or not (any(o["selected"] for o in opts) or (submit and submit["selected"])):
        return None  # a numbered list in normal output has no selection caret
    if numbered and numbered != [str(k) for k in range(1, len(numbered) + 1)]:
        return None
    multi = False
    for o in opts:
        cb = CHECKBOX.match(o["label"])
        if cb:
            multi = True
            o["checked"] = cb.group(1) != " "
            o["label"] = o["label"][cb.end():]
    before = lines[max(0, first - 40):first]
    footer = " ".join(lines[end + 1:end + 4]) if last is not None else ""
    widget = bool(submit) or any(o["label"] == "Chat about this" for o in opts) or "Enter to select" in footer
    for o in opts:
        low = o["label"].lower().rstrip(".")
        o["kind"] = ("chat" if low == "chat about this" else
                     "text" if low in ("type something", "other") or "feedback" in o["description"].lower() else "choice")
    qi = next((i for i in range(len(before) - 1, -1, -1) if before[i].strip(" \u2502").endswith("?")), None)
    if qi is None:
        qi = next((i for i in range(len(before) - 1, -1, -1) if "?" in before[i]), None)
    q = before[qi].strip(" \u2502") if qi is not None else None
    if q and not q.endswith("?"):  # a question mid-paragraph (the trust check): keep the whole wrapped sentence
        rest = [l.strip() for l in before[qi + 1:] if l.strip() and not l.startswith("  ") and len(l) - len(l.lstrip()) <= 1]
        q = " ".join([q] + rest[:3])[:400]
    tabs_line = next((l for l in reversed(before) if re.search(r"[\u2610\u2611\u2612]", l)), None)
    tabs = [{"label": t[1].strip(), "done": t[0] != "\u2610"} for t in TABS.findall(tabs_line)] if tabs_line else []
    header = next((t["label"] for t in tabs if not t["done"]), None)  # all ticked: this is the review screen
    # what is being approved: the lines between the prompt's top rule and its question
    stop, ctx = qi if qi is not None else len(before), []
    while not ctx and stop > 0:  # the plan prompt puts a second rule right above its question: step back past it
        top = next((i for i in range(stop - 1, -1, -1) if DIVIDER.match(before[i]) and "\u2500" in before[i]), None)
        if top is None:
            break
        ctx = [l.strip() for l in before[top + 1:stop] if l.strip() and not DIVIDER.match(l) and l is not tabs_line and not l.strip().startswith("Tip:")]
        stop = top
    sel = next((o for o in opts if o["selected"]), None)
    if box and sel:
        sel["preview"] = _box_text(box)
    return {"question": q, "header": header, "options": opts, "multi": multi, "submit": submit, "tabs": tabs, "boxed": bool(box),
            "context": "\n".join(ctx[-30:]) or None}




def plan_answer(ask, pick=None, picks=None, text=None):
    """Keystrokes that give the on-screen prompt this answer. Steps are ("keys", [...]), ("text", s) or ("wait", s).
    Digits pick (single) or tick (multi) without moving the caret; rows with no digit, the typed-answer row and the
    Submit row are reached by moving the caret."""
    opts = ask["options"]
    order = list(range(len(opts)))
    if ask.get("submit"):
        order.insert(ask["submit"]["pos"], "submit")
    cur = next((order.index(i) for i, o in enumerate(opts) if o["selected"]), 0)
    if ask.get("submit") and ask["submit"]["selected"]:
        cur = order.index("submit")
    steps = []

    def move(target):
        nonlocal cur
        to = order.index(target)
        if to != cur:
            steps.append(("keys", ["down" if to > cur else "up"] * abs(to - cur)))
        cur = to

    if not ask.get("multi"):
        o = opts[pick]
        if o["n"] and o["kind"] != "text":
            # beside a preview box the digit only moves the caret, so Enter confirms
            return [("keys", [o["n"]])] + ([("keys", ["enter"])] if ask.get("boxed") else [])
        if o["n"]:
            steps.append(("keys", [o["n"]]))  # on a typed-answer row the digit only moves the caret there
            cur = order.index(pick)
        else:
            move(pick)
        if o["kind"] == "text" and text:
            steps += [("wait", 0.35), ("text", text)]
        steps.append(("keys", ["enter"]))
        return steps
    want = set(picks or [])
    text_i = next((i for i, o in enumerate(opts) if o["kind"] == "text"), None)
    if text_i is not None and cur == order.index(text_i):
        move(0)  # a digit typed while the caret sits in the text row would become text
    for i, o in enumerate(opts):
        if o["kind"] == "choice" and o["n"] and (i in want) != bool(o.get("checked")):
            steps.append(("keys", [o["n"]]))
    if text_i is not None:
        o = opts[text_i]
        if text:
            if not o.get("checked"):
                steps.append(("keys", [o["n"]]))
            move(text_i)
            steps += [("wait", 0.2), ("text", text)]
        elif o.get("checked"):
            steps.append(("keys", [o["n"]]))
    if ask.get("submit"):
        move("submit")
    steps.append(("keys", ["enter"]))
    return steps
