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
