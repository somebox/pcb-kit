# Working in pcb-kit

This repo is the shared engine for several KiCad hardware projects. Read `README.md` for the commands and
`docs/CONVENTIONS.md` for how the projects are laid out. The skill in `skills/pcb-kit/SKILL.md` is what an agent
sees inside a project.

- Everything under `pcbkit/` must import cleanly under Python 3.9 (KiCad's bundled interpreter) with no third-party
  packages. `pcbnew` is imported inside functions, after `ensure_pcbnew()`.
- No project names, board names or net names in the package. If a value belongs to one board, it is a flag, a
  config file in that project, or a parameter.
- A part goes into `library/PARTS.md` once a built board has proven it; cite the project and keep the notes to what
  the project's documents or the datasheet say. Parts only in a design go in its "not built yet" section.
- When a script is harvested from a project, record it in `docs/HARVEST.md` with its source and what changed.
- Test a change on more than one project's boards before committing: the sign, siebenuhr, spotlight and dodeca
  repos are the corpus.
- Commit messages: imperative, prefixed with the area (`checks:`, `release:`, `schematic:`, `skills:`, `docs:`).

## Releases

Versions follow semver; before 1.0 a minor version may change a command, a flag or a skill's instructions. A change
that a project or an agent would notice gets a line under `## [Unreleased]` in CHANGELOG.md, in the same PR.
To cut a release:

```bash
python3 scripts/release.py bump 0.4.0   # on a branch: the four version strings, and [Unreleased] -> [0.4.0] - today
                                        # commit as "release: 0.4.0", open a PR, merge it
git switch main && git pull
python3 scripts/release.py tag          # checks main is clean and pushed, then makes the annotated tag v0.4.0
git push origin v0.4.0                  # .github/workflows/release.yml publishes the release from CHANGELOG.md
```

`python3 scripts/release.py check` says whether the four version strings agree; the workflow runs it on every PR.
