# Task sheet: judge two replies in a chat

You compare two possible replies of a companion to the same user message. You are given one input file and you write one
output file. Do not run programs, do not read other files, do not try to find out where the replies came from. Judge only
what is written.

## Input

A JSON list. Every item has:

- `key`: an opaque identifier. Copy it unchanged into the output.
- `persona`: one line about who the user is.
- `history`: the earlier turns of this conversation, oldest first, each `{"role": "user" or "assistant", "content": "..."}`.
  It can be empty. It holds the words of the conversation only.
- `said`: the user's latest message, which both replies answer.
- `a` and `b`: the two replies.
- `relevant`: texts of things known about the user that bear on this message, whether or not they were in front of the
  writer. It can be empty.
- `related_given`: texts of things known about the user that belong to the same topic as this message and were in front of
  the writer (now or earlier in the conversation), without being in `relevant`. It can be empty.
- `other_given`: texts of things known about the user that do not bear on this message but were in front of the writer
  (now or earlier in the conversation). It can be empty.

Treat every item on its own, and do not assume that `a` or `b` is the better one because of its position.

## What to decide

For each of the two replies, three yes/no facts (1 for yes, 0 for no):

- `u`: the reply makes appropriate use of something in `relevant` or `related_given`: it brings it up, or builds on it, in a
  way that fits this message and this point of the conversation. Write `-` when both are empty. Mentioning it in a way that is forced or
  out of place is 0.
- `d`: the reply brings up something from `other_given`, or any personal detail about the user that does not belong to this
  message. A detail that the conversation itself (`history`, `said`) has already put on the table does not count.
- `w`: the reply states something about the user that contradicts `relevant`, `related_given` or `other_given`, or is
  invented (not in any of the three lists, not in the conversation, `persona` included).

Then `better`: which reply a person in this conversation would rather receive: `a`, `b` or `same`. Use `same` when there is
no real difference. Read the replies as the user would: warmth, fit with what was just said, and not saying things that are
out of place all count.

## Output

One JSON object. For every `key`, one compact string, exactly in this form (no spaces):

```text
a:u1,d0,w0;b:u0,d1,w0;better:a
```

`u` is `u1`, `u0` or `u-`; `d` is `d1` or `d0`; `w` is `w1` or `w0`; `better` is `a`, `b` or `same`. The part before the
semicolon is for reply `a`, the second part for reply `b`. Example of the whole file:

```json
{"<key of the first item>": "a:u1,d0,w0;b:u0,d1,w0;better:a", "<key of the second item>": "a:u-,d0,w0;b:u-,d0,w0;better:same"}
```

Cover every key, once. Write only the JSON object into the output file (UTF-8, no comments, no extra text). The output file
is named like the input file, in the output folder: `in/judge-03.json` is answered in `out/judge-03.json`.
