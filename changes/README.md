# Changelog fragments

Add one file per change here instead of editing `CHANGELOG.md`:

    changes/<issue>.<kind>.md

`kind` is one of `added`, `changed`, `deprecated`, `removed`, `fixed`, `security`, `design`. The file
holds the changelog bullet(s), for example:

    - `lights-out batch`: many drills per scenario and temperature (#36).

Fragments are assembled into `CHANGELOG.md` at release (`scripts/build_changelog.py --release`).
Pull requests then never conflict over the changelog.
