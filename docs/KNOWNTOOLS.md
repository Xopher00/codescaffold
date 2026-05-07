To extend Codescaffold beyond `rope‑mcp-server` and LibCST, you can draw on a rich ecosystem of Python packages.  These tools fall into several categories depending on whether you need to build AST‑based codemods, explore import graphs, or clean up and modernize code.

### AST manipulation and codemod frameworks

* **Bowler** is a refactoring tool built on LibCST that provides a fluent API for pattern‑matching, modifying and rewriting Python code.  It ensures transformations preserve compilation and is designed for safe, large‑scale modifications.

* **Refactor** offers a light‑weight AST‑based toolkit for building simple codemods.  You define transformation rules as classes that match nodes and replace them with new nodes, and the toolkit applies these across a codebase.

* **RedBaron** builds on the full‑syntax‑tree “Baron” library.  It lets you write custom refactoring tools while preserving formatting; changes are limited to the locations you specify.

* **Codemod** (the Facebook codemod library) helps with large‑scale refactors that need human oversight.  It provides a library and CLI to automate repetitive changes across a codebase.

* **Codemodder** is a pluggable framework for building codemods.  It can be run as a CLI or imported as a library; you can list available codemods, perform dry runs, or call a `run()` function to apply transformations programmatically.

* **Refine** wraps LibCST’s codemod module and allows you to chain multiple codemods in a single run.  It manages codemod priorities and runs them in a single AST pass, which improves performance on large projects.

* **ast_tools** provides decorators for applying “passes” to functions or classes.  Each pass receives an AST and environment and returns a possibly modified AST; `apply_passes` serializes and executes the rewritten AST.

* **astToolkit** supplies a type‑safe, composable toolkit for manipulating Python ASTs.  It is useful when you need to analyze, transform or generate code programmatically and offers a layered architecture with visitor patterns, predicate/action builders and ready‑made tools for extracting functions or removing unused parameters.

* **google‑pasta** (pasta) is a small AST‑based refactoring library from Google designed for rewriting Python code.

* **asttokens** annotates AST nodes with the positions of the source tokens that generated them, making it easier to map AST nodes back to original code for refactoring or highlighting.

### Import‑graph and architecture analysis

* **Grimp** builds a queryable import graph for a package.  You can ask for children or descendants of a module and inspect upstream relationships.  Codescaffold already uses it to validate plans.

* **Import‑linter** enforces architectural rules by restricting which modules may import others.  It can also generate an interactive UI for exploring a package’s architecture.

* **Impulse** visualizes your project’s import graph; the `drawgraph` command displays dependency graphs and highlights import cycles.

* **Pyan3** generates offline call graphs and import dependency graphs through static analysis.  The revived 2026 release can build a module‑level import graph, detect cycles and limit graph depth; it supports modern syntax such as pattern matching and `async` with statements.

* **import‑graph‑python** is a lightweight tool that maps relative imports in your project, useful for quick visualizations.

### Cleanup and code modernisation

* **Unimport** is a linter/formatter that detects and removes unused imports.  It scans your code for imports that are no longer used and can be run in a pre‑commit hook.

* **Pycln** similarly removes unused imports.  You can point it at a directory and it will eliminate unused imports; it runs as a command‑line tool or via `python -m pycln`.

* **Pyupgrade** automatically upgrades your syntax to newer Python features (for example replacing `dict([...])` with comprehensions or removing unnecessary unicode prefixes).  It’s often used as a pre‑commit hook.

* **Ruff** is a very fast linter and code formatter written in Rust.  It can replace Flake8, Black, isort and pyupgrade; it provides caching, supports `pyproject.toml`, removes unused imports and contains more than 800 built‑in rules.

### Static analysis and IDE integrations

* **Rope** remains the canonical Python refactoring library.  It supports most Python syntax up to Python 3.10, is light on dependencies and focuses on safe refactoring operations like renaming symbols, extracting methods and moving code.

* **Jedi** offers static analysis, autocompletion and code search and includes some refactoring and reference‑finding capabilities.

* **Parso** is a parser that produces error‑recovering ASTs; it can return multiple syntax errors and is used by Jedi.

* **Astroid** provides an abstract syntax tree with inference and local scope information, forming the basis of pylint.

These packages complement the existing tools in Codescaffold.  AST manipulation frameworks (Bowler, refactor, astToolkit) help you build codemods or implement high‑level operations.  Import‑graph tools (Grimp, import‑linter, Pyan3) support the evidence and architecture‑linter side of your planning.  Cleanup tools (Unimport, Pycln, Pyupgrade, Ruff) ensure that final code is clean and modern.  Static‑analysis libraries (Rope, Jedi, Parso, Astroid) can supply additional context for decision‑making.

It’s usually better to **pick a small, complementary set of tools** rather than pulling in every library you come across.  Research on dependency bloat shows that obsolete or unnecessary packages increase maintenance cost and expand your project’s attack surface.  Articles on modern development echo this sentiment, arguing that developers should challenge their dependencies and avoid bloated frameworks.  For a refactoring and architecture‑governance project like yours, choose one solid framework for AST and codemods, one tool for import‑graph analysis and architecture linting, and one well‑maintained linter/formatter.  This keeps the toolchain focused, easier to test, and less prone to version conflicts or security issues.
