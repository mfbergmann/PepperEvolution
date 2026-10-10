"""
Is a recognised utterance clearly unfinished? (#26)

The streaming recogniser ends an utterance after 1 s of silence, and people pause longer than that mid-sentence
while they think. On the robot (2026-10-08) about one utterance in fifteen of a conversation ended mid-sentence
("...a local model onto the", "How do you feel about", "Can you", "So"), and Pepper answered the fragment
while the rest was lost under its reply (the microphone is muted while Pepper speaks).

``unfinished()`` is a word rule, not a model. It was checked on the 151 distinct utterances recorded on the robot
(results/system-one/bench_unfinished.py, local only):
- the rule caught 9 of the 11 cut-off sentences and wrongly held 1 of 140 complete ones before the fix below;
- Nimble, asked the same question, could not separate them (cut-off sentences scored 0.3-0.5, like odd but
  complete phrases).

It errs on the side of answering: a missed cut-off is what happened before; a false hold costs the person
``HOLD`` seconds of waiting. The recogniser's output has no final punctuation, so punctuation is no evidence.
"""

import re

HOLD = 2.5  # seconds to wait for the rest after an unfinished utterance (on top of the recogniser's 1 s). People
# resumed after about 2-2.5 s of silence on the robot, and the first word of the rest shows about 0.5 s after they do.
# With the real recogniser at real-time pace (2026-10-09): resuming after up to about 3 s of silence is joined, after
# 4 s it is not.
MAX_HOLD = 10.0  # while the person goes on speaking, wait for their next final at most this long

# words that cannot end an English sentence: articles, possessives, conjunctions, prepositions that need an object
_DANGLING = {
    "the",
    "a",
    "an",
    "my",
    "your",
    "our",
    "their",
    "his",
    "its",
    "and",
    "or",
    "but",
    "because",
    "cause",
    "than",
    "of",
    "onto",
    "into",
    "to",
    "with",
    "from",
    "for",
    "like",
}
_COPULA = {"is", "are", "was", "were", "be"}  # unfinished unless they close a clause: "where you are", "what it is"
_WH = {"what", "where", "who", "whom", "how", "why", "which", "when", "whatever", "wherever"}
_AUX = {"can", "could", "would", "will", "do", "does", "did", "should", "shall", "may", "might", "must"}
_PRONOUNS = {"you", "i", "we", "they", "he", "she", "it"}
_ALONE = {"so", "that", "to", "and", "but", "or", "because", "if", "when", "the", "um", "uh", "well"}
_REPORTING = {"happy", "glad", "sure", "think", "know", "said", "say", "hope", "believe", "guess", "feel", "afraid"}
_NEEDS_OBJECT = {"check", "focus", "depend", "depends", "based", "count", "rely"}  # not "working on" (a question)


def _words(text: str):
    return re.findall(r"[a-z']+", text.lower())


def unfinished(text: str) -> bool:
    """True if ``text`` clearly stops mid-sentence, so the rest is probably coming after a pause."""
    words = _words(text)
    if not words:
        return False
    sentences = [s for s in re.split(r"[.?!]\s+", text.strip()) if s.strip()]
    last = _words(sentences[-1]) if sentences else words
    if not last:
        return False
    end = last[-1]
    if len(words) == 1:
        return end in _ALONE
    if end in _DANGLING:
        return True
    if end in _COPULA:
        return not any(w in _WH for w in last[-5:-1])  # "where you are" is complete; "it is supposed to be" is not
    if len(last) >= 2 and last[-2] in _AUX and end in _PRONOUNS and last[0] not in _WH:
        return True  # "Can you", "Could we" (but not "how are you" or "what can you")
    if end == "about" and last[0] == "how":
        return True  # "How do you feel about", "how about"; "what are you talking about" is a whole question
    if end == "that" and len(last) >= 2 and last[-2] in _REPORTING:
        return True  # "I'm happy that", "I think that"
    if end == "on" and len(last) >= 2 and last[-2] in _NEEDS_OBJECT:
        return True  # "let's check on", "it depends on"
    return False
