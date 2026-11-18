# A placement checklist for a release that has none

A release made before pcbkit wrote the checklist has none, and `pcbkit release` never rewrites a release. Make one
beside it, leaving the release alone:

- Find the commit that added the release: `git log --diff-filter=A --format=%h -- <release dir>/MANIFEST.txt`.
- Extract the board's folder as it was then: `git archive <commit> <board folder> | tar -x -C <scratch>`.
- Run `pcbkit fab` on that copy into a scratch folder, passing the project's `placed` setting as `--placed`.
- If its CPL for the release's sides is the same file as the release's CPL, its checklist holds for the release.
- If they differ, the rotation table has changed since the release, and the release's CPL has the old angles.
  Tell the user: `pcbkit release` would make a new version with the new angles and its own checklist. Until
  then, compare the viewer's hover readings with the pad positions in KiCad at that version.
