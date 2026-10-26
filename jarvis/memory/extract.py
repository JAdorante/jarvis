"""Pull an explicit personal fact out of one spoken sentence."""

import re


UNCERTAIN = re.compile(
    r"\b(maybe|perhaps|i think|i guess|just kidding|kidding|joke|joking)\b",
    re.I,
)


def extract_fact(text):
    """Return a fact string, or empty when the sentence is not a fact to store.

    Uncertain or joking lines return None so the caller can refuse them.
    A sentence that is not about the user returns an empty string.
    """
    lowered = text.lower().strip()
    if UNCERTAIN.search(lowered) and re.search(r"\b(remember|my name|i am|i like|i love|i prefer|i hate)\b", lowered):
        return None
    name = re.search(r"\bmy name is ([a-z][a-z' -]{0,40})", lowered)
    remembered = re.split(r"\bremember that\b", text, maxsplit=1, flags=re.I)
    like = re.search(r"\bi (like|love|prefer|hate) (.+)", lowered)
    usual = re.search(r"\bi usually ([a-z0-9' ,-]{3,80})", lowered)
    if name:
        fact = "The user's name is " + name.group(1).strip().title() + "."
    elif len(remembered) == 2 and remembered[1].strip():
        fact = remembered[1].strip(" .")
        fact = fact[0].upper() + fact[1:] + "."
    elif like:
        fact = "The user " + like.group(1) + "s " + like.group(2).strip(" .") + "."
        fact = fact[0].upper() + fact[1:]
    elif usual:
        fact = "The user usually " + usual.group(1).strip(" .") + "."
    else:
        return ""
    return re.sub(r"\s+", " ", fact)[:240]
