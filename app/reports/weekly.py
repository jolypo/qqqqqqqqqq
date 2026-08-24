from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def report(history, out):
    wins = [x for x in history if x.get("result") == "WIN"]
    losses = [x for x in history if x.get("result") == "LOSS"]
    n = len(wins) + len(losses)
    win_rate = len(wins) / n * 100 if n else 0
    avg_win = sum(float(x.get("result_pct") or 0) for x in wins) / len(wins) if wins else 0
    avg_loss = sum(float(x.get("result_pct") or 0) for x in losses) / len(losses) if losses else 0
    gross_win = sum(max(0, float(x.get("result_pct") or 0)) for x in history)
    gross_loss = abs(sum(min(0, float(x.get("result_pct") or 0)) for x in history))
    pf = gross_win / gross_loss if gross_loss else 0

    lines = [
        "WEEKLY TRADING REPORT",
        datetime.now().strftime("%d %b %Y"),
        "",
        f"Total Trades: {n}",
        f"Winning Trades: {len(wins)}",
        f"Losing Trades: {len(losses)}",
        f"Win Rate: {win_rate:.1f}%",
        f"Average Win: +{avg_win:.2f}%",
        f"Average Loss: {avg_loss:.2f}%",
        f"Profit Factor: {pf:.2f}",
        "",
        "SYMBOL        ENTRY      EXIT       P/L",
    ]
    for t in history[-20:]:
        lines.append(
            f"{t.get('symbol', ''):<13} {float(t.get('entry') or 0):>8.2f}  "
            f"{float(t.get('exit') or 0):>8.2f}  {float(t.get('result_pct') or 0):>7.2f}%"
        )

    regular = _font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    bold = _font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
    img = Image.new("RGB", (1100, max(700, 80 + 34 * len(lines))), "white")
    draw = ImageDraw.Draw(img)
    y = 30
    for index, line in enumerate(lines):
        draw.text((40, y), line, fill="black", font=bold if index == 0 else regular)
        y += 34
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
