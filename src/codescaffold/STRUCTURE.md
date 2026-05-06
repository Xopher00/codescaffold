codescaffold.graphify
  Owns Graphify integration.
  Loads/regenerates graph.json.
  Understands Graphify node/edge concepts.
  Produces curated refactor evidence, not raw graph dumping.

codescaffold.plans
  Owns plan schema, lifecycle, graph hash, repo commit hash, approval state.
  Plan is the single source of truth.

codescaffold.contracts
  Owns refactor contracts and import-linter contracts.
  Separates provisional sandbox contracts from durable architecture contracts.

codescaffold.operations
  Owns typed mechanical operations:
  move_file, move_symbol, rename_symbol, rewrite_imports.
  No operation executes directly unless attached to a plan.

codescaffold.sandbox
  Owns git worktree creation, branch naming, dirty-tree policy, cleanup.

codescaffold.validation
  Owns compile/import/test/import-linter/MCP-startup validation.

codescaffold.audit
  Owns evidence, diff classification, graph delta, validation result records.

codescaffold.mcp
  Thin tool layer only.
  Converts MCP calls into plan/workflow operations.
