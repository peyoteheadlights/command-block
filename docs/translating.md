# Translating the apps

Every word the desktop app and the phone apps show lives in one table,
[`shared/strings.json`](../shared/strings.json). Each entry has two
versions: **Simple** (everyday words, the default) and **Technical** (the
precise terms). English is the table itself.

## Adding a language

Choosing a language is not built yet, and the apps have no way to load a
second language file. When it is, a translation will be a file with the same
keys as the table:

```json
{
  "action.start": ["Starten", "Starten"],
  "head.players.one": ["{count} Spieler online", "{count} Spieler online"]
}
```

The rules for such a file are already fixed:

1. Use the same keys as English and translate both versions.
2. Keep every `{placeholder}` exactly as it is in English.
3. A file can hold only some of the entries. Any key it leaves out would be
   shown in English, so a half-done translation stays usable.

## Checks

`tests/test_strings.py` reads `shared/strings.json` and fails if an entry is
not a `[Simple, Technical]` pair, or if the two versions have different
placeholders.
