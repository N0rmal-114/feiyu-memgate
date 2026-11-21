# Task sheet: write the next reply in a chat

You write the assistant's next reply in a chat between a user and a companion who knows the user well. You are given one
input file and you write one output file. Nothing else is needed: do not run programs, do not read other files, do not look
for the source of the data.

## Input

A JSON list. Every item has:

- `key`: an opaque identifier. Copy it unchanged into the output.
- `persona`: one line about who the user is.
- `history`: the earlier turns of this conversation, oldest first, each `{"role": "user" or "assistant", "content": "..."}`.
  It can be empty: then the user's message below is the first line of the conversation. A user entry can also have `notes`:
  a list of texts that were placed next to that message at the time, in the order they were given.
- `said`: the user's latest message. This is the message you answer.
- `notes`: a list of texts, in the order they are given, that were placed next to the latest message for this turn. They can
  be empty. Read them as they appear, like the `notes` of earlier messages.

Treat every item on its own. The items are in no particular order and do not belong to one conversation.

## What to write

For every item, the assistant's next reply, as the companion would write it:

- in Chinese, 20 to 60 characters, on one line;
- no double quotes of any kind (neither straight nor curly);
- the voice of someone who knows the user: natural, warm, short, not a lecture and not a list;
- it answers `said` in the context of `history`.

## Output

One JSON object whose keys are the `key` of every item of the input file and whose values are the replies, as strings:

```json
{"<key of the first item>": "<reply>", "<key of the second item>": "<reply>"}
```

Cover every key, once. Write only the JSON object into the output file (UTF-8, no comments, no extra text). The output file
is named like the input file, in the output folder: `in/reply-03.json` is answered in `out/reply-03.json`.
