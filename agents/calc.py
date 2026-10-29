"""
agents/calc.py - The Calc agent.

Works out arithmetic, e.g. "6789 + 12344" -> 19133.

NO AI MODEL: language models can get sums wrong, so the answer is worked
out by plain Python. The expression is read with Python's own parser (ast)
and only numbers and + - * / // % ** and brackets are allowed. Anything
else (names, function calls...) is rejected, so nothing can be "run".

The answer is saved in state["last_answer"], so the Writer can put it
in a note (e.g. "write the answer in notepad").
"""

import ast
import operator
import re
import time

# The only operations allowed
OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}

# Words people type -> symbols (checked in this order)
WORDS = [
    (r"(\d[\d,]*(?:\.\d+)?)\s*(?:%|percent)\s*of\s*", r"\1/100*"),  # "18% of 2450" (else % = remainder)
    (r"(\d[\d,]*(?:\.\d+)?)\s*(?:%|percent)(?!\s*[\d(])", r"(\1/100)"),  # "18% * 2450": % with no number after
    (r"multiplied by|times|×", "*"), (r"divided by|÷", "/"), (r"plus", "+"), (r"minus", "-"),
    (r"(?<=\d)\s*x\s*(?=\d)", "*"),  # "12 x 4" (only between numbers)
    (r"\^", "**"),
]


def to_expression(task):
    """'calculate 6,789 plus 12344' -> '6789+12344' (only maths characters are kept)."""
    text = task.lower()
    for pattern, symbol in WORDS:
        text = re.sub(pattern, symbol, text)
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)       # 6,789 -> 6789
    return re.sub(r"[^0-9.+\-*/%() ]", "", text).strip()


def evaluate(node):
    """Work out a parsed expression, allowing ONLY numbers and the OPERATORS above."""
    if isinstance(node, ast.Expression):
        return evaluate(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPERATORS:
        return OPERATORS[type(node.op)](evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
        left, right = evaluate(node.left), evaluate(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1_000_000):
            raise ValueError("That power is too big to work out.")
        return OPERATORS[type(node.op)](left, right)
    raise ValueError("Only numbers and + - * / % ^ and brackets are allowed.")


def tidy(number):
    """19133.0 -> '19133', 0.1 + 0.2 -> '0.3' (no long float tails)."""
    if isinstance(number, float):
        if number.is_integer():
            return str(int(number))
        return f"{number:.10g}"
    return str(number)


def run_calc(task, state):
    """Run the Calc agent on one task, e.g. "6789 + 12344"."""
    start = time.time()
    expression = to_expression(task)
    if not expression or not re.search(r"\d", expression):
        raise ValueError(f"I couldn't find a calculation in '{task}'.")
    try:
        answer = tidy(evaluate(ast.parse(expression, mode="eval")))
    except ZeroDivisionError:
        raise ValueError("Can't divide by zero.")
    except (SyntaxError, TypeError, OverflowError):
        raise ValueError(f"I couldn't work out '{expression}'.")

    shown = expression.replace("**", "^").replace("/100*", "% of ")
    shown = re.sub(r"\(([\d.]+)/100\)", r"\1%", shown).replace("*", "×")  # (18/100) -> 18%
    state["last_answer"] = state["calc_answer"] = f"{shown} = {answer}"  # for the Writer

    return {
        "agent": "calc", "model": "none (plain Python)", "seconds": round(time.time() - start, 2),
        "fallback_used": False,
        "action": {"action": "calculate", "target": expression},
        "message": f"{shown} = {answer}",
        "answer": answer,
    }
