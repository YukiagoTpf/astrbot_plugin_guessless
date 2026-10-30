"""盲猜宝可梦：猜一只，对比世代、属性、颜色、进化阶段、种族值。不单独建题库，用猜宝可梦题库里的条目
（entries.pokemon_entry 的 attrs，格式改了换 entries.POKEMON_VERSION）。"""

from .base import Blind, num

COLUMNS = ("世代", "属性1", "属性2", "颜色", "进化阶段", "种族值")
CLUES = ("分类", "进化", "首字")


def row(guess: dict, answer: dict) -> list[list[str]]:
    """属性：答案有、但不在这一格的标黄。世代差一代、种族值差 40 以内算接近。"""

    def typ(i):
        mine = guess["types"][i] if i < len(guess["types"]) else "无"
        theirs = answer["types"][i] if i < len(answer["types"]) else "无"
        if mine == theirs:
            return [mine, "hit", ""]
        return [mine, "near" if mine in answer["types"] else "miss", ""]

    return [
        num(guess["gen"], answer["gen"], f"第{guess['gen']}代", abs(guess["gen"] - answer["gen"]) == 1),
        typ(0),
        typ(1),
        [guess["color"], "hit" if guess["color"] == answer["color"] else "miss", ""],
        num(guess["stage"], answer["stage"], f"{guess['stage']} 阶"),
        num(guess["bst"], answer["bst"], str(guess["bst"]), abs(guess["bst"] - answer["bst"]) <= 40),
    ]


MODE = Blind(
    cat="pokedle", command="盲猜宝可梦", desc="不给图，猜一只看属性对比", header="宝可梦",
    columns=COLUMNS, clues=CLUES, row=row, base="pokemon",
)
