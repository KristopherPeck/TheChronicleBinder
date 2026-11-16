# Contributing

Contributions that improve Chronicle extraction, EPUB interoperability, accessibility, metadata, documentation, or maintainability are welcome.

## Development setup

Use Python 3.10 or newer from the repository root:

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
```

Keep changes focused and include regression coverage for parsing, metadata, cover layout, or packaging behavior when practical. Do not require Node, Docker, Calibre, or a live Halo Waypoint request to run the unit tests.

## Content and repository hygiene

- Do not commit generated EPUBs, downloaded Waypoint HTML, test-output folders, proprietary fonts, credentials, or personal library data.
- Do not add Microsoft or Halo logos as project branding.
- Use only the minimum third-party material necessary for documentation or regression fixtures.
- Keep the project free, noncommercial, and ad-free.
- Preserve the fan-project notice and the link to Microsoft's [Game Content Usage Rules](https://www.xbox.com/en-US/developers/rules).
- Keep the MIT-licensed project code clearly separated from third-party content.

## Pull requests

Describe the user-visible effect, identify the Chronicle URLs affected by parser changes, and note which readers were checked for EPUB changes. Do not include generated books in a pull request; screenshots or narrowly scoped markup excerpts are preferred when needed.

By contributing, you agree that your original contribution is provided under the project's MIT License. You must have the right to submit everything included in your contribution.
