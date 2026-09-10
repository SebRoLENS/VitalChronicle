from pathlib import Path

path = Path("scripts/_tmp_patch_agent_followup.py")
text = path.read_text(encoding="utf-8")
old = 'return "\\n".join('
new = 'return "\\\\n".join('
count = text.count(old)
if count != 2:
    raise SystemExit(f"expected 2 newline join markers, found {count}")
path.write_text(text.replace(old, new), encoding="utf-8")
