from __future__ import annotations

from datetime import date

# 40 句强硬、命令式的短句，语气像在催自己干活；长度控制在 8-24 字。
QUOTE_POOL: tuple[tuple[str, str], ...] = (
    ("现在，立刻，马上。", "佚名"),
    ("少想，多做，别磨叽。", "佚名"),
    ("别等了，没人替你上场。", "佚名"),
    ("今天必须赢一次。", "佚名"),
    ("你要的结果，自己拿。", "佚名"),
    ("别喊累，先做完。", "佚名"),
    ("说做就做，不给自己退路。", "佚名"),
    ("你先动，运气才跟得上。", "佚名"),
    ("不解释，直接做出来。", "佚名"),
    ("今天的事，今天清干。", "佚名"),
    ("别把明天当垃圾桶。", "佚名"),
    ("想赢，就别怕难看。", "佚名"),
    ("情绪放一边，事情先办完。", "佚名"),
    ("没人催你，结果会催你。", "佚名"),
    ("你想要的生活，得自己去抢。", "佚名"),
    ("别装忙，做出结果。", "佚名"),
    ("软话说多了，骨头会软。", "佚名"),
    ("现在不做，等什么时候。", "佚名"),
    ("要么做完，要么别开始。", "佚名"),
    ("你今天偷的懒，明天加倍还。", "佚名"),
    ("别找理由，找个办法。", "佚名"),
    ("站直了，把事扛住。", "佚名"),
    ("你不是不行，你是不肯开始。", "佚名"),
    ("把嘴闭上，把事做了。", "佚名"),
    ("先赢自己，再谈其他。", "佚名"),
    ("别怕得罪人，怕没本事。", "佚名"),
    ("今天不狠，明天更难。", "佚名"),
    ("你不逼自己，生活就逼你。", "佚名"),
    ("收起委屈，拿出结果。", "佚名"),
    ("谁都可以退，你不能。", "佚名"),
    ("别等心情，直接开工。", "佚名"),
    ("做完这一件，再去难过。", "佚名"),
    ("你的时间，不许白给。", "佚名"),
    ("该你上场了，别躲。", "佚名"),
    ("别把「差不多」挂嘴边。", "佚名"),
    ("现在开始，晚一分钟都算输。", "佚名"),
    ("说话算数，先从对自己算起。", "佚名"),
    ("你要的不是安慰，是进展。", "佚名"),
    ("别指望被理解，先做出成绩。", "佚名"),
    ("今天，听自己的。", "佚名"),
)


def quote_for(day: date) -> tuple[str, str]:
    """Stable quote for a given day, so reopening the app shows the same line."""
    return QUOTE_POOL[day.toordinal() % len(QUOTE_POOL)]


def next_quote(current: str) -> tuple[str, str]:
    """Cycle to the next local quote; falls back to the day's quote."""
    texts = [item[0] for item in QUOTE_POOL]
    if current not in texts:
        return QUOTE_POOL[0]
    index = texts.index(current)
    return QUOTE_POOL[(index + 1) % len(QUOTE_POOL)]


def format_quote(text: str, author: str) -> str:
    """Plain-text form used for the clipboard."""
    body = text.strip().strip("“”\"'")
    if not body:
        return ""
    return f"“{body}” —— {author.strip() or '佚名'}"
