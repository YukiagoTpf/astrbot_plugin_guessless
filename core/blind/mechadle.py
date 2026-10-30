"""盲猜萝卜：猜一台高达机体，对比世界观、登场年份、阵营、机型、机械设计、型号开头。不单独建题库，用猜萝卜题库里
有 Gundam Wiki 资料的条目（entries.mecha_entry 的 attrs，格式改了换 entries.MECHA_VERSION）。"""

from .base import Blind, num, same

COLUMNS = ("世界观", "登场年份", "阵营", "机型", "机械设计", "型号开头")
CLUES = ("出自", "全高", "首字")


def row(guess: dict, answer: dict) -> list[list[str]]:
    """登场年份差 3 年以内算接近。"""
    year = num(guess["year"], answer["year"], str(guess["year"]), abs(guess["year"] - answer["year"]) <= 3)
    return [same(guess["era"], answer["era"]), year, *(same(guess[k], answer[k]) for k in ("faction", "kind", "designer", "prefix"))]


MODE = Blind(
    cat="mechadle", command="盲猜萝卜", desc="不给图，猜一台高达机体看资料对比", header="机体",
    columns=COLUMNS, clues=CLUES, row=row, base="mecha", peek=True,
)
